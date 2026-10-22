"""Verify-Modul: Verifizierung per Formular, Fahrernummern, automatische Fahrerliste und Import der Melpo-Altdaten."""
from discord.ext import commands

from .commands import VerifyCog
from .flow import PanelView
from .review import DecisionButton


async def setup(bot: commands.Bot):
    # Buttons, die auch nach einem Neustart funktionieren müssen
    bot.add_view(PanelView())
    bot.add_dynamic_items(DecisionButton)
    await bot.add_cog(VerifyCog(bot))
