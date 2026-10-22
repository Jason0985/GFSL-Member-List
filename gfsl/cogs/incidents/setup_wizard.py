"""/rennvorfall setup: Einrichtung Schritt für Schritt in einer privaten Nachricht."""
import discord

from gfsl import storage

from . import data as D
from .ticket import PanelView, panel_embed

TEXT = [discord.ChannelType.text, discord.ChannelType.news]

STEPS = [
    ("panel_channel", "Melde-Kanal"),
    ("panel_text", "Text & Button"),
    ("category", "Ticket-Kategorie"),
    ("staff", "Ligaleitung & Stewards"),
    ("result", "Ergebnis-Kanal"),
    ("archive", "Archiv"),
    ("finish", "Panel posten"),
]

PERMS_SEND = [("view_channel", "Kanal ansehen"), ("send_messages", "Nachrichten senden"), ("embed_links", "Links einbetten")]
PERMS = {
    "panel_channel_id": PERMS_SEND,
    "category_id": [("view_channel", "Kanal ansehen"), ("manage_channels", "Kanäle verwalten"),
                    ("manage_roles", "Berechtigungen verwalten"), ("send_messages", "Nachrichten senden")],
    "result_channel_id": PERMS_SEND + [("attach_files", "Dateien anhängen")],
    "archive_channel_id": PERMS_SEND + [("attach_files", "Dateien anhängen"), ("create_public_threads", "Öffentliche Threads erstellen"),
                                        ("send_messages_in_threads", "Nachrichten in Threads senden"), ("manage_threads", "Threads verwalten")],
}


def channel_status(guild: discord.Guild, s: dict, key: str) -> str:
    ch = guild.get_channel(s[key] or 0)
    if ch is None:
        return "❌ nicht gesetzt"
    p = ch.permissions_for(guild.me)
    missing = [label for attr, label in PERMS[key] if not getattr(p, attr)]
    return ch.mention + (f"\n⚠️ Mir fehlen dort die Rechte: {', '.join(missing)}" if missing else "")


def overview(guild: discord.Guild, s: dict) -> str:
    roles = " ".join(f"<@&{r}>" for r in s["staff_role_ids"]) or "❌ nicht gesetzt"
    return "\n".join([
        f"**Melde-Kanal:** {channel_status(guild, s, 'panel_channel_id')}",
        f"**Ticket-Kategorie:** {channel_status(guild, s, 'category_id')}",
        f"**Ligaleitung & Stewards:** {roles}",
        f"**Ergebnis-Kanal:** {channel_status(guild, s, 'result_channel_id')}",
        f"**Archiv:** {channel_status(guild, s, 'archive_channel_id')}",
    ])


async def post_panel(guild: discord.Guild, s: dict) -> str:
    channel = guild.get_channel(s["panel_channel_id"] or 0)
    if channel is None:
        return "❌ Erst einen Melde-Kanal wählen."
    embed, view = panel_embed(s), PanelView(s["panel_button"])
    old_channel = guild.get_channel(s["panel_message_channel_id"] or 0)
    if old_channel is not None and s["panel_message_id"]:
        old = old_channel.get_partial_message(s["panel_message_id"])
        try:
            if old_channel.id == channel.id:
                await old.edit(embed=embed, view=view)
                return f"✅ Panel in {channel.mention} aktualisiert."
            await old.delete()
        except discord.HTTPException:
            pass
    msg = await channel.send(embed=embed, view=view)
    s.update(panel_message_channel_id=channel.id, panel_message_id=msg.id)
    storage.save()
    return f"✅ Panel in {channel.mention} gepostet."


class IncidentSetup(discord.ui.View):
    def __init__(self, guild: discord.Guild, user_id: int, step: int = 0, channel=None):
        super().__init__(timeout=900)
        self.guild, self.user_id, self.step, self.channel = guild, user_id, step, channel
        self.note: str | None = None

    @property
    def s(self) -> dict:
        return D.get(self.guild.id)["settings"]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def refresh(self, interaction: discord.Interaction, note: str | None = None):
        self.note = note
        await interaction.response.edit_message(embed=self.render(), view=self)

    def button(self, label, style, callback, emoji=None, disabled=False, row=None):
        b = discord.ui.Button(label=label, style=style, emoji=emoji, disabled=disabled, row=row)
        b.callback = callback
        self.add_item(b)

    def channel_select(self, key: str, placeholder: str, types: list[discord.ChannelType]):
        current = self.s[key]
        select = discord.ui.ChannelSelect(channel_types=types, placeholder=placeholder, row=0,
                                          default_values=[discord.Object(current)] if current else [])

        async def picked(i):
            self.s[key] = select.values[0].id
            storage.save()
            await self.refresh(i, "✅ Gespeichert.")
        select.callback = picked
        self.add_item(select)
        if self.channel is not None and self.channel.type in types and self.channel.id != current:
            async def here(i):
                self.s[key] = self.channel.id
                storage.save()
                await self.refresh(i, "✅ Gespeichert.")
            self.button(f"Diesen Kanal nehmen: #{self.channel.name}"[:80], discord.ButtonStyle.secondary, here, emoji="📍", row=1)

    def render(self) -> discord.Embed:
        self.clear_items()
        key, name = STEPS[self.step]
        embed = discord.Embed(color=0x3B82F6, title=f"⚖️ Rennvorfall-Einrichtung · Schritt {self.step + 1}/{len(STEPS)}: {name}")
        s = self.s
        if key == "panel_channel":
            embed.description = ("In welchem Kanal steht der Button **„Vorfall melden“**? Er öffnet ein privates Ticket, das Formular füllen die Fahrer dort aus.\n\n"
                                 f"**Aktuell:** {channel_status(self.guild, s, 'panel_channel_id')}")
            self.channel_select("panel_channel_id", "Melde-Kanal wählen …", TEXT)
        elif key == "panel_text":
            embed.description = ("So sieht das Panel aus. Mit **Bearbeiten** änderst du Titel, Text und Button.\n"
                                 "Der Button öffnet nur das Ticket – das Meldeformular füllen die Fahrer danach im Ticket aus.")
            embed.add_field(name="Titel", value=s["panel_title"], inline=False)
            embed.add_field(name="Text", value=s["panel_text"][:1024], inline=False)
            embed.add_field(name="Button", value=s["panel_button"], inline=False)

            async def edit(i):
                await i.response.send_modal(PanelModal(self))

            async def reset(i):
                s.update(panel_title=D.DEFAULT_TITLE, panel_text=D.DEFAULT_TEXT, panel_button="Vorfall melden")
                storage.save()
                await self.refresh(i, "↩️ Standardtext wiederhergestellt.")
            self.button("Bearbeiten", discord.ButtonStyle.primary, edit, emoji="✏️")
            self.button("Standardtext", discord.ButtonStyle.secondary, reset, emoji="↩️")
        elif key == "category":
            embed.description = ("In welcher **Kategorie** sollen die Ticket-Kanäle (`vorfall-0001`, …) entstehen? "
                                 "Am besten eine eigene Kategorie, die normale Mitglieder nicht sehen.\n\n"
                                 f"**Aktuell:** {channel_status(self.guild, s, 'category_id')}")
            self.channel_select("category_id", "Kategorie wählen …", [discord.ChannelType.category])
        elif key == "staff":
            roles = " ".join(f"<@&{r}>" for r in s["staff_role_ids"]) or "—"
            embed.description = ("Welche Rollen sehen **alle** Tickets und dürfen sie bearbeiten (Ligaleitung, FIA-Stewards)? "
                                 "Sie werden auch angepingt, wenn ein Fall bereit zur Bewertung ist.\n"
                                 "Ist jemand davon selbst beteiligt, sperrt ihn der Bot nach seinem Teil automatisch aus dem Ticket aus.\n\n"
                                 f"**Aktuell:** {roles}")
            select = discord.ui.RoleSelect(placeholder="Rollen wählen (mehrere möglich) …", min_values=0, max_values=10, row=0,
                                           default_values=[discord.Object(r) for r in s["staff_role_ids"]])

            async def picked(i):
                s["staff_role_ids"] = [r.id for r in select.values]
                storage.save()
                await self.refresh(i, "✅ Gespeichert.")
            select.callback = picked
            self.add_item(select)
        elif key == "result":
            embed.description = ("Wo wird die **FIA-Entscheidung** veröffentlicht? Einsender und Beschuldigte werden dort "
                                 "erwähnt, Videos und Links hängen dran.\n\n"
                                 f"**Aktuell:** {channel_status(self.guild, s, 'result_channel_id')}")
            self.channel_select("result_channel_id", "Ergebnis-Kanal wählen …", TEXT)
        elif key == "archive":
            embed.description = ("Wo werden abgeschlossene Fälle **archiviert**? Jeder Vorfall wird ein eigener Thread mit dem "
                                 "kompletten Verlauf (Uhrzeiten, Texte, Dateien) und einem Protokoll als Datei.\n"
                                 "Tipp: Ein **Forum-Kanal** ist dafür ideal (jeder Vorfall ein Beitrag). Nur für Ligaleitung/Stewards sichtbar machen.\n\n"
                                 f"**Aktuell:** {channel_status(self.guild, s, 'archive_channel_id')}")
            self.channel_select("archive_channel_id", "Archiv-Kanal wählen (Forum oder Text) …", [*TEXT, discord.ChannelType.forum])
        else:
            missing = [label for k, label in (("panel_channel_id", "Melde-Kanal"), ("category_id", "Kategorie"),
                                              ("result_channel_id", "Ergebnis-Kanal"), ("archive_channel_id", "Archiv")) if not s[k]]
            if not s["staff_role_ids"]:
                missing.append("Ligaleitung & Stewards")
            embed.description = overview(self.guild, s) + (
                f"\n\n❌ **Es fehlt noch:** {', '.join(missing)}" if missing else "\n\nAlles bereit – **Panel posten** stellt den Button in den Melde-Kanal.")

            async def publish(i):
                await i.response.defer()
                self.note = await post_panel(self.guild, s)
                await i.edit_original_response(embed=self.render(), view=self)
            self.button("Panel aktualisieren" if s["panel_message_id"] else "Panel posten", discord.ButtonStyle.success,
                        publish, emoji="📌", disabled=bool(missing))

        if key in ("panel_channel", "result", "archive"):
            embed.set_footer(text="Kanal fehlt im Menü? Namen ins Menü tippen – oder /rennvorfall setup im Kanal ausführen und „Diesen Kanal nehmen“ klicken.")
        if self.note:
            embed.description = f"{self.note}\n\n{embed.description}"
            self.note = None

        async def back(i):
            self.step -= 1
            await self.refresh(i)

        async def forward(i):
            self.step += 1
            await self.refresh(i)
        self.button("Zurück", discord.ButtonStyle.secondary, back, emoji="◀️", disabled=self.step == 0, row=4)
        if self.step < len(STEPS) - 1:
            self.button("Weiter", discord.ButtonStyle.primary, forward, emoji="▶️", row=4)
        return embed


class PanelModal(discord.ui.Modal, title="Melde-Panel bearbeiten"):
    def __init__(self, wizard: IncidentSetup):
        super().__init__()
        self.wizard = wizard
        s = wizard.s
        self.t = discord.ui.TextInput(label="Titel", default=s["panel_title"], max_length=200)
        self.text = discord.ui.TextInput(label="Text", style=discord.TextStyle.long, default=s["panel_text"], max_length=2500)
        self.button = discord.ui.TextInput(label="Beschriftung des Buttons", default=s["panel_button"], max_length=80)
        for item in (self.t, self.text, self.button):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        self.wizard.s.update(panel_title=self.t.value.strip(), panel_text=self.text.value.strip(),
                             panel_button=self.button.value.strip())
        storage.save()
        await self.wizard.refresh(interaction, "✅ Gespeichert. Ein schon gepostetes Panel änderst du im letzten Schritt.")
