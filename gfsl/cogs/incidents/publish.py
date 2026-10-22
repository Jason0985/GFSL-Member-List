"""Entscheidung eintragen: Entscheidung veröffentlichen (mit Links + Dateien), Verlauf ins Archiv kopieren, Ticket löschen."""
import asyncio
import io
import logging
import os
import re
from zoneinfo import ZoneInfo

import discord

from gfsl import storage

from . import data as D
from .ticket import add_log, lock, now, parties

log = logging.getLogger("gfsl.incidents")
URL_RE = re.compile(r"https?://[^\s<>()]+")
TZ = ZoneInfo(os.environ.get("BOT_TIMEZONE", "Europe/Berlin"))  # Uhrzeiten im Protokoll in deutscher Zeit


async def collect(channel: discord.TextChannel, t: dict) -> tuple[list[discord.Message], list[tuple[str, str]], list[discord.Attachment]]:
    """Alle Nachrichten (alt -> neu) sowie Links und Dateien der Beteiligten: aus ihren Formularen und was sie selbst gepostet haben."""
    messages = [m async for m in channel.history(limit=None, oldest_first=True)]
    involved = set(parties(t))
    entry_owner = {e["message_id"]: uid for uid, e in t["entries"].items()}
    links = [(uid, url) for uid, e in t["entries"].items() for url in e["links"]]
    files = []
    for m in messages:
        if m.id in entry_owner:
            files += m.attachments
        elif str(m.author.id) in involved:
            links += [(str(m.author.id), url.rstrip(".,!?")) for url in URL_RE.findall(m.content)]
            files += m.attachments
    return messages, links, files


async def download(attachments: list[discord.Attachment], limit: int) -> tuple[list[discord.File], list[discord.Attachment]]:
    """Lädt Dateien herunter, die der Bot wieder hochladen darf. Gibt (Dateien, zu große) zurück."""
    ok, too_big = [], []
    for a in attachments:
        if a.size > limit:
            too_big.append(a)
            continue
        try:
            ok.append(await a.to_file())
        except discord.HTTPException:
            too_big.append(a)
    return ok, too_big


def summary_embed(t: dict, guild: discord.Guild, links: list[tuple[str, str]], too_big: list[discord.Attachment]) -> discord.Embed:
    desc = f"**Entscheidung:**\n{t['decision']}"
    if t["penalty"]:
        desc += f"\n\n**Strafe(n):**\n{t['penalty']}"
    embed = discord.Embed(color=0xE10600, title=f"⚖️ FIA-Entscheidung · Rennvorfall {D.number(t)}", description=desc[:4096])
    embed.add_field(name="Einsender", value=f"<@{t['reporter_id']}>", inline=True)
    embed.add_field(name="Beschuldigt", value=", ".join(f"<@{u}>" for u in t["accused"]) or "—", inline=True)
    if t["extra"]:
        embed.add_field(name="Hinzugefügt", value=", ".join(f"<@{u}>" for u in t["extra"]), inline=True)
    embed.add_field(name="Gemeldet", value=f"<t:{t['opened_at']}:f>", inline=True)
    embed.add_field(name="Entschieden", value=f"<t:{t['closed_at']}:f>", inline=True)
    if links:
        lines = [f"• [Video]({url}) von <@{uid}>" for uid, url in dict.fromkeys(links)]
        embed.add_field(name="🎥 Videos & Links", value="\n".join(lines)[:1024], inline=False)
    if too_big:
        embed.add_field(name="⚠️ Zu groß zum Anhängen", inline=False,
                        value="\n".join(f"• {a.filename} ({a.size / 1_048_576:.0f} MB)" for a in too_big)[:1024])
    return embed


async def send_with_files(target, files: list[discord.File], **kwargs) -> discord.Message:
    """Erst die Entscheidung, danach die Anhänge (je bis zu 10 pro Nachricht) – so bleibt die Entscheidung oben."""
    first = await target.send(**kwargs)
    for i in range(0, len(files), 10):
        await target.send(files=files[i:i + 10])
    return first


def embed_text(e: discord.Embed) -> str:
    parts = [e.title or "", e.description or ""] + [f"{f.name}: {f.value}" for f in e.fields]
    return " | ".join(p.replace("\n", " / ") for p in parts if p)


def transcript(messages: list[discord.Message], t: dict) -> discord.File:
    lines = [f"Rennvorfall {D.number(t)} – Protokoll", ""]
    for m in messages:
        when = m.created_at.astimezone(TZ).strftime("%d.%m.%Y %H:%M:%S")
        text = m.content or (embed_text(m.embeds[0]) if m.embeds else "")
        lines.append(f"[{when}] {m.author.display_name} ({m.author.id}): {text}")
        lines += [f"    Anhang: {a.filename} ({a.size / 1_048_576:.1f} MB) {a.url}" for a in m.attachments]
    lines += ["", f"Entscheidung: {t['decision']}", f"Strafe(n): {t['penalty'] or '—'}"]
    return discord.File(io.BytesIO("\n".join(lines).encode("utf-8")), filename=f"vorfall-{int(t['id']):04d}.txt")


async def copy_to_archive(target, messages: list[discord.Message], bot_id: int, limit: int, entry_ids: set[int]) -> list[str]:
    """Kopiert den Verlauf in den Archiv-Thread. Gibt Dateinamen zurück, die nicht mitkamen."""
    missing, buffer = [], ""

    async def flush():
        nonlocal buffer
        if buffer:
            await target.send(buffer, allowed_mentions=discord.AllowedMentions.none())
            buffer = ""

    for m in messages:
        if m.id in entry_ids:  # Formular-Eintrag (Meldung/Aussage) mit Dateien übernehmen
            await flush()
            files, too_big = await download(m.attachments, limit)
            missing += [a.filename for a in too_big]
            await target.send(embeds=m.embeds[:1], files=files[:10], allowed_mentions=discord.AllowedMentions.none())
            continue
        if m.author.id == bot_id and not m.content:
            continue  # Status-Embeds des Bots
        block = f"**{m.author.display_name}** · <t:{int(m.created_at.timestamp())}:f>\n{m.content}".strip()
        if m.attachments:
            await flush()
            files, too_big = await download(m.attachments, limit)
            missing += [a.filename for a in too_big]
            note = "".join(f"\n⚠️ {a.filename} war zu groß zum Archivieren" for a in too_big)
            await target.send((block + note)[:2000], files=files[:10], allowed_mentions=discord.AllowedMentions.none())
            continue
        if len(buffer) + len(block) + 2 > 1900:
            await flush()
        buffer += ("\n\n" if buffer else "") + block[:1900]
    await flush()
    return missing


def thread_name(guild: discord.Guild, t: dict) -> str:
    accused = ", ".join((m.display_name if (m := guild.get_member(int(u))) else u) for u in t["accused"])
    reporter = guild.get_member(int(t["reporter_id"]))
    return f"Rennvorfall {D.number(t)} · {reporter.display_name if reporter else t['reporter_id']} vs {accused}"[:100]


async def archive(guild: discord.Guild, t: dict, s: dict, messages, summary: discord.Embed) -> tuple[str | None, bool]:
    """Legt den Archiv-Thread an. Gibt (Link, alle Dateien archiviert?) zurück."""
    channel = guild.get_channel(s["archive_channel_id"] or 0)
    if channel is None:
        return None, False
    name = thread_name(guild, t)
    file = transcript(messages, t)
    if isinstance(channel, discord.ForumChannel):
        thread = (await channel.create_thread(name=name, embed=summary, file=file,
                                              allowed_mentions=discord.AllowedMentions.none())).thread
    else:
        starter = await channel.send(embed=summary, file=file, allowed_mentions=discord.AllowedMentions.none())
        thread = await starter.create_thread(name=name, auto_archive_duration=10080)
    entry_ids = {e["message_id"] for e in t["entries"].values()}
    missing = await copy_to_archive(thread, messages, guild.me.id, guild.filesize_limit, entry_ids)
    try:
        await thread.edit(archived=True, locked=True)
    except discord.HTTPException:
        pass
    return thread.jump_url, not missing


async def close_ticket(interaction: discord.Interaction, tid: str, decision: str, penalty: str | None):
    guild = interaction.guild
    d = D.get(guild.id)
    s = d["settings"]
    t = d["tickets"].get(tid)
    if t is None or t["phase"] in ("closed", "cancelled"):
        return await interaction.response.send_message("Dieser Vorfall ist schon abgeschlossen.", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    async with lock(guild.id, tid):
        channel = interaction.channel
        t.update(phase="closed", decision=decision, penalty=penalty, closed_at=now(), closed_by=str(interaction.user.id))
        await channel.send(add_log(t, f"Fall abgeschlossen von <@{interaction.user.id}>"), allowed_mentions=discord.AllowedMentions.none())
        storage.save()

        messages, links, attachments = await collect(channel, t)
        results = guild.get_channel(s["result_channel_id"] or 0)
        files, too_big = await download(attachments, guild.filesize_limit)
        summary = summary_embed(t, guild, links, too_big)
        result_url = None
        if results:
            pings = " ".join(f"<@{u}>" for u in [t["reporter_id"], *t["accused"]])
            kwargs = {"content": pings, "embed": summary,
                      "allowed_mentions": discord.AllowedMentions(users=True, roles=False, everyone=False)}
            try:
                if isinstance(results, discord.ForumChannel):
                    # Forum: jede Entscheidung ein eigener Beitrag, Anhänge darunter im Beitrag
                    post = await results.create_thread(name=thread_name(guild, t), **kwargs)
                    for i in range(0, len(files), 10):
                        await post.thread.send(files=files[i:i + 10])
                    result_url = post.thread.jump_url
                else:
                    result_url = (await send_with_files(results, files, **kwargs)).jump_url
            except discord.HTTPException as e:
                log.warning("Entscheidung #%s nicht veröffentlicht: %s", t["id"], e)

        archive_url, complete = await archive(guild, t, s, messages, summary_embed(t, guild, links, too_big))
        storage.save()

    notes = [f"✅ Entscheidung veröffentlicht: {result_url}" if result_url else "⚠️ Entscheidung nicht veröffentlicht – Ergebnis-Kanal fehlt oder mir fehlen dort Rechte (siehe `/rennvorfall einstellungen`)."]
    notes.append(f"🗄️ Archiv: {archive_url}" if archive_url else "⚠️ Archiv-Kanal nicht gefunden – nichts archiviert.")
    if archive_url and complete and result_url:
        notes.append("Der Ticket-Kanal wird in 15 Sekunden gelöscht.")
        await interaction.followup.send("\n".join(notes), ephemeral=True)
        await asyncio.sleep(15)
        try:
            await channel.delete(reason=f"Rennvorfall {D.number(t)} abgeschlossen")
        except discord.HTTPException:
            pass
        return
    # Nicht alles gesichert (z. B. zu große Videos): Kanal nur schreibgeschützt behalten statt löschen
    for target in channel.overwrites:
        if target != guild.me:
            await channel.set_permissions(target, overwrite=discord.PermissionOverwrite(
                **{**dict(channel.overwrites[target]), "send_messages": False}))
    await channel.edit(name=f"archiv-vorfall-{int(t['id']):04d}")
    notes.append("⚠️ Nicht alles konnte gesichert werden – der Kanal bleibt deshalb schreibgeschützt erhalten.")
    await interaction.followup.send("\n".join(notes), ephemeral=True)
