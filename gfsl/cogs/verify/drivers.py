"""Fahrernummern, Nicknames und die automatische Fahrerliste."""
import re

import discord

from gfsl import storage

from .constants import Colors
from .flags import lookup_flag


def taken_numbers(d: dict, except_user_id: str | None = None) -> set[int]:
    taken = set(d["settings"]["reserved_numbers"])
    for uid, drv in d["drivers"].items():
        if uid != except_user_id and isinstance(drv.get("number"), int):
            taken.add(drv["number"])
    return taken


def free_numbers(d: dict, except_user_id: str | None = None) -> list[int]:
    s = d["settings"]
    taken = taken_numbers(d, except_user_id)
    return [n for n in range(s["number_min"], s["number_max"] + 1) if n not in taken]


def next_free_number(d: dict, wanted: int | None, except_user_id: str | None = None) -> int | None:
    """Wunschnummer frei? -> nehmen. Sonst die nächste freie danach (am Ende wieder von vorne)."""
    lo, hi = d["settings"]["number_min"], d["settings"]["number_max"]
    taken = taken_numbers(d, except_user_id)
    start = wanted if isinstance(wanted, int) and lo <= wanted <= hi else lo
    size = hi - lo + 1
    for i in range(size):
        n = lo + (start - lo + i) % size
        if n not in taken:
            return n
    return None


def owner_of(d: dict, number: int, except_user_id: str | None = None) -> str | None:
    return next((uid for uid, drv in d["drivers"].items() if uid != except_user_id and drv.get("number") == number), None)


def render_nickname(template: str, drv: dict) -> str:
    """Platzhalter: {name} {nummer} {flagge} {plattform}"""
    values = {
        "name": drv.get("name") or "",
        "nummer": "" if drv.get("number") is None else str(drv["number"]),
        "flagge": lookup_flag(drv.get("flag")) or "",
        "plattform": drv.get("platform") or "",
    }
    text = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), ""), template)
    return re.sub(r"\s{2,}", " ", text).strip()[:32]


async def set_nickname(member: discord.Member, template: str, drv: dict) -> str | None:
    """Setzt den Nickname. Gibt eine Warnung zurück, falls es nicht geklappt hat."""
    nick = render_nickname(template, drv)
    if not nick:
        return None
    try:
        await member.edit(nick=nick, reason="GFSL Verify")
    except discord.HTTPException as e:
        return (f"Nickname nicht gesetzt ({e.text or e}). Die Bot-Rolle muss über der Rolle des Users stehen; "
                "den Server-Owner kann kein Bot umbenennen.")
    return None


def driver_line(uid: str, drv: dict) -> str:
    nr = f"#{drv['number']:02d}" if isinstance(drv.get("number"), int) else "#--"
    flag = lookup_flag(drv.get("flag"))
    name = discord.utils.escape_markdown(drv.get("name") or "?")
    return f"`{nr}` {flag + ' ' if flag else ''}**{name}** · <@{uid}>"


async def refresh_driver_list(guild: discord.Guild, d: dict) -> None:
    ch_id = d["settings"]["list_channel_id"]
    if not ch_id:
        return
    channel = guild.get_channel(ch_id)
    if channel is None:
        raise RuntimeError("Listen-Kanal nicht gefunden.")

    entries = sorted(d["drivers"].items(), key=lambda e: e[1]["number"] if isinstance(e[1].get("number"), int) else 9999)
    lines = [driver_line(uid, drv) for uid, drv in entries]

    # Aufteilen, falls die Liste für eine Nachricht zu lang wird
    chunks: list[str] = []
    cur = ""
    for line in lines:
        if len(cur) + len(line) + 1 > 3800:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur or not chunks:
        chunks.append(cur or "_Noch keine Fahrer verifiziert._")

    old_ids = d["settings"]["list_message_ids"]
    new_ids = []
    none = discord.AllowedMentions.none()
    for i, chunk in enumerate(chunks):
        embed = discord.Embed(color=Colors.list, description=chunk)
        if i == 0:
            embed.title = f"🏁 GFSL Fahrerliste ({len(entries)})"
        if i == len(chunks) - 1:
            embed.set_footer(text="Wird automatisch aktualisiert")
            embed.timestamp = discord.utils.utcnow()
        msg = None
        if i < len(old_ids):
            try:
                msg = await channel.fetch_message(old_ids[i])
                await msg.edit(embed=embed, allowed_mentions=none)
            except discord.NotFound:
                msg = None
        if msg is None:
            msg = await channel.send(embed=embed, allowed_mentions=none)
        new_ids.append(msg.id)
    for old in old_ids[len(chunks):]:
        try:
            await channel.get_partial_message(old).delete()
        except discord.HTTPException:
            pass
    d["settings"]["list_message_ids"] = new_ids
    storage.save()
