"""Anmelde-Modul (wie Raid-Helper): Rennkalender, automatisches Posten, An-/Abmeldung mit Teams und Ersatzfahrern,
Q-Sperre nach der Frist, zufällige Einteilung der Ersatzfahrer und Anwesenheits-Statistik."""
from discord.ext import commands

from .actions import SignupButton
from .commands import EventsCog
from .review import ReviewButton


async def setup(bot: commands.Bot):
    # Buttons, die auch nach einem Neustart funktionieren müssen
    bot.add_dynamic_items(SignupButton, ReviewButton)
    await bot.add_cog(EventsCog(bot))
