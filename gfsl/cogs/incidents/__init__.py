"""Rennvorfall-Modul: Vorfall melden per Button, privates Ticket, Aussagen, FIA-Entscheidung, Archiv-Thread."""
from discord.ext import commands

from .commands import IncidentsCog
from .ticket import PanelView, TicketButton


async def setup(bot: commands.Bot):
    # Buttons, die auch nach einem Neustart funktionieren müssen
    bot.add_view(PanelView())
    bot.add_dynamic_items(TicketButton)
    await bot.add_cog(IncidentsCog(bot))
