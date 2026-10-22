"""Startpunkt des GFSL-Bots. Die einzelnen Funktionen liegen als Module in gfsl/cogs/."""
import logging
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # Hoster setzen DISCORD_TOKEN meist direkt als Umgebungsvariable

from gfsl.bot import GFSLBot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# Bot starten (Token kommt aus der Umgebungsvariable DISCORD_TOKEN)
GFSLBot().run(os.environ["DISCORD_TOKEN"], log_handler=None)
