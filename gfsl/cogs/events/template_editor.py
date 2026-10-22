"""/vorlage bearbeiten: alle Einstellungen einer Vorlage in einer privaten Nachricht."""
from datetime import timedelta

import discord

from gfsl import storage

from . import data as D
from . import render
from . import timeutil as tu

TEXT_CHANNELS = [discord.ChannelType.text, discord.ChannelType.news]


def days_text(days: int, hhmm: str) -> str:
    when = "am Renntag" if days == 0 else ("1 Tag vorher" if days == 1 else f"{days} Tage vorher")
    return f"{when} um {hhmm}"


def preview_event(t: dict) -> dict:
    """Beispiel-Event für die Vorschau: nächste Woche zur Event-Uhrzeit."""
    day = (tu.now() + timedelta(days=7)).date()
    return {"id": "0", "template_id": t["id"], "title": None, "description": None, "image": None,
            "starts_at": f"{day.isoformat()} {t['event_time']}", "status": "posted", "signups": {}, "assignments": {}}


class TemplateEditor(discord.ui.View):
    def __init__(self, guild: discord.Guild, user_id: int, template_id: str):
        super().__init__(timeout=900)
        self.guild, self.user_id, self.tid = guild, user_id, template_id
        self.note: str | None = None

    @property
    def d(self) -> dict:
        return D.get(self.guild.id)

    @property
    def t(self) -> dict:
        return self.d["templates"][self.tid]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.tid not in self.d["templates"]:
            await interaction.response.edit_message(content="Diese Vorlage gibt es nicht mehr.", embed=None, view=None)
            return False
        return interaction.user.id == self.user_id

    async def refresh(self, interaction: discord.Interaction, note: str | None = None):
        self.note = note
        await interaction.response.edit_message(embed=self.render(), view=self)

    def render(self) -> discord.Embed:
        t = {**D.TEMPLATE_DEFAULTS, **self.t}
        self.clear_items()
        channel = self.guild.get_channel(t["channel_id"] or 0)
        pings = " ".join(f"<@&{r}>" for r in t["ping_role_ids"]) or "— (niemand, im Menü unten auswählen)"
        review = (f"an – DM {t['review_hours']} h vor dem Posten an <@{t['reminder_user_id']}>"
                  if t["review"] else "aus – wird automatisch gepostet")
        nudge = f"{t['nudge_hours']} h vor der Frist werden Fahrer ohne Rückmeldung angepingt" if t["nudge_hours"] else "aus"
        lines = [
            f"**Kanal:** {channel.mention if channel else '❌ nicht gesetzt'}",
            f"**Pingt:** {pings}",
            f"**Titel (Standard):** {t['title']}",
            f"**Beschreibung:** {(t['description'][:300] + ('…' if len(t['description']) > 300 else '')) or '—'}",
            f"**Bild:** {'✅' if t['image'] else '—'} (ändern mit `/vorlage bild`)",
            f"**Event-Uhrzeit:** {t['event_time']}",
            f"**Gepostet:** {days_text(t['post_days_before'], t['post_time'])}",
            f"**Anmeldefrist:** {days_text(t['deadline_days_before'], t['deadline_time'])} – danach Q-Sperre und Einteilung der Ersatzfahrer",
            f"**Freigabe vor dem Posten:** {review}",
            f"**Erinnerung an Fahrer:** {nudge}",
        ]
        embed = discord.Embed(color=0x3B82F6, title=f"📋 Vorlage „{t['name']}“",
                              description=(f"{self.note}\n\n" if self.note else "") + "\n".join(lines))
        self.note = None

        channel_select = discord.ui.ChannelSelect(channel_types=TEXT_CHANNELS, placeholder="Kanal für die Anmeldung …", row=0,
                                                  default_values=[discord.Object(t["channel_id"])] if t["channel_id"] else [])

        async def on_channel(i):
            self.t["channel_id"] = channel_select.values[0].id
            storage.save()
            await self.refresh(i, "✅ Gespeichert.")
        channel_select.callback = on_channel
        self.add_item(channel_select)

        roles = discord.ui.RoleSelect(placeholder="Rollen, die beim Posten gepingt werden …", row=1, min_values=0, max_values=10,
                                      default_values=[discord.Object(r) for r in t["ping_role_ids"]])

        async def on_roles(i):
            self.t["ping_role_ids"] = [r.id for r in roles.values]
            storage.save()
            await self.refresh(i, "✅ Gespeichert.")
        roles.callback = on_roles
        self.add_item(roles)

        reviewer = discord.ui.UserSelect(placeholder="Freigabe-DM bekommt …", row=2, min_values=1, max_values=1,
                                         default_values=[discord.Object(t["reminder_user_id"])] if t["reminder_user_id"] else [])

        async def on_reviewer(i):
            self.t["reminder_user_id"] = reviewer.values[0].id
            storage.save()
            await self.refresh(i, "✅ Gespeichert.")
        reviewer.callback = on_reviewer
        self.add_item(reviewer)

        for label, emoji, callback in (("Texte & Zeiten", "✏️", self.open_texts), ("Freigabe & Erinnerungen", "⏰", self.open_reminders),
                                       ("Vorschau", "👁️", self.preview)):
            b = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.primary if emoji == "✏️" else discord.ButtonStyle.secondary, row=3)
            b.callback = callback
            self.add_item(b)
        return embed

    async def open_texts(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TextsModal(self))

    async def open_reminders(self, interaction: discord.Interaction):
        await interaction.response.send_modal(RemindersModal(self))

    async def preview(self, interaction: discord.Interaction):
        ev = preview_event(self.t)
        kwargs = {"embed": render.event_embed(self.guild, self.d, ev), "ephemeral": True,
                  "content": "👁️ So sieht eine Anmeldung mit dieser Vorlage aus (Titel kommt pro Rennen aus dem Kalender):"}
        if file := D.image_file(self.t["image"]):
            kwargs["file"] = file
        await interaction.response.send_message(**kwargs)


class TextsModal(discord.ui.Modal, title="Texte & Zeiten"):
    def __init__(self, editor: TemplateEditor):
        super().__init__()
        self.editor = editor
        t = {**D.TEMPLATE_DEFAULTS, **editor.t}
        self.title_input = discord.ui.TextInput(label="Standard-Titel (pro Rennen änderbar)", default=t["title"], max_length=200)
        self.desc = discord.ui.TextInput(label="Beschreibung (bei jedem Rennen gleich)", style=discord.TextStyle.long,
                                         default=t["description"] or None, required=False, max_length=3000)
        self.time = discord.ui.TextInput(label="Uhrzeit des Events", default=t["event_time"], max_length=5)
        self.post = discord.ui.TextInput(label="Posten: Tage vorher + Uhrzeit", default=f"{t['post_days_before']} {t['post_time']}",
                                         max_length=10, placeholder="3 10:00")
        self.deadline = discord.ui.TextInput(label="Anmeldefrist: Tage vorher + Uhrzeit", max_length=10, placeholder="0 15:00",
                                             default=f"{t['deadline_days_before']} {t['deadline_time']}")
        for item in (self.title_input, self.desc, self.time, self.post, self.deadline):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            event_time = tu.parse_hhmm(self.time.value)
            post_days, post_time = tu.parse_offset(self.post.value)
            dl_days, dl_time = tu.parse_offset(self.deadline.value)
        except ValueError as e:
            return await self.editor.refresh(interaction, f"❌ {e}")
        after_post = dl_days < post_days or (dl_days == post_days and dl_time > post_time)
        before_start = dl_days > 0 or dl_time < event_time
        if not (after_post and before_start):
            return await self.editor.refresh(interaction, "❌ Die Anmeldefrist muss nach dem Posten und vor dem Event liegen.")
        self.editor.t.update(title=self.title_input.value.strip(), description=self.desc.value.strip(), event_time=event_time,
                             post_days_before=post_days, post_time=post_time, deadline_days_before=dl_days, deadline_time=dl_time)
        storage.save()
        await self.editor.refresh(interaction, "✅ Gespeichert. Gilt auch für schon geplante Rennen dieser Vorlage.")


class RemindersModal(discord.ui.Modal, title="Freigabe & Erinnerungen"):
    def __init__(self, editor: TemplateEditor):
        super().__init__()
        self.editor = editor
        t = {**D.TEMPLATE_DEFAULTS, **editor.t}
        self.review = discord.ui.RadioGroup(options=[
            discord.RadioGroupOption(label="Ja – erst nach meiner Freigabe posten", value="1", default=t["review"]),
            discord.RadioGroupOption(label="Nein – automatisch posten", value="0", default=not t["review"]),
        ])
        self.review_hours = discord.ui.TextInput(default=str(t["review_hours"]), max_length=3)
        self.nudge_hours = discord.ui.TextInput(default=str(t["nudge_hours"]), max_length=3)
        self.add_item(discord.ui.Label(text="Freigabe per DM vor dem Posten?", component=self.review))
        self.add_item(discord.ui.Label(text="DM so viele Stunden vor dem Posten", component=self.review_hours))
        self.add_item(discord.ui.Label(text="Fahrer ohne Rückmeldung anpingen", description="Stunden vor der Anmeldefrist, 0 = aus",
                                       component=self.nudge_hours))

    async def on_submit(self, interaction: discord.Interaction):
        if not (self.review_hours.value.strip().isdigit() and self.nudge_hours.value.strip().isdigit()):
            return await self.editor.refresh(interaction, "❌ Stunden bitte als ganze Zahl eingeben.")
        self.editor.t.update(review=self.review.value != "0", review_hours=int(self.review_hours.value),
                             nudge_hours=int(self.nudge_hours.value))
        storage.save()
        await self.editor.refresh(interaction, "✅ Gespeichert.")
