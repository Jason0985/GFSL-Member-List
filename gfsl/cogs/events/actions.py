"""Was mit einem Event passiert: posten, aktualisieren, Frist, Erinnerung, schließen – und die Anmelde-Buttons."""
import asyncio
import logging
import time

import discord

from gfsl import storage
from gfsl.permissions import is_admin

from . import assign, render
from . import data as D
from . import timeutil as tu

log = logging.getLogger("gfsl.events")

_locks: dict[str, asyncio.Lock] = {}


def lock(guild_id: int, ev_id: str) -> asyncio.Lock:
    """Eine Änderung pro Event gleichzeitig, damit sich Nachrichten-Updates nicht überholen."""
    return _locks.setdefault(f"{guild_id}:{ev_id}", asyncio.Lock())


def reply_to(ev: dict) -> discord.MessageReference | None:
    """Antwort auf die Event-Nachricht – schlägt nicht fehl, falls sie gelöscht wurde."""
    if not ev.get("message_id"):
        return None
    return discord.MessageReference(message_id=ev["message_id"], channel_id=ev["channel_id"], fail_if_not_exists=False)


def has_reserves(d: dict, ev: dict) -> bool:
    reserve_ids = {c["id"] for c in assign.classes(d) if c["kind"] == "reserve"}
    return any(s["status"] == "yes" and s["class_id"] in reserve_ids for s in ev["signups"].values())


# ---------- Buttons ----------

# Ohne Icons: Farbe + Text reichen, ein rotes ❌ auf rotem Button sieht man schlecht
BUTTONS = {
    "yes": ("Anmelden", discord.ButtonStyle.success),
    "maybe": ("Vielleicht", discord.ButtonStyle.secondary),
    "no": ("Abmelden", discord.ButtonStyle.danger),
}


class SignupButton(discord.ui.DynamicItem[discord.ui.Button], template=r"ev:(?P<status>yes|maybe|no):(?P<ev>\d+)"):
    def __init__(self, status: str, ev_id: str):
        label, style = BUTTONS[status]
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"ev:{status}:{ev_id}"))
        self.status = status
        self.ev_id = ev_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["status"], match["ev"])

    async def callback(self, interaction: discord.Interaction):
        await signup(interaction, self.ev_id, self.status)


class AdminEditButton(discord.ui.DynamicItem[discord.ui.Button], template=r"ev:edit:(?P<ev>\d+)"):
    """⚙️ am Event: Admins können Titel, Beschreibung, Start und Bild direkt hier ändern."""

    def __init__(self, ev_id: str):
        super().__init__(discord.ui.Button(emoji="⚙️", style=discord.ButtonStyle.secondary, custom_id=f"ev:edit:{ev_id}"))
        self.ev_id = ev_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["ev"])

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            return await interaction.response.send_message("⚙️ Das ist nur für Admins (Event bearbeiten).", ephemeral=True)
        ev = D.get(interaction.guild_id)["events"].get(self.ev_id)
        if ev is None or ev["status"] != "posted":
            return await interaction.response.send_message("Dieses Event kann nicht mehr bearbeitet werden.", ephemeral=True)
        from .review import EventEditModal  # review importiert actions, daher erst hier
        await interaction.response.send_modal(EventEditModal(interaction.guild, self.ev_id))


def signup_view(ev_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for status in BUTTONS:
        view.add_item(SignupButton(status, ev_id))
    view.add_item(AdminEditButton(ev_id))
    return view


async def rescheduled(guild: discord.Guild, d: dict, ev: dict):
    """Start eines offenen Events geändert: Q-Sperren und Einteilung passend zur neuen Frist neu berechnen."""
    async with lock(guild.id, ev["id"]):
        deadline = render.times(d, ev)["deadline"]
        for s in ev["signups"].values():
            if s["status"] == "yes":
                s["late"] = s["at"] >= deadline.timestamp()
        remind = render.times(d, ev)["remind"]
        if remind and tu.now() < remind:
            ev["remind_done"] = False  # Erinnerung liegt wieder in der Zukunft
        if tu.now() < deadline and ev["deadline_done"]:
            # Frist liegt wieder in der Zukunft: Einteilung erst zur neuen Frist
            ev["deadline_done"] = False
            ev["assignments"] = {}
            channel = guild.get_channel(ev.get("channel_id") or 0)
            if channel and ev.get("assignment_message_id"):
                try:
                    await channel.get_partial_message(ev["assignment_message_id"]).edit(content=None, embed=discord.Embed(
                        color=0x64748B, description=f"⏳ Termin verschoben – die Ersatzfahrer werden zur neuen Frist "
                                                    f"<t:{tu.ts(deadline)}:f> neu eingeteilt."))
                except discord.HTTPException:
                    pass
        storage.save()
    await refresh(guild, d, ev)


# ---------- Anmelden ----------

STATUS_TEXT = {"yes": "✅ Angemeldet", "maybe": "❔ Als „vielleicht“ eingetragen", "no": "❌ Abgemeldet"}


def set_signup(d: dict, ev: dict, uid: str, status: str, class_id: int, name: str) -> tuple[bool, bool]:
    """Trägt die Anmeldung ein. Gibt (geändert, nach der Frist angemeldet) zurück."""
    prev = ev["signups"].get(uid)
    if prev and prev["status"] == status and prev["class_id"] == class_id:
        return False, False
    after_deadline = tu.now() >= render.times(d, ev)["deadline"]
    was_yes = bool(prev and prev["status"] == "yes")
    late = status == "yes" and (prev.get("late", False) if was_yes else after_deadline)
    if was_yes and status == "yes":
        order = prev["order"]  # nur die Klasse hat sich geändert
    else:
        order = ev["next_order"]
        ev["next_order"] += 1
    ev["signups"][uid] = {"status": status, "class_id": class_id, "name": name, "at": int(time.time()),
                          "order": order, "late": late}
    return True, late and not (was_yes and prev.get("late"))


async def signup(interaction: discord.Interaction, ev_id: str, status: str):
    d = D.get(interaction.guild_id)
    ev = d["events"].get(ev_id)
    if not ev or ev["status"] != "posted" or tu.now() >= render.times(d, ev)["start"]:
        return await interaction.response.send_message("🔒 Die Anmeldung für dieses Event ist geschlossen.", ephemeral=True)
    cls = assign.class_for(interaction.user, d)
    if cls is None:
        roles = ", ".join(f"<@&{c['role_id']}>" for c in assign.classes(d)) or "—"
        return await interaction.response.send_message(
            f"Du hast keine Rolle, mit der man sich anmelden kann ({roles}).", ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none())

    uid = str(interaction.user.id)
    changed, late = set_signup(d, ev, uid, status, cls["id"], interaction.user.display_name)
    if not changed:
        return await interaction.response.send_message(f"{STATUS_TEXT[status]} – das warst du schon.", ephemeral=True)
    storage.save()

    deadline = render.times(d, ev)["deadline"]
    msg = STATUS_TEXT[status] + (f" als {cls['emoji']} **{cls['name']}**." if status == "yes" else ".")
    if late:
        msg += (f"\n⚠️ Die Anmeldefrist ist abgelaufen (<t:{tu.ts(deadline)}:f>). "
                "Du bist angemeldet, hast aber eine **Q-Sperre**.")
    await interaction.response.send_message(msg, ephemeral=True)
    if late:
        try:
            await interaction.user.send(embed=discord.Embed(color=0xEF4444, description=(
                f"⏰ Du hast dich für **{D.field(d, ev, 'title')}** nach der Anmeldefrist angemeldet.\n"
                "Damit hast du für dieses Rennen eine **Q-Sperre**.")))
        except discord.HTTPException:
            pass
    await refresh(interaction.guild, d, ev)


async def refresh(guild: discord.Guild, d: dict, ev: dict):
    """Nachricht neu zeichnen; nach der Frist auch die Ersatzfahrer-Einteilung nachziehen."""
    async with lock(guild.id, ev["id"]):
        changes = assign.update(d, ev) if ev["deadline_done"] else []
        storage.save()
        await update_message(guild, d, ev)
        if changes:
            await post_assignment(guild, d, ev, changes)


async def update_message(guild: discord.Guild, d: dict, ev: dict, new_image: bool = False):
    channel = guild.get_channel(ev.get("channel_id") or 0)
    if channel is None or not ev.get("message_id"):
        return
    kwargs = {"embed": render.event_embed(guild, d, ev), "view": signup_view(ev["id"]) if ev["status"] == "posted" else None}
    if new_image:  # Bild geändert: alten Anhang ersetzen
        file = D.image_file(D.field(d, ev, "image"))
        kwargs["attachments"] = [file] if file else []
    try:
        await channel.get_partial_message(ev["message_id"]).edit(**kwargs)
    except discord.NotFound:
        log.warning("Event-Nachricht #%s wurde gelöscht", ev["id"])
    except discord.HTTPException as e:
        log.warning("Event #%s nicht aktualisiert: %s", ev["id"], e)


async def post_assignment(guild: discord.Guild, d: dict, ev: dict, changes: list):
    """Einteilungs-Nachricht unter dem Event anlegen bzw. aktualisieren; bei späteren Änderungen kurz Bescheid geben."""
    channel = guild.get_channel(ev.get("channel_id") or 0)
    if channel is None:
        return
    embed = render.assignment_embed(guild, d, ev)
    teams = {c["id"]: c for c in assign.classes(d)}
    pings = discord.AllowedMentions(users=True, roles=False, everyone=False)
    msg_id = ev.get("assignment_message_id")
    if msg_id:
        try:
            await channel.get_partial_message(msg_id).edit(embed=embed)
        except discord.NotFound:
            msg_id = None
    if not msg_id:
        assigned = " ".join(f"<@{u}>" for u in ev["assignments"])
        msg = await channel.send(content=assigned or None, embed=embed, allowed_mentions=pings, reference=reply_to(ev))
        ev["assignment_message_id"] = msg.id
        storage.save()
        return
    lines = []
    for uid, old, new in changes:
        if new is not None:
            lines.append(f"🔁 <@{uid}> fährt jetzt für {teams[new]['emoji']} **{teams[new]['name']}**")
        else:
            lines.append(f"🚫 <@{uid}> hat keine Einteilung mehr – alle Plätze belegt")
    if lines:
        await channel.send("**Einteilung geändert:**\n" + "\n".join(lines), allowed_mentions=pings)


# ---------- Ablauf ----------

async def post(guild: discord.Guild, d: dict, ev: dict) -> str:
    t = D.template_of(d, ev)
    channel = guild.get_channel(t["channel_id"] or 0)
    if channel is None:
        return "❌ Der Kanal der Vorlage fehlt oder ist nicht erreichbar."
    ev["status"] = "posted"
    ev["confirmed"] = True
    ev["channel_id"] = channel.id
    # Die Anmeldung selbst pingt niemanden – das macht die Info-Nachricht
    kwargs = {"embed": render.event_embed(guild, d, ev), "view": signup_view(ev["id"])}
    if file := D.image_file(D.field(d, ev, "image")):
        kwargs["file"] = file
    try:
        msg = await channel.send(**kwargs)
    except discord.HTTPException as e:
        ev["status"] = "planned"
        return f"❌ Posten fehlgeschlagen: {e.text or e}"
    ev["message_id"] = msg.id
    storage.save()
    result = f"✅ Gepostet in {channel.mention}: {msg.jump_url}"
    if t["announce"]:
        if err := await send_info(guild, d, ev, "open"):
            result += f"\n⚠️ {err}"
    return result


async def send_info(guild: discord.Guild, d: dict, ev: dict, kind: str) -> str | None:
    """Info „Anmeldung ist offen“ (kind="open") oder Anmelde-Erinnerung (kind="remind") mit @-Erwähnungen."""
    t = D.template_of(d, ev)
    channel = guild.get_channel(t["info_channel_id"] or ev.get("channel_id") or 0)
    if channel is None:
        return "Info-Kanal nicht gefunden."
    pings = " ".join(f"<@&{r}>" for r in t["ping_role_ids"]) or None
    try:
        await channel.send(content=pings, embed=render.info_embed(guild, d, ev, kind),
                           allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False))
    except discord.HTTPException as e:
        log.warning("Info (%s) für Event #%s nicht gesendet: %s", kind, ev["id"], e)
        return f"Info-Nachricht nicht gesendet: {e.text or e}"
    return None


async def deadline_reached(guild: discord.Guild, d: dict, ev: dict):
    async with lock(guild.id, ev["id"]):
        ev["deadline_done"] = True
        assign.update(d, ev)
        storage.save()
        await update_message(guild, d, ev)
        if has_reserves(d, ev):
            await post_assignment(guild, d, ev, [])


async def close(guild: discord.Guild, d: dict, ev: dict):
    async with lock(guild.id, ev["id"]):
        ev["status"] = "closed"
        storage.save()
        await update_message(guild, d, ev)
