"""Slash-Befehle des Rennvorfall-Moduls: alles unter /rennvorfall (nur Admins)."""
import discord
from discord import app_commands
from discord.ext import commands

from gfsl.permissions import require_admin

from . import data as D
from .setup_wizard import STEPS, IncidentSetup, overview, post_panel

STEP_CHOICES = [app_commands.Choice(name=f"{n + 1}. {name}", value=n) for n, (_, name) in enumerate(STEPS)]


class IncidentsCog(commands.Cog):
    rennvorfall = app_commands.Group(name="rennvorfall", description="Rennvorfälle: Einrichtung und offene Fälle",
                                     default_permissions=discord.Permissions(manage_guild=True), guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return require_admin(interaction)

    @rennvorfall.command(name="setup", description="Einrichtung Schritt für Schritt: Kanäle, Vorlage, Rollen, Archiv")
    @app_commands.describe(schritt="Direkt zu einem Schritt springen")
    @app_commands.choices(schritt=STEP_CHOICES)
    async def setup(self, interaction: discord.Interaction, schritt: app_commands.Choice[int] | None = None):
        wizard = IncidentSetup(interaction.guild, interaction.user.id, schritt.value if schritt else 0, interaction.channel)
        await interaction.response.send_message(embed=wizard.render(), view=wizard, ephemeral=True)

    @rennvorfall.command(name="einstellungen", description="Zeigt alle Einstellungen der Vorfall-Meldung")
    async def einstellungen(self, interaction: discord.Interaction):
        s = D.get(interaction.guild_id)["settings"]
        embed = discord.Embed(color=0x3B82F6, title="⚖️ Rennvorfall – Einstellungen",
                              description=overview(interaction.guild, s) + "\n\nÄndern mit `/rennvorfall setup`.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @rennvorfall.command(name="panel", description="Postet den „Vorfall melden“-Button neu bzw. aktualisiert ihn")
    async def panel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await post_panel(interaction.guild, D.get(interaction.guild_id)["settings"]), ephemeral=True)

    @rennvorfall.command(name="offen", description="Alle offenen Vorfälle mit Stand")
    async def offen(self, interaction: discord.Interaction):
        d = D.get(interaction.guild_id)
        open_ = [t for t in d["tickets"].values() if t["phase"] not in ("closed", "cancelled")]
        if not open_:
            return await interaction.response.send_message("✅ Keine offenen Vorfälle.", ephemeral=True)
        lines = [f"**{D.number(t)}** <#{t['channel_id']}> · {D.PHASES[t['phase']]} · <@{t['reporter_id']}>"
                 + (f" vs {', '.join(f'<@{u}>' for u in t['accused'])}" if t["accused"] else "")
                 + f" · seit <t:{t['opened_at']}:R>" for t in open_]
        await interaction.response.send_message("\n".join(lines)[:2000], ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())
