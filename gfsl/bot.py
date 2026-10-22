import logging

import discord
from discord import app_commands
from discord.ext import commands

from . import storage

log = logging.getLogger("gfsl")

# Alle Module des Bots. Neues Modul = neue Datei in gfsl/cogs/ + hier eintragen.
EXTENSIONS = [
    "gfsl.cogs.ersatzfahrer",  # automatische Ersatzfahrer-Liste
    "gfsl.cogs.verify",        # Verifizierung, Fahrernummern, Fahrerliste, Melpo-Import
]


class GFSLBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True          # Mitglieder & Rollen (Developer Portal: Server Members Intent)
        intents.message_content = True  # Melpo-Import liest fremde Bot-Nachrichten (Message Content Intent)
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.tree.on_error = self.on_app_command_error

    async def setup_hook(self):
        storage.load()
        for ext in EXTENSIONS:
            await self.load_extension(ext)
        # Slash-Befehle bei Discord registrieren
        await self.tree.sync()

    async def on_ready(self):
        log.info("Eingeloggt als %s (ID: %s) auf %d Server(n)", self.user, self.user.id, len(self.guilds))

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            msg = str(error) or "⛔ Dafür fehlt dir die Berechtigung."
        else:
            original = getattr(error, "original", error)
            log.exception("Fehler in /%s", interaction.command and interaction.command.qualified_name, exc_info=original)
            msg = f"⚠️ Fehler: {original}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
