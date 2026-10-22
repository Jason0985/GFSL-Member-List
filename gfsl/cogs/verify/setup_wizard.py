"""/verify setup: Einrichtung Schritt für Schritt in einer privaten Nachricht."""
import discord

from gfsl import storage

from . import data, drivers, flow
from . import questions as Q
from .constants import MAX_QUESTIONS, Colors
from .data import DEFAULT_PANEL_TEXT, DEFAULT_PANEL_TITLE

STEPS = [
    ("panel_channel", "Start-Kanal"),
    ("panel_text", "Start-Nachricht"),
    ("mod_channel", "Mod-Kanal"),
    ("list_channel", "Fahrerliste"),
    ("roles", "Rollen & Admin-Rolle"),
    ("numbers", "Nickname & Nummern"),
    ("questions", "Fragen"),
    ("finish", "Panel posten"),
]

TEXT_CHANNELS = [discord.ChannelType.text, discord.ChannelType.news]

PERMS_SEND = [("view_channel", "Kanal ansehen"), ("send_messages", "Nachrichten senden"), ("embed_links", "Links einbetten")]
PERMS_MOD = PERMS_SEND + [("attach_files", "Dateien anhängen")]
PERMS_LIST = PERMS_SEND + [("read_message_history", "Nachrichtenverlauf lesen")]


def channel_status(guild: discord.Guild, channel_id: int | None, perms) -> str:
    if not channel_id:
        return "❌ nicht gesetzt"
    ch = guild.get_channel(channel_id)
    if ch is None:
        return f"⚠️ Kanal `{channel_id}` nicht gefunden"
    p = ch.permissions_for(guild.me)
    missing = [label for attr, label in perms if not getattr(p, attr)]
    return ch.mention + (f"\n⚠️ Mir fehlen dort die Rechte: {', '.join(missing)}" if missing else "")


def role_status(guild: discord.Guild, role_id: int | None, assigned: bool = True) -> str:
    role = guild.get_role(role_id or 0)
    if role is None:
        return "—"
    if assigned and (role >= guild.me.top_role or role.managed):
        return f"{role.mention}\n⚠️ Diese Rolle steht über meiner Bot-Rolle, ich kann sie nicht vergeben. Bot-Rolle in den Servereinstellungen nach oben ziehen."
    return role.mention


def settings_overview(guild: discord.Guild, d: dict) -> str:
    s = d["settings"]
    example = drivers.render_nickname(s["nickname_template"], {"name": "Wenig-Luca", "number": 17, "flag": "Deutschland", "platform": "PlayStation"})
    return "\n".join([
        f"**Start-Kanal:** {channel_status(guild, s['panel_channel_id'], PERMS_SEND)}",
        f"**Mod-Kanal:** {channel_status(guild, s['mod_channel_id'], PERMS_MOD)}",
        f"**Fahrerliste:** {channel_status(guild, s['list_channel_id'], PERMS_LIST)}",
        f"**Rolle bei Freigabe:** {role_status(guild, s['verified_role_id'])}",
        f"**Rolle entfernen:** {role_status(guild, s['remove_role_id'])}",
        f"**Admin-Rolle (darf alles mit dem Bot):** {role_status(guild, s['mod_role_id'], assigned=False)} (+ alle mit „Server verwalten“)",
        f"**Nickname-Vorlage:** `{s['nickname_template']}` → z. B. „{example}“",
        f"**Nummern:** {s['number_min']}–{s['number_max']}",
        f"**Fragen:** {len(d['questions'])}",
    ])


async def post_panel(guild: discord.Guild, d: dict) -> str:
    """Postet das Panel oder aktualisiert das vorhandene. Gibt eine Meldung für den Admin zurück."""
    s = d["settings"]
    channel = guild.get_channel(s["panel_channel_id"] or 0)
    if channel is None:
        return "❌ Erst einen Start-Kanal wählen."
    embed = flow.panel_embed(s)
    view = flow.PanelView(s["panel_button"])

    old_channel = guild.get_channel(s["panel_message_channel_id"] or 0)
    if old_channel is not None and s["panel_message_id"]:
        old = old_channel.get_partial_message(s["panel_message_id"])
        try:
            if old_channel.id == channel.id:
                await old.edit(embed=embed, view=view)
                return f"✅ Panel in {channel.mention} aktualisiert."
            await old.delete()  # Kanal gewechselt -> altes Panel weg
        except discord.HTTPException:
            pass
    msg = await channel.send(embed=embed, view=view)
    s["panel_message_channel_id"] = channel.id
    s["panel_message_id"] = msg.id
    storage.save()
    return f"✅ Panel in {channel.mention} gepostet."


class SetupWizard(discord.ui.View):
    def __init__(self, guild: discord.Guild, user_id: int, step: int = 0, channel: discord.abc.GuildChannel | None = None):
        super().__init__(timeout=900)
        self.guild = guild
        self.channel = channel  # Kanal, in dem /verify setup ausgeführt wurde
        self.user_id = user_id
        self.step = step
        self.note: str | None = None
        self.confirm_defaults = False

    @property
    def d(self) -> dict:
        return data.get(self.guild.id)

    @property
    def s(self) -> dict:
        return self.d["settings"]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        await flow.report_error(interaction, error)

    async def refresh(self, interaction: discord.Interaction, note: str | None = None):
        self.note = note
        await interaction.response.edit_message(embed=self.render(), view=self)

    def save(self):
        storage.save()

    # ---------- Bausteine ----------

    def add_button(self, label, style, callback, emoji=None, disabled=False, row=None):
        b = discord.ui.Button(label=label, style=style, emoji=emoji, disabled=disabled, row=row)
        b.callback = callback
        self.add_item(b)

    def add_channel_select(self, key: str, placeholder: str, row: int = 0, on_change=None):
        current = self.s[key]
        select = discord.ui.ChannelSelect(channel_types=TEXT_CHANNELS, placeholder=placeholder, row=row,
                                          default_values=[discord.Object(current)] if current else [])

        async def callback(interaction: discord.Interaction):
            self.s[key] = select.values[0].id
            if on_change:
                on_change()
            self.save()
            await self.refresh(interaction, "✅ Gespeichert.")
        select.callback = callback
        self.add_item(select)

        # Falls der Kanal im Menü nicht auftaucht: /verify setup im gewünschten Kanal ausführen und hier klicken
        if self.channel is not None and self.channel.type in TEXT_CHANNELS and self.channel.id != current:
            async def take_here(interaction: discord.Interaction):
                self.s[key] = self.channel.id
                if on_change:
                    on_change()
                self.save()
                await self.refresh(interaction, "✅ Gespeichert.")
            self.add_button(f"Diesen Kanal nehmen: #{self.channel.name}"[:80], discord.ButtonStyle.secondary,
                            take_here, emoji="📍", row=row + 1)

    def add_role_select(self, key: str, placeholder: str, row: int):
        current = self.s[key]
        select = discord.ui.RoleSelect(placeholder=placeholder, row=row, min_values=0, max_values=1,
                                       default_values=[discord.Object(current)] if current else [])

        async def callback(interaction: discord.Interaction):
            self.s[key] = select.values[0].id if select.values else None
            self.save()
            await self.refresh(interaction, "✅ Gespeichert.")
        select.callback = callback
        self.add_item(select)

    # ---------- Darstellung ----------

    def render(self) -> discord.Embed:
        self.clear_items()
        key, name = STEPS[self.step]
        embed = discord.Embed(color=Colors.info, title=f"⚙️ Einrichtung · Schritt {self.step + 1}/{len(STEPS)}: {name}")
        getattr(self, f"step_{key}")(embed)
        if key in ("panel_channel", "mod_channel", "list_channel"):
            embed.set_footer(text="Kanal fehlt im Menü? Namen ins Menü tippen, dann sucht Discord. Oder /verify setup "
                                  "direkt im Kanal ausführen und „Diesen Kanal nehmen“ klicken.")
        if self.note:
            embed.description = f"{self.note}\n\n{embed.description}"
            self.note = None

        async def back(i):
            self.step -= 1
            self.confirm_defaults = False
            await self.refresh(i)

        async def forward(i):
            self.step += 1
            self.confirm_defaults = False
            await self.refresh(i)
        self.add_button("Zurück", discord.ButtonStyle.secondary, back, emoji="◀️", disabled=self.step == 0, row=4)
        if self.step < len(STEPS) - 1:
            self.add_button("Weiter", discord.ButtonStyle.primary, forward, emoji="▶️", row=4)
        return embed

    def step_panel_channel(self, embed: discord.Embed):
        embed.description = (
            "In welchem Kanal soll die **Start-Nachricht mit dem Verifizieren-Button** stehen?\n"
            "Hier brauchen neue User nur das Recht, den Kanal zu sehen.\n\n"
            f"**Aktuell:** {channel_status(self.guild, self.s['panel_channel_id'], PERMS_SEND)}"
        )
        self.add_channel_select("panel_channel_id", "Kanal für den Start-Button wählen …")

    def step_panel_text(self, embed: discord.Embed):
        s = self.s
        embed.description = "So sieht die Start-Nachricht aus. Mit **Text bearbeiten** änderst du Titel, Text und Button."
        embed.add_field(name="Titel", value=s["panel_title"], inline=False)
        embed.add_field(name="Text", value=s["panel_text"][:1024], inline=False)
        embed.add_field(name="Button", value=f"🏎️ {s['panel_button']}", inline=False)

        async def edit(i):
            await i.response.send_modal(PanelTextModal(self))

        async def reset(i):
            s.update(panel_title=DEFAULT_PANEL_TITLE, panel_text=DEFAULT_PANEL_TEXT, panel_button="Verifizierung starten")
            self.save()
            await self.refresh(i, "↩️ Standardtext wiederhergestellt.")
        self.add_button("Text bearbeiten", discord.ButtonStyle.primary, edit, emoji="✏️")
        self.add_button("Standardtext", discord.ButtonStyle.secondary, reset, emoji="↩️")

    def step_mod_channel(self, embed: discord.Embed):
        embed.description = (
            "In welchen Kanal sollen die **ausgefüllten Verifizierungen** mit Akzeptieren/Ablehnen kommen?\n"
            "Am besten ein Kanal, den nur Mods sehen.\n\n"
            f"**Aktuell:** {channel_status(self.guild, self.s['mod_channel_id'], PERMS_MOD)}"
        )
        self.add_channel_select("mod_channel_id", "Mod-Kanal wählen …")

    def step_list_channel(self, embed: discord.Embed):
        embed.description = (
            "Wo soll die **Fahrerliste** (Nummer, Flagge, @User, Plattform) stehen? Sie aktualisiert sich automatisch.\n"
            "Am besten ein eigener Kanal, in dem sonst niemand schreibt.\n\n"
            f"**Aktuell:** {channel_status(self.guild, self.s['list_channel_id'], PERMS_LIST)}"
        )

        def moved():
            self.s["list_message_ids"] = []  # im neuen Kanal neu posten
        self.add_channel_select("list_channel_id", "Kanal für die Fahrerliste wählen …", on_change=moved)

    def step_roles(self, embed: discord.Embed):
        s = self.s
        embed.description = (
            "**Beim Akzeptieren** einer Verifizierung (beide optional):\n"
            f"• **Rolle bei Freigabe:** {role_status(self.guild, s['verified_role_id'])}\n"
            f"• **Rolle entfernen** (z. B. Unverifiziert): {role_status(self.guild, s['remove_role_id'])}\n\n"
            "**Admin-Rolle zum Bearbeiten mit dem Bot:**\n"
            f"• {role_status(self.guild, s['mod_role_id'], assigned=False)}\n"
            "Wer diese Rolle hat, darf **alles** mit dem Bot: alle Befehle, Anträge akzeptieren/ablehnen, Fahrer bearbeiten. "
            "Alle anderen können nur sich selbst verifizieren und mit `/verify-profil` ihre Flagge/Plattform eintragen. "
            "Leute mit „Server verwalten“ dürfen immer alles, auch ohne diese Rolle.\n\n"
            "Zum Leeren die Auswahl im Menü entfernen. Rollen je Antwort (z. B. PlayStation → @PS-Fahrer) stellst du mit `/verify fragen rollen` ein."
        )
        self.add_role_select("verified_role_id", "Rolle bei Freigabe …", 0)
        self.add_role_select("remove_role_id", "Rolle, die entfernt wird …", 1)
        self.add_role_select("mod_role_id", "Admin-Rolle (darf alles mit dem Bot bearbeiten) …", 2)

    def step_numbers(self, embed: discord.Embed):
        s = self.s
        example = drivers.render_nickname(s["nickname_template"], {"name": "Wenig-Luca", "number": 17, "flag": "Deutschland", "platform": "PlayStation"})
        embed.description = (
            f"**Nickname-Vorlage:** `{s['nickname_template']}` → z. B. „{example}“\n"
            "Platzhalter: `{name}` `{nummer}` `{flagge}` `{plattform}` (max. 32 Zeichen)\n\n"
            f"**Fahrernummern:** {s['number_min']}–{s['number_max']}\n\n"
            "Beim Verifizieren können User aus allen freien Nummern im Bereich wählen."
        )

        async def edit(i):
            await i.response.send_modal(NumbersModal(self))
        self.add_button("Bearbeiten", discord.ButtonStyle.primary, edit, emoji="✏️")

    def step_questions(self, embed: discord.Embed):
        d = self.d
        qs = d["questions"]
        lines = [f"**{n + 1}.** {q['label']}" for n, q in enumerate(qs)]
        embed.description = (
            (("\n".join(lines)) if qs else "Noch keine Fragen.")
            + f"\n\n**{len(qs)}/{MAX_QUESTIONS} Fragen.** Fragen änderst du mit `/verify fragen neu`, `/verify fragen bearbeiten`, "
            "`/verify fragen verschieben` und `/verify fragen entfernen`, Rollen je Antwort (z. B. Plattform) mit `/verify fragen rollen`. Mit **Testlauf** gehst du die Verifizierung selbst durch, "
            "ohne dass etwas gespeichert wird."
        )

        async def load_defaults(i):
            if qs and not self.confirm_defaults:
                self.confirm_defaults = True
                return await self.refresh(i)
            self.confirm_defaults = False
            d["questions"], d["next_question_id"] = Q.default_questions(d["next_question_id"])
            self.save()
            await self.refresh(i, f"✅ {len(d['questions'])} Standardfragen geladen.")

        async def test(i):
            await flow.start(i, preview=True)

        if self.confirm_defaults:
            self.add_button("Wirklich alle Fragen ersetzen?", discord.ButtonStyle.danger, load_defaults, emoji="⚠️")
        else:
            self.add_button("Standardfragen laden", discord.ButtonStyle.secondary if qs else discord.ButtonStyle.primary,
                            load_defaults, emoji="📋")
        self.add_button("Testlauf", discord.ButtonStyle.secondary, test, emoji="🧪", disabled=not qs)

    def step_finish(self, embed: discord.Embed):
        s = self.s
        missing = []
        if not s["panel_channel_id"]:
            missing.append("Start-Kanal (Schritt 1)")
        if not s["mod_channel_id"]:
            missing.append("Mod-Kanal (Schritt 3)")
        if not self.d["questions"]:
            missing.append("Fragen (Schritt 7)")
        embed.description = settings_overview(self.guild, self.d)
        if missing:
            embed.description += "\n\n❌ **Es fehlt noch:** " + ", ".join(missing)
        else:
            embed.description += "\n\nAlles bereit! **Panel posten** stellt den Start-Button in den Start-Kanal " \
                                 "(ein vorhandenes Panel wird aktualisiert) und baut die Fahrerliste auf."

        async def publish(i: discord.Interaction):
            await i.response.defer()
            notes = [await post_panel(self.guild, self.d)]
            if s["list_channel_id"]:
                try:
                    await drivers.refresh_driver_list(self.guild, self.d)
                    notes.append("✅ Fahrerliste aktualisiert.")
                except (discord.HTTPException, RuntimeError) as e:
                    notes.append(f"⚠️ Fahrerliste: {e}")
            self.note = "\n".join(notes)
            await i.edit_original_response(embed=self.render(), view=self)

        label = "Panel aktualisieren" if s["panel_message_id"] else "Panel posten"
        self.add_button(label, discord.ButtonStyle.success, publish, emoji="📌", disabled=bool(missing))


class PanelTextModal(discord.ui.Modal, title="Start-Nachricht bearbeiten"):
    def __init__(self, wizard: SetupWizard):
        super().__init__()
        self.wizard = wizard
        s = wizard.s
        self.title_input = discord.ui.TextInput(label="Titel", default=s["panel_title"], max_length=256)
        self.text_input = discord.ui.TextInput(label="Text", style=discord.TextStyle.long, default=s["panel_text"], max_length=4000)
        self.button_input = discord.ui.TextInput(label="Beschriftung des Buttons", default=s["panel_button"], max_length=80)
        for item in (self.title_input, self.text_input, self.button_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        self.wizard.s.update(panel_title=self.title_input.value.strip(), panel_text=self.text_input.value.strip(),
                             panel_button=self.button_input.value.strip())
        self.wizard.save()
        await self.wizard.refresh(interaction, "✅ Gespeichert. Ein schon gepostetes Panel änderst du im letzten Schritt mit **Panel aktualisieren**.")


class NumbersModal(discord.ui.Modal, title="Nickname & Nummern"):
    def __init__(self, wizard: SetupWizard):
        super().__init__()
        self.wizard = wizard
        s = wizard.s
        self.nick = discord.ui.TextInput(label="Nickname-Vorlage", default=s["nickname_template"], max_length=60,
                                         placeholder="{name} oder #{nummer} | {name}")
        self.lo = discord.ui.TextInput(label="Kleinste Fahrernummer", default=str(s["number_min"]), max_length=3)
        self.hi = discord.ui.TextInput(label="Größte Fahrernummer", default=str(s["number_max"]), max_length=3)
        for item in (self.nick, self.lo, self.hi):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        s = self.wizard.s
        try:
            lo, hi = int(self.lo.value), int(self.hi.value)
        except ValueError:
            return await self.wizard.refresh(interaction, "❌ Nummern bitte nur als ganze Zahlen eingeben.")
        if not 0 <= lo <= hi <= 999:
            return await self.wizard.refresh(interaction, "❌ Der Bereich muss zwischen 0 und 999 liegen (klein ≤ groß).")
        s.update(nickname_template=self.nick.value.strip() or "{name}", number_min=lo, number_max=hi)
        self.wizard.save()
        await self.wizard.refresh(interaction, "✅ Gespeichert.")
