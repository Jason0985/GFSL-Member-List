"""Ablauf eines Rennvorfalls (Ticket):

1. „Vorfall melden“ -> privater Kanal für Einsender + Ligaleitung/Stewards
2. Einsender öffnet (jederzeit, auch später) das Meldeformular: Beschuldigte, Vorfall, Beschreibung, Video/Links.
   Danach „Einsendung abschicken“ -> Einsender wird entfernt, Beschuldigte werden hinzugeholt
3. Jeder Beschuldigte (und von Stewards hinzugefügte Personen) füllt das Aussage-Formular aus und klickt
   „Aussage abschicken“ -> wird danach entfernt. Kommt nichts: „Ohne Gegenclip bewerten“.
4. Stewards beraten und tragen mit „Entscheidung eintragen“ die Entscheidung ein
   -> Ergebnis mit Links/Dateien im Ergebnis-Kanal, Verlauf als Thread im Archiv, Ticket-Kanal wird gelöscht

Befangenheit: Wer Einsender, Beschuldigter oder hinzugefügt ist, bekommt nach seinem Teil eine Sperre für den Kanal –
auch wenn er selbst Ligaleitung/Steward ist (Mitglieds-Sperre schlägt Rollen-Freigabe).
"""
import asyncio
import logging
import time

import discord

from gfsl import storage

from . import data as D

log = logging.getLogger("gfsl.incidents")

PARTY = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True,
                                    read_message_history=True)
STAFF = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True,
                                    read_message_history=True, manage_messages=True)
REMOVED = discord.PermissionOverwrite(view_channel=False)

_locks: dict[str, asyncio.Lock] = {}


def lock(guild_id: int, tid: str) -> asyncio.Lock:
    return _locks.setdefault(f"{guild_id}:{tid}", asyncio.Lock())


def now() -> int:
    return int(time.time())


def is_staff(member: discord.Member, s: dict) -> bool:
    if member.guild_permissions.manage_guild:
        return True
    return any(member.get_role(rid) for rid in s["staff_role_ids"])


def parties(t: dict) -> list[str]:
    return [t["reporter_id"], *t["accused"], *t["extra"]]


def pending(t: dict) -> list[str]:
    """Beschuldigte/Hinzugefügte, die noch nichts abgeschickt haben."""
    return [u for u in [*t["accused"], *t["extra"]] if u not in t["submitted"] and u not in t["skipped"]]


def add_log(t: dict, text: str) -> str:
    ts = now()
    t["log"].append([ts, text])
    return f"🕒 <t:{ts}:f> – {text}"


# ---------- Panel ----------

def panel_embed(s: dict) -> discord.Embed:
    return discord.Embed(color=0xE10600, title=s["panel_title"], description=s["panel_text"])


class PanelView(discord.ui.View):
    def __init__(self, label: str = "Vorfall melden"):
        super().__init__(timeout=None)
        button = discord.ui.Button(label=label, style=discord.ButtonStyle.danger, custom_id="rv:open")
        button.callback = open_ticket
        self.add_item(button)


# ---------- Status im Ticket ----------

def state(u: str, t: dict) -> str:
    if u in t["submitted"]:
        return f"✅ abgeschickt <t:{t['submitted'][u]}:f>"
    if u in t["skipped"]:
        return "⏭️ ohne Aussage"
    if u in t["entries"]:
        return f"📝 Formular ausgefüllt <t:{t['entries'][u]['at']}:f> – noch nicht abgeschickt"
    return "⏳ Formular noch offen"


def who(uids: list[str], t: dict) -> str:
    return "\n".join(f"<@{u}> – {state(u, t)}" for u in uids) or "—"


INSTRUCTIONS = {
    "reporter": ("**{reporter}:** Öffne mit **📝 Aussage eintragen** das Meldeformular – das geht jederzeit, auch später, "
                 "und du kannst es beliebig oft ändern. Wenn alles passt: **Einsendung endgültig abschicken**."),
    "statements": ("**Beschuldigte:** Schaut euch die Meldung oben an, füllt über **📝 Aussage eintragen** eure Aussage "
                   "aus (Text + Video/YouTube-Link) und klickt dann **Aussage endgültig abschicken**. Danach werdet ihr aus dem "
                   "Ticket entfernt.\n**Stewards:** Kommt kein Gegenclip, geht es mit **Ohne Gegenclip bewerten** weiter."),
    "review": ("**Ligaleitung & Stewards:** Alle Aussagen sind da. Beratet hier und tragt die Entscheidung mit "
               "**⚖️ Entscheidung eintragen** ein. Weitere Personen holt ihr mit **Person hinzufügen** dazu."),
}


def status_embed(t: dict) -> discord.Embed:
    embed = discord.Embed(color=0xF59E0B if t["phase"] != "closed" else 0x22C55E,
                          title=f"⚖️ Rennvorfall {D.number(t)} · {D.PHASES[t['phase']]}",
                          description=INSTRUCTIONS.get(t["phase"], "").format(reporter=f"<@{t['reporter_id']}>"))
    reporter = t["reporter_id"]
    embed.add_field(name="Einsender", value=f"<@{reporter}> – {state(reporter, t)}", inline=False)
    embed.add_field(name="Beschuldigt", value=who(t["accused"], t) if t["accused"] else "— (wird beim Abschicken ausgewählt)", inline=False)
    if t["extra"]:
        embed.add_field(name="Hinzugefügt", value=who(t["extra"], t), inline=False)
    embed.add_field(name="Eröffnet", value=f"<t:{t['opened_at']}:f>", inline=False)
    return embed


ACTIONS = {
    "form": ("Aussage eintragen", discord.ButtonStyle.primary, "📝"),
    "submit": ("Einsendung endgültig abschicken", discord.ButtonStyle.danger, None),
    "cancel": ("Abbrechen", discord.ButtonStyle.secondary, None),
    "statement": ("Aussage endgültig abschicken", discord.ButtonStyle.danger, None),
    "skip": ("Ohne Gegenclip bewerten", discord.ButtonStyle.secondary, "⏭️"),
    "add": ("Person hinzufügen", discord.ButtonStyle.secondary, "➕"),
    "close": ("Entscheidung eintragen", discord.ButtonStyle.primary, "⚖️"),
}
PHASE_ACTIONS = {"reporter": ["form", "submit", "cancel"], "statements": ["form", "statement", "add", "skip"],
                 "review": ["add", "close"]}


class TicketButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rv:(?P<action>[a-z]+):(?P<t>\d+)"):
    def __init__(self, action: str, tid: str, label: str | None = None):
        default_label, style, emoji = ACTIONS[action]
        label = label or default_label
        super().__init__(discord.ui.Button(label=label, style=style, emoji=emoji, custom_id=f"rv:{action}:{tid}"))
        self.action, self.tid = action, tid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], match["t"])

    async def callback(self, interaction: discord.Interaction):
        d = D.get(interaction.guild_id)
        t = d["tickets"].get(self.tid)
        if t is None or t["phase"] in ("closed", "cancelled"):
            return await interaction.response.send_message("Dieser Vorfall ist schon abgeschlossen.", ephemeral=True)
        if self.action not in PHASE_ACTIONS.get(t["phase"], []):
            return await interaction.response.send_message("Das geht in diesem Schritt nicht mehr.", ephemeral=True)
        await HANDLERS[self.action](interaction, d, t)


def status_view(t: dict) -> discord.ui.View | None:
    actions = PHASE_ACTIONS.get(t["phase"])
    if not actions:
        return None
    view = discord.ui.View(timeout=None)
    for a in actions:
        view.add_item(TicketButton(a, t["id"], form_label(t) if a == "form" else None))
    return view


def form_label(t: dict) -> str:
    """„Aussage eintragen“ beim ersten Mal, danach „Aussage anpassen“."""
    who_fills = [t["reporter_id"]] if t["phase"] == "reporter" else pending(t)
    done = [u in t["entries"] for u in who_fills]
    if who_fills and all(done):
        return "Aussage anpassen"
    if any(done):
        return "Aussage eintragen / anpassen"
    return "Aussage eintragen"


async def repost_status(channel: discord.TextChannel, t: dict):
    """Status mit Buttons immer als letzte Nachricht, damit die Buttons unten erreichbar sind."""
    if t.get("status_message_id"):
        try:
            await channel.get_partial_message(t["status_message_id"]).delete()
        except discord.HTTPException:
            pass
    msg = await channel.send(embed=status_embed(t), view=status_view(t), allowed_mentions=discord.AllowedMentions.none())
    t["status_message_id"] = msg.id
    storage.save()


async def say(channel: discord.TextChannel, text: str, ping_users: list[str] = (), ping_roles: list[int] = ()):
    pings = " ".join([*(f"<@{u}>" for u in ping_users), *(f"<@&{r}>" for r in ping_roles)])
    await channel.send(f"{pings}\n{text}" if pings else text,
                       allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False))


# ---------- 1. Ticket eröffnen ----------

async def open_ticket(interaction: discord.Interaction):
    d = D.get(interaction.guild_id)
    s = d["settings"]
    guild = interaction.guild
    category = guild.get_channel(s["category_id"] or 0)
    if not isinstance(category, discord.CategoryChannel) or not s["staff_role_ids"] or not s["result_channel_id"]:
        return await interaction.response.send_message("Die Vorfall-Meldung ist noch nicht eingerichtet. Bitte der Ligaleitung Bescheid geben.",
                                                       ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    t = D.new_ticket(d, interaction.user.id, now())
    overwrites = {guild.default_role: REMOVED, guild.me: STAFF, interaction.user: PARTY}
    for rid in s["staff_role_ids"]:
        if role := guild.get_role(rid):
            overwrites[role] = STAFF
    try:
        channel = await guild.create_text_channel(
            f"vorfall-{int(t['id']):04d}", category=category, overwrites=overwrites,
            topic=f"Rennvorfall {D.number(t)} – gemeldet von {interaction.user.display_name}", reason="Rennvorfall gemeldet")
    except discord.HTTPException as e:
        del d["tickets"][t["id"]]
        storage.save()
        return await interaction.followup.send(f"⚠️ Ticket konnte nicht angelegt werden ({e.text or e}). Bitte der Ligaleitung Bescheid geben.",
                                               ephemeral=True)
    t["channel_id"] = channel.id
    storage.save()

    await channel.send(
        content=f"<@{interaction.user.id}>",
        embed=discord.Embed(color=0xE10600, title=f"⚖️ Rennvorfall {D.number(t)}",
                            description="Dein Ticket ist offen. Unten mit **📝 Aussage eintragen** die Meldung ausfüllen – "
                                        "jetzt oder später. Du kannst hier auch zusätzlich schreiben oder Dateien posten."))
    await channel.send(add_log(t, f"Ticket eröffnet von <@{interaction.user.id}>"), allowed_mentions=discord.AllowedMentions.none())
    await repost_status(channel, t)
    await interaction.followup.send(f"✅ Dein Ticket: {channel.mention}", ephemeral=True)


# ---------- 2. Einsendung abschicken ----------

class PickUsersView(discord.ui.View):
    """Auswahl von Personen (zum Hinzufügen durch die Stewards) mit Bestätigen-Button."""

    def __init__(self, placeholder: str, confirm_label: str, on_confirm):
        super().__init__(timeout=600)
        self.on_confirm = on_confirm
        self.select = discord.ui.UserSelect(placeholder=placeholder, min_values=1, max_values=10)
        self.select.callback = self.picked
        self.add_item(self.select)
        self.confirm = discord.ui.Button(label=confirm_label, style=discord.ButtonStyle.success, disabled=True)
        self.confirm.callback = self.confirmed
        self.add_item(self.confirm)

    async def picked(self, interaction: discord.Interaction):
        self.confirm.disabled = False
        await interaction.response.edit_message(view=self)

    async def confirmed(self, interaction: discord.Interaction):
        self.stop()
        users = [u for u in self.select.values if not u.bot]
        await self.on_confirm(interaction, users)


async def on_form(interaction: discord.Interaction, d: dict, t: dict):
    from .forms import EntryForm  # forms importiert ticket, daher erst hier
    uid = str(interaction.user.id)
    if t["phase"] == "reporter" and uid == t["reporter_id"]:
        return await interaction.response.send_modal(EntryForm(t, uid, "report"))
    if t["phase"] == "statements" and uid in pending(t):
        return await interaction.response.send_modal(EntryForm(t, uid, "statement"))
    await interaction.response.send_message("Für dich gibt es hier gerade kein Formular auszufüllen.", ephemeral=True)


async def on_submit(interaction: discord.Interaction, d: dict, t: dict):
    if str(interaction.user.id) != t["reporter_id"]:
        return await interaction.response.send_message("Nur der Einsender kann die Einsendung abschicken.", ephemeral=True)
    entry = t["entries"].get(t["reporter_id"])
    if not entry:
        return await interaction.response.send_message("Bitte fülle zuerst mit **📝 Aussage eintragen** die Meldung aus.", ephemeral=True)
    accused = [m for u in entry["fields"]["accused"] if (m := interaction.guild.get_member(int(u)))]
    if not accused:
        return await interaction.response.send_message("Die Beschuldigten sind nicht (mehr) auf dem Server – bitte im Formular ändern.",
                                                       ephemeral=True)
    await interaction.response.send_message("✅ Abgeschickt. Du wirst jetzt aus dem Ticket entfernt – die Entscheidung der "
                                            "Stewards wird im Ergebnis-Kanal veröffentlicht.", ephemeral=True)
    async with lock(interaction.guild_id, t["id"]):
        if t["phase"] != "reporter":
            return
        channel = interaction.channel
        t["submitted"][t["reporter_id"]] = now()
        t["accused"] = [str(m.id) for m in accused]
        t["phase"] = "statements"
        await channel.set_permissions(interaction.user, overwrite=REMOVED, reason="Einsendung abgeschickt")
        for m in accused:
            await channel.set_permissions(m, overwrite=PARTY, reason="Beschuldigt im Rennvorfall")
        await channel.send(add_log(t, f"Einsendung von <@{t['reporter_id']}> abgeschickt – Einsender entfernt"),
                           allowed_mentions=discord.AllowedMentions.none())
        await say(channel, "Ihr wurdet in diesem Rennvorfall gemeldet. Bitte schaut euch die Meldung oben an, füllt über "
                           "**📝 Aussage eintragen** eure Aussage aus und klickt dann **Aussage endgültig abschicken**.",
                  ping_users=t["accused"])
        await repost_status(channel, t)


async def on_cancel(interaction: discord.Interaction, d: dict, t: dict):
    member = interaction.user
    if str(member.id) != t["reporter_id"] and not is_staff(member, d["settings"]):
        return await interaction.response.send_message("Nur der Einsender oder die Stewards können abbrechen.", ephemeral=True)
    t["phase"] = "cancelled"
    t["closed_at"] = now()
    storage.save()
    await interaction.response.send_message("✖️ Vorfall abgebrochen – der Kanal wird in 5 Sekunden gelöscht.")
    await asyncio.sleep(5)
    try:
        await interaction.channel.delete(reason="Rennvorfall abgebrochen")
    except discord.HTTPException:
        pass


# ---------- 3. Aussagen ----------

async def to_review(channel: discord.TextChannel, d: dict, t: dict, by: str):
    t["phase"] = "review"
    await channel.send(add_log(t, by), allowed_mentions=discord.AllowedMentions.none())
    await say(channel, "⚖️ **Bereit zur Bewertung.** Alle Teile sind da – ihr könnt beraten und die Entscheidung eintragen.",
              ping_roles=d["settings"]["staff_role_ids"])


async def on_statement(interaction: discord.Interaction, d: dict, t: dict):
    uid = str(interaction.user.id)
    if uid not in pending(t):
        return await interaction.response.send_message("Du hast hier nichts (mehr) abzuschicken.", ephemeral=True)
    if uid not in t["entries"]:
        return await interaction.response.send_message(
            "Bitte fülle zuerst mit **📝 Aussage eintragen** deine Aussage aus. "
            "Willst du nichts dazu sagen, schreib dort z. B. „Keine Aussage“.", ephemeral=True)
    await interaction.response.send_message("✅ Aussage abgeschickt. Du wirst jetzt aus dem Ticket entfernt.", ephemeral=True)
    async with lock(interaction.guild_id, t["id"]):
        channel = interaction.channel
        t["submitted"][uid] = now()
        await channel.set_permissions(interaction.user, overwrite=REMOVED, reason="Aussage abgeschickt")
        await channel.send(add_log(t, f"Aussage von <@{uid}> abgeschickt – entfernt"),
                           allowed_mentions=discord.AllowedMentions.none())
        if not pending(t):
            await to_review(channel, d, t, "Alle Aussagen sind da")
        await repost_status(channel, t)


async def on_skip(interaction: discord.Interaction, d: dict, t: dict):
    if not is_staff(interaction.user, d["settings"]):
        return await interaction.response.send_message("Das können nur Ligaleitung und Stewards.", ephemeral=True)
    await interaction.response.defer()
    async with lock(interaction.guild_id, t["id"]):
        channel = interaction.channel
        for uid in pending(t):
            t["skipped"].append(uid)
            await channel.set_permissions(discord.Object(int(uid)), overwrite=REMOVED, reason="Ohne Gegenclip bewertet")
        await to_review(channel, d, t, f"Ohne Gegenclip weiter (<@{interaction.user.id}>) – Ausstehende entfernt")
        await repost_status(channel, t)


async def on_add(interaction: discord.Interaction, d: dict, t: dict):
    if not is_staff(interaction.user, d["settings"]):
        return await interaction.response.send_message("Das können nur Ligaleitung und Stewards.", ephemeral=True)

    async def confirm(i: discord.Interaction, users: list[discord.Member]):
        new = [u for u in users if str(u.id) not in parties(t)]
        if not new:
            return await i.response.edit_message(content="Diese Personen sind schon beteiligt.", view=None)
        await i.response.edit_message(content=f"➕ {', '.join(u.mention for u in new)} hinzugefügt.", view=None)
        async with lock(i.guild_id, t["id"]):
            channel = i.channel
            for u in new:
                t["extra"].append(str(u.id))
                await channel.set_permissions(u, overwrite=PARTY, reason="Zum Rennvorfall hinzugefügt")
            t["phase"] = "statements"
            await channel.send(add_log(t, f"{', '.join(u.mention for u in new)} hinzugefügt von <@{i.user.id}>"),
                               allowed_mentions=discord.AllowedMentions.none())
            await say(channel, "Ihr wurdet zu diesem Rennvorfall dazugeholt. Bitte schaut euch alles oben an, füllt über "
                               "**📝 Aussage eintragen** eure Sicht aus und klickt dann **Aussage endgültig abschicken**.",
                      ping_users=[str(u.id) for u in new])
            await repost_status(channel, t)

    await interaction.response.send_message("Wen möchtest du dazuholen (z. B. weitere Beteiligte oder für eine andere Sicht)?",
                                            ephemeral=True, view=PickUsersView("Personen auswählen …", "Hinzufügen", confirm))


# ---------- 4. Abschließen ----------

class CloseModal(discord.ui.Modal, title="Entscheidung eintragen"):
    decision = discord.ui.TextInput(label="Entscheidung", style=discord.TextStyle.long, max_length=3000,
                                    placeholder="z. B. Fahrer X trägt die Hauptschuld am Unfall in Kurve 1, weil …")
    penalty = discord.ui.TextInput(label="Strafe(n)", style=discord.TextStyle.long, max_length=1000, required=False,
                                   placeholder="z. B. 5 Sekunden Zeitstrafe für @X, Verwarnung für @Y – leer = keine Strafe")

    def __init__(self, tid: str):
        super().__init__()
        self.tid = tid

    async def on_submit(self, interaction: discord.Interaction):
        from .publish import close_ticket  # publish importiert ticket, daher erst hier
        await close_ticket(interaction, self.tid, self.decision.value.strip(), self.penalty.value.strip() or None)


async def on_close(interaction: discord.Interaction, d: dict, t: dict):
    if not is_staff(interaction.user, d["settings"]):
        return await interaction.response.send_message("Das können nur Ligaleitung und Stewards.", ephemeral=True)
    await interaction.response.send_modal(CloseModal(t["id"]))


HANDLERS = {"form": on_form, "submit": on_submit, "cancel": on_cancel, "statement": on_statement, "skip": on_skip, "add": on_add, "close": on_close}
