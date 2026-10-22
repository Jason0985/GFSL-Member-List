"""Anträge im Mod-Kanal: Zusammenfassung, Akzeptieren, Ablehnen."""
import logging
import time

import discord

from gfsl import storage

from . import data, drivers
from . import questions as Q
from .constants import Colors

log = logging.getLogger("gfsl.verify")


def is_mod(interaction: discord.Interaction, d: dict) -> bool:
    if interaction.permissions.manage_guild:
        return True
    rid = d["settings"]["mod_role_id"]
    return bool(rid) and any(r.id == rid for r in getattr(interaction.user, "roles", []))


def answer_of(app: dict, purpose: str):
    q = next((q for q in app["questions"] if q["purpose"] == purpose), None)
    return app["answers"].get(str(q["id"])) if q else None


def number_preview(d: dict, app: dict) -> str | None:
    if not any(q["purpose"] == "number" for q in app["questions"]):
        return None
    wanted = answer_of(app, "number")
    free = drivers.next_free_number(d, wanted, app["user_id"])
    if free is None:
        return "**Fahrernummer:** ⚠️ keine freie Nummer mehr!"
    if wanted == free:
        return f"**Fahrernummer:** #{free} (frei)"
    return f"**Fahrernummer:** Wunsch #{wanted if wanted is not None else '–'} ist belegt → bekommt **#{free}**"


async def get_user(client: discord.Client, guild: discord.Guild, user_id: int) -> discord.User | discord.Member:
    return guild.get_member(user_id) or await client.fetch_user(user_id)


async def build_summary(client: discord.Client, guild: discord.Guild, d: dict, app: dict, color: int, status: str,
                        number_line: str | None) -> discord.Embed:
    uid = int(app["user_id"])
    user = await get_user(client, guild, uid)
    joined = getattr(user, "joined_at", None)
    lines = [
        f"**User:** <@{uid}> (`{user.name}`)",
        f"**User-ID:** `{uid}`",
        f"**Account erstellt:** {discord.utils.format_dt(user.created_at, 'R')}",
        f"**Server beigetreten:** {discord.utils.format_dt(joined, 'R')}" if joined else None,
        f"**Eingereicht:** <t:{app['created_at']}:R>",
        number_line,
        f"**Status:** {status}",
    ]
    embed = discord.Embed(color=color, title=f"Verifizierung: {user.name}"[:256],
                          description="\n".join(line for line in lines if line))
    embed.set_thumbnail(url=user.display_avatar.url)
    for n, q in enumerate(app["questions"]):
        value = Q.display(q, app["answers"].get(str(q["id"])))
        if q["type"] == "image" and app["answers"].get(str(q["id"])):
            value += " (angehängt)"
        embed.add_field(name=f"{n + 1}. {q['label']}"[:256], value=value[:1024], inline=False)
    return embed


class DecisionButton(discord.ui.DynamicItem[discord.ui.Button], template=r"gv:(?P<action>acc|rej):(?P<app>\d+)"):
    """Akzeptieren/Ablehnen. Die Antrags-ID steckt im Button, daher funktioniert er auch nach einem Neustart."""

    def __init__(self, action: str, app_id: str, disabled: bool = False):
        accept = action == "acc"
        super().__init__(discord.ui.Button(
            label="Akzeptieren" if accept else "Ablehnen",
            style=discord.ButtonStyle.success if accept else discord.ButtonStyle.danger,
            emoji="✅" if accept else "❌",
            custom_id=f"gv:{action}:{app_id}",
            disabled=disabled,
        ))
        self.action = action
        self.app_id = app_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], match["app"])

    async def callback(self, interaction: discord.Interaction):
        d = data.get(interaction.guild_id)
        app = d["applications"].get(self.app_id)
        if not is_mod(interaction, d):
            return await interaction.response.send_message("Dafür fehlt dir die Berechtigung.", ephemeral=True)
        if not app or app["status"] != "pending":
            return await interaction.response.send_message("Dieser Antrag wurde schon bearbeitet.", ephemeral=True)
        if self.action == "acc":
            await accept(interaction, d, app)
        else:
            await interaction.response.send_modal(RejectModal(app["id"]))


def decision_view(app_id: str, disabled: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(DecisionButton("acc", app_id, disabled))
    view.add_item(DecisionButton("rej", app_id, disabled))
    return view


async def post_application(client: discord.Client, channel: discord.abc.GuildChannel, d: dict, app: dict,
                           files: list[discord.File]):
    embed = await build_summary(client, channel.guild, d, app, Colors.wait, "⏳ Wartet auf Prüfung", number_preview(d, app))
    view = decision_view(app["id"])
    try:
        msg = await channel.send(embed=embed, view=view, files=files)
    except discord.HTTPException:
        if not files:
            raise
        msg = await channel.send(content="⚠️ Screenshots konnten nicht angehängt werden.", embed=embed, view=view)
    app["mod_channel_id"] = channel.id
    app["mod_message_id"] = msg.id
    storage.save()


async def update_mod_message(guild: discord.Guild, app: dict, embed: discord.Embed):
    channel = guild.get_channel(app.get("mod_channel_id") or 0)
    if channel is None:
        return
    try:
        await channel.get_partial_message(app["mod_message_id"]).edit(embed=embed, view=decision_view(app["id"], disabled=True))
    except discord.HTTPException as e:
        log.warning("Mod-Nachricht nicht aktualisiert: %s", e)


async def notify(interaction: discord.Interaction, user_id: int, embed: discord.Embed) -> str | None:
    """Schickt dem User eine Direktnachricht. Gibt eine Warnung zurück, falls das nicht ging."""
    try:
        user = await get_user(interaction.client, interaction.guild, user_id)
        await user.send(embed=embed)
    except discord.HTTPException:
        return "User konnte nicht per DM benachrichtigt werden (DMs geschlossen)."
    return None


# ---------- Akzeptieren ----------

async def accept(interaction: discord.Interaction, d: dict, app: dict):
    await interaction.response.defer(ephemeral=True, thinking=True)
    s = d["settings"]
    uid = int(app["user_id"])

    wanted = answer_of(app, "number")
    number = None
    if any(q["purpose"] == "number" for q in app["questions"]):
        number = drivers.next_free_number(d, wanted, app["user_id"])
        if number is None:
            return await interaction.followup.send(
                f"❌ Im Bereich {s['number_min']}–{s['number_max']} ist keine Nummer mehr frei. "
                "Bereich mit `/verify setup` erweitern.", ephemeral=True)

    member = interaction.guild.get_member(uid)
    driver = {
        "number": number,
        "name": answer_of(app, "name") or (member.name if member else None),
        "flag": answer_of(app, "flag"),
        "platform": answer_of(app, "platform"),
        "verified_at": int(time.time()),
        "verified_by": str(interaction.user.id),
    }

    # sofort speichern, damit die Nummer belegt ist
    app["status"] = "accepted"
    app["decided_by"] = str(interaction.user.id)
    d["drivers"][app["user_id"]] = driver
    storage.save()

    warnings = []
    if member is None:
        warnings.append("User ist nicht mehr auf dem Server.")
    else:
        if w := await drivers.set_nickname(member, s["nickname_template"], driver):
            warnings.append(w)
        for role_id, add in ((s["verified_role_id"], True), (s["remove_role_id"], False)):
            role = interaction.guild.get_role(role_id or 0)
            if role is None:
                continue
            try:
                await (member.add_roles if add else member.remove_roles)(role, reason="GFSL Verify")
            except discord.HTTPException as e:
                warnings.append(f"Rolle {role.mention} nicht {'vergeben' if add else 'entfernt'}: {e.text or e}")

    number_line = f"**Fahrernummer:** #{number}" if number is not None else None
    embed = await build_summary(interaction.client, interaction.guild, d, app, Colors.ok, f"✅ Verifiziert von {interaction.user.mention}", number_line)
    await update_mod_message(interaction.guild, app, embed)

    text = f"🎉 **Du wurdest auf {interaction.guild.name} verifiziert!** Willkommen in der GFSL."
    if number is not None:
        text += f"\nDeine Fahrernummer: **#{number}**"
        if isinstance(wanted, int) and wanted != number:
            text += f" (deine Wunschnummer #{wanted} war inzwischen vergeben)"
    if w := await notify(interaction, uid, discord.Embed(color=Colors.ok, description=text)):
        warnings.append(w)

    try:
        await drivers.refresh_driver_list(interaction.guild, d)
    except (discord.HTTPException, RuntimeError) as e:
        warnings.append(f"Fahrerliste: {e}")

    msg = f"✅ <@{uid}> verifiziert" + (f" mit **#{number}**" if number is not None else "") + "."
    if warnings:
        msg += "\n\n" + "\n".join(f"⚠️ {w}" for w in warnings)
    await interaction.followup.send(msg, ephemeral=True)


# ---------- Ablehnen ----------

class RejectModal(discord.ui.Modal, title="Verifizierung ablehnen"):
    reason = discord.ui.TextInput(label="Grund (wird dem User angezeigt)", style=discord.TextStyle.long,
                                  required=False, max_length=500)

    def __init__(self, app_id: str):
        super().__init__()
        self.app_id = app_id

    async def on_submit(self, interaction: discord.Interaction):
        d = data.get(interaction.guild_id)
        app = d["applications"].get(self.app_id)
        if not app or app["status"] != "pending":
            return await interaction.response.send_message("Dieser Antrag wurde schon bearbeitet.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)

        reason = self.reason.value.strip() or None
        app["status"] = "rejected"
        app["decided_by"] = str(interaction.user.id)
        app["reason"] = reason
        storage.save()

        status = f"❌ Abgelehnt von {interaction.user.mention}" + (f": {reason}" if reason else "")
        embed = await build_summary(interaction.client, interaction.guild, d, app, Colors.bad, status, None)
        await update_mod_message(interaction.guild, app, embed)

        text = f"❌ Deine Verifizierung auf {interaction.guild.name} wurde abgelehnt."
        if reason:
            text += f"\n**Grund:** {reason}"
        text += "\nDu kannst es über den Button erneut versuchen."
        warning = await notify(interaction, int(app["user_id"]), discord.Embed(color=Colors.bad, description=text))
        await interaction.followup.send("Antrag abgelehnt." + (f"\n⚠️ {warning}" if warning else ""), ephemeral=True)
