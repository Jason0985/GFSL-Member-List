"""Freigabe vor dem Posten: DM mit Vorschau an den Admin, Bearbeiten-Dialog, Freigeben, Aussetzen."""
import logging

import discord

from gfsl import storage
from gfsl.permissions import is_admin_member

from . import actions, render
from . import data as D
from . import timeutil as tu

log = logging.getLogger("gfsl.events")


def reminder_user_id(d: dict, ev: dict) -> int | None:
    uid = D.template_of(d, ev)["reminder_user_id"] or d["settings"]["reminder_user_id"]
    return int(uid) if uid else None


async def send_dm(client: discord.Client, d: dict, ev: dict, guild: discord.Guild, text: str, with_buttons: bool) -> bool:
    uid = reminder_user_id(d, ev)
    if not uid:
        log.warning("Event #%s: niemand für die Freigabe eingetragen", ev["id"])
        return False
    try:
        user = guild.get_member(uid) or await client.fetch_user(uid)
        kwargs = {"content": text, "embed": render.event_embed(guild, d, ev)}
        if with_buttons:
            kwargs["view"] = review_view(guild.id, ev["id"])
        if file := D.image_file(D.field(d, ev, "image")):
            kwargs["file"] = file
        await user.send(**kwargs)
        return True
    except discord.HTTPException as e:
        log.warning("Freigabe-DM für Event #%s nicht zugestellt: %s", ev["id"], e)
        return False


async def send_review(client: discord.Client, guild: discord.Guild, d: dict, ev: dict):
    post_at = render.times(d, ev)["post"]
    text = (f"📝 **Freigabe nötig – {guild.name}**\nDie Anmeldung unten soll <t:{tu.ts(post_at)}:R> "
            f"(<t:{tu.ts(post_at)}:f>) gepostet werden. Bitte Titel, Beschreibung, Datum und Bild prüfen.\n"
            "**Ohne Freigabe wird sie nicht gepostet.**")
    ev["review_sent"] = True  # auch bei geschlossenen DMs nicht jede Minute neu versuchen (steht im Log)
    storage.save()
    await send_dm(client, d, ev, guild, text, with_buttons=True)


async def send_overdue(client: discord.Client, guild: discord.Guild, d: dict, ev: dict):
    ev["overdue_sent"] = True
    storage.save()
    await send_dm(client, d, ev, guild, "⚠️ **Noch nicht gepostet:** Die Anmeldung wartet auf deine Freigabe. "
                                       "Sobald du sie freigibst, wird sie sofort gepostet.", with_buttons=True)


# ---------- Buttons in der DM ----------

class ReviewButton(discord.ui.DynamicItem[discord.ui.Button], template=r"evr:(?P<action>edit|ok|skip):(?P<guild>\d+):(?P<ev>\d+)"):
    LABELS = {"edit": ("Bearbeiten & freigeben", discord.ButtonStyle.primary, "✏️"),
              "ok": ("So freigeben", discord.ButtonStyle.success, "✅"),
              "skip": ("Diese Woche aussetzen", discord.ButtonStyle.secondary, "⏭️")}

    def __init__(self, action: str, guild_id: int, ev_id: str):
        label, style, emoji = self.LABELS[action]
        super().__init__(discord.ui.Button(label=label, style=style, emoji=emoji, custom_id=f"evr:{action}:{guild_id}:{ev_id}"))
        self.action, self.guild_id, self.ev_id = action, int(guild_id), ev_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], match["guild"], match["ev"])

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.client.get_guild(self.guild_id)
        if guild is None or not is_admin_member(guild.get_member(interaction.user.id)):
            return await interaction.response.send_message("⛔ Dafür brauchst du die Admin-Rolle auf dem Server.", ephemeral=True)
        d = D.get(self.guild_id)
        ev = d["events"].get(self.ev_id)
        if ev is None or ev["status"] != "planned":
            return await interaction.response.send_message(
                f"Dieses Event ist schon {D.STATUS_LABEL.get(ev['status'], 'erledigt') if ev else 'gelöscht'}.", ephemeral=True)
        if self.action == "edit":
            return await interaction.response.send_modal(EventEditModal(guild, ev["id"]))
        if self.action == "skip":
            ev["status"] = "skipped"
            storage.save()
            return await interaction.response.send_message(f"⏭️ **{D.field(d, ev, 'title')}** wird nicht gepostet.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(await confirm(guild, d, ev), ephemeral=True)


def review_view(guild_id: int, ev_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in ("edit", "ok", "skip"):
        view.add_item(ReviewButton(action, guild_id, ev_id))
    return view


async def confirm(guild: discord.Guild, d: dict, ev: dict) -> str:
    """Freigeben; ist der Post-Zeitpunkt schon vorbei, sofort posten."""
    ev["confirmed"] = True
    storage.save()
    t = render.times(d, ev)
    if tu.now() >= t["start"]:
        return "⚠️ Das Event hat schon begonnen, es wird nicht mehr gepostet."
    if tu.now() >= t["post"]:
        return await actions.post(guild, d, ev)
    return f"✅ Freigegeben. Wird <t:{tu.ts(t['post'])}:f> gepostet."


# ---------- Bearbeiten ----------

class EventEditModal(discord.ui.Modal):
    """Titel, Beschreibung, Datum und Bild eines Events. Speichern = Freigabe (bei geplanten Events)."""

    def __init__(self, guild: discord.Guild, ev_id: str):
        d = D.get(guild.id)
        ev = d["events"][ev_id]
        super().__init__(title=f"Event #{ev_id} bearbeiten", timeout=1800)
        self.guild = guild
        self.ev_id = ev_id
        self.title_input = discord.ui.TextInput(max_length=200, default=D.field(d, ev, "title"))
        self.desc = discord.ui.TextInput(style=discord.TextStyle.long, required=False, max_length=3000,
                                         default=D.field(d, ev, "description") or None)
        self.when = discord.ui.TextInput(max_length=20, default=tu.from_str(ev["starts_at"]).strftime("%d.%m.%Y %H:%M"))
        self.image = discord.ui.FileUpload(required=False, min_values=0, max_values=1)
        self.add_item(discord.ui.Label(text="Titel (z. B. Strecke)", component=self.title_input))
        self.add_item(discord.ui.Label(text="Beschreibung", description="Leer = Text aus der Vorlage", component=self.desc))
        self.add_item(discord.ui.Label(text="Datum & Uhrzeit", description="z. B. 16.10.2026 20:00", component=self.when))
        self.add_item(discord.ui.Label(text="Neues Bild (optional)", description="Leer lassen = Bild bleibt", component=self.image))

    async def on_submit(self, interaction: discord.Interaction):
        d = D.get(self.guild.id)
        ev = d["events"].get(self.ev_id)
        if ev is None:
            return await interaction.response.send_message("Dieses Event gibt es nicht mehr.", ephemeral=True)
        try:
            starts_at = tu.parse_datetime(self.when.value)
        except ValueError as e:
            return await interaction.response.send_message(f"❌ {e}", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)

        template = D.template_of(d, ev)
        ev["title"] = self.title_input.value.strip()
        desc = self.desc.value.strip()
        ev["description"] = desc if desc and desc != template["description"] else None
        moved = starts_at != ev["starts_at"]
        ev["starts_at"] = starts_at
        new_image = False
        if self.image.values:
            try:
                ev["image"] = await D.save_image(self.image.values[0], f"event{ev['id']}")
                new_image = True
            except ValueError as e:
                return await interaction.followup.send(f"❌ {e}", ephemeral=True)
        storage.save()

        if ev["status"] == "planned":
            msg = await confirm(self.guild, d, ev)
        else:
            await actions.update_message(self.guild, d, ev, new_image=new_image)
            msg = "✅ Gespeichert und Anmeldung aktualisiert."
        if moved:
            msg += f"\n📅 Neuer Termin: {tu.pretty(starts_at)}"
        await interaction.followup.send(msg, ephemeral=True)
