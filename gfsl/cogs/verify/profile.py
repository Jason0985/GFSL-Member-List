"""Profil für schon verifizierte Fahrer: Flagge und Plattform selbst nachtragen oder ändern.

Öffnet sich mit /profil oder wenn ein verifizierter Fahrer auf den Start-Button klickt. Die Auswahl-Optionen
kommen aus den Verify-Fragen mit dem Feld „Flagge“ bzw. „Plattform“. Die passenden Rollen (/fragen rollen)
werden direkt angepasst.
"""
import discord

from gfsl import storage

from . import data, drivers, option_roles
from . import questions as Q
from .flow import Session, build_input, read_input, report_error


def profile_questions(d: dict, own: bool = True) -> list[dict]:
    """Flagge immer (notfalls mit der Standard-Flaggenliste), Plattform nur, wenn es die Frage gibt."""
    result = []
    for key, label, fallback_type in (("flag", "Flagge", "flag"), ("platform", "Plattform", None)):
        label = f"Deine {label}" if own else label
        q = next((q for q in d["questions"] if q["purpose"] == key), None)
        if q is None and fallback_type is None:
            continue
        q = dict(q) if q else Q.new_question(-1, fallback_type, label, purpose=key)
        q.update(label=label, hint="Leer lassen = bleibt wie bisher", required=False)
        result.append(q)
    return result


class ProfileModal(discord.ui.Modal):
    """by_admin=False: der Fahrer selbst (/profil). by_admin=True: ein Admin bearbeitet einen Fahrer (/fahrer bearbeiten),
    dann zusätzlich mit Nummer und Name."""

    def __init__(self, guild: discord.Guild, user_id: int, by_admin: bool = False):
        d = data.get(guild.id)
        drv = d["drivers"].get(str(user_id), {})
        title = f"Fahrer bearbeiten: {drv.get('name') or user_id}" if by_admin else "Flagge & Plattform"
        super().__init__(title=title[:45], timeout=900)
        self.guild_id = guild.id
        self.user_id = user_id
        self.by_admin = by_admin
        self.inputs: list[tuple[dict, discord.ui.Item]] = []
        self.session = Session(guild.id, user_id, profile_questions(d, own=not by_admin))
        for q in self.session.questions:
            current = drivers.profile_value(d, guild, str(user_id), q["purpose"])
            if q["type"] in ("select", "flag"):
                current = option_roles.match_option(q, current)
            if current is not None:
                self.session.answers[str(q["id"])] = current
                self.session.raw[str(q["id"])] = str(current)
            item = build_input(q, d["settings"], self.session)
            self.add_item(discord.ui.Label(text=q["label"], description=q["hint"], component=item))
            self.inputs.append((q, item))

        self.number = self.name = None
        if by_admin:
            self.number = discord.ui.TextInput(required=False, max_length=3,
                                               default=str(drv["number"]) if isinstance(drv.get("number"), int) else None)
            self.name = discord.ui.TextInput(required=False, max_length=32, default=drv.get("name"))
            self.add_item(discord.ui.Label(text="Fahrernummer", description="Leer lassen = bleibt wie bisher", component=self.number))
            self.add_item(discord.ui.Label(text="Ingame-Name (für den Nickname)", component=self.name))

    async def on_submit(self, interaction: discord.Interaction):
        d = data.get(self.guild_id)
        uid = str(self.user_id)
        drv = d["drivers"].get(uid)
        if drv is None:
            text = "Dieser User ist nicht in der Fahrerliste." if self.by_admin else "Du bist (noch) nicht verifiziert."
            return await interaction.response.send_message(text, ephemeral=True)

        errors, changes = [], {}
        for q, item in self.inputs:
            self.session.answers.pop(str(q["id"]), None)
            if err := read_input(q, item, d["settings"], self.session):
                errors.append(f"**{q['label']}:** {err}")
            elif (value := self.session.answers.get(str(q["id"]))) is not None:
                changes[q["purpose"]] = value
        if self.number is not None and (raw := self.number.value.strip().lstrip("#")):
            if not raw.isdigit():
                errors.append("**Fahrernummer:** Bitte nur eine Zahl eingeben.")
            elif owner := drivers.owner_of(d, int(raw), uid):
                errors.append(f"**Fahrernummer:** #{raw} ist schon an <@{owner}> vergeben.")
            else:
                changes["number"] = int(raw)
        if self.name is not None and self.name.value.strip():
            changes["name"] = self.name.value.strip()
        if errors:
            return await interaction.response.send_message("⚠️ " + "\n".join(errors), ephemeral=True)
        drv.update(changes)
        storage.save()
        await interaction.response.defer(ephemeral=True, thinking=True)

        warnings = []
        member = interaction.guild.get_member(self.user_id)
        if member is None:
            warnings.append("User ist nicht auf dem Server, Rollen und Nickname wurden nicht angepasst.")
        else:
            # Plattform-/Flaggen-Rolle passend setzen, die Rolle der alten Auswahl entfernen
            report = option_roles.SyncReport()
            await option_roles.sync_member(member, d, option_roles.mapped_questions(d["questions"]), report, remove_others=True)
            warnings += list(dict.fromkeys(report.errors))
            template = d["settings"]["nickname_template"]
            if self.by_admin or "{flagge}" in template or "{plattform}" in template:
                if w := await drivers.set_nickname(member, template, drv):
                    warnings.append(w)
        try:
            await drivers.refresh_driver_list(interaction.guild, d)
        except (discord.HTTPException, RuntimeError) as e:
            warnings.append(f"Fahrerliste: {e}")

        flag = drivers.profile_value(d, interaction.guild, uid, "flag")
        platform = drivers.profile_value(d, interaction.guild, uid, "platform")
        msg = f"✅ Gespeichert: {drivers.driver_line(uid, drv, d, interaction.guild)}"
        if not flag or not platform:
            msg += "\nℹ️ Es fehlt noch: " + ", ".join(x for x, v in (("Flagge", flag), ("Plattform", platform)) if not v)
        if warnings:
            msg += "\n" + "\n".join(f"⚠️ {w}" for w in warnings)
        await interaction.followup.send(msg, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await report_error(interaction, error)
