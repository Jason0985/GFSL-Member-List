"""Ersatzfahrer-Liste: postet alle Mitglieder einer Rolle in einen Kanal und hält die Liste aktuell."""
import json
import logging
import os
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from gfsl import storage

log = logging.getLogger("gfsl.ersatzfahrer")

DEFAULTS = {"channel_id": None, "role_id": None, "message_id": None}
LIST_TITLE = "🏎️ Aktuelle Ersatzfahrer"

# Einstellungen aus der alten config.json (vor dem Umbau auf Module) einmalig übernehmen
LEGACY_CONFIG = Path(os.environ.get("CONFIG_FILE", "config.json"))


def cfg(guild_id: int) -> dict:
    return storage.section(guild_id, "ersatzfahrer", DEFAULTS)


def migrate_legacy_config() -> None:
    if not LEGACY_CONFIG.exists():
        return
    old = json.loads(LEGACY_CONFIG.read_text(encoding="utf-8"))
    changed = False
    for gid, values in old.items():
        c = cfg(int(gid))
        for key in DEFAULTS:
            if c[key] is None and values.get(key):
                c[key] = values[key]
                changed = True
    if changed:
        storage.save()
        log.info("Alte Einstellungen aus %s übernommen", LEGACY_CONFIG)


class Ersatzfahrer(commands.GroupCog, group_name="ersatzfahrer", group_description="Automatische Ersatzfahrer-Liste"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def update_list(self, guild: discord.Guild):
        c = cfg(guild.id)
        channel = guild.get_channel(c["channel_id"] or 0)
        role = guild.get_role(c["role_id"] or 0)
        if not channel or not role:
            return

        drivers = [m.mention for m in role.members]
        text = "\n".join(f"• {d}" for d in drivers) if drivers else "Aktuell sind keine Ersatzfahrer eingetragen."
        embed = discord.Embed(
            title=LIST_TITLE,
            description=text,
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Automatisch aktualisiert")

        try:
            msg = None
            if c["message_id"]:
                try:
                    msg = await channel.fetch_message(c["message_id"])
                except discord.NotFound:
                    pass  # Nachricht wurde gelöscht -> unten neue schicken
            if not msg:
                # Alte Listen-Nachricht im Kanal suchen, z. B. wenn die Datenbank verloren ging
                async for m in channel.history(limit=20):
                    if m.author == self.bot.user and m.embeds and m.embeds[0].title == LIST_TITLE:
                        msg = m
                        break
            if msg:
                await msg.edit(embed=embed)
            else:
                msg = await channel.send(embed=embed)
            if c["message_id"] != msg.id:
                c["message_id"] = msg.id
                storage.save()
        except discord.HTTPException as e:
            log.warning("Liste auf %s nicht aktualisiert: %s", guild.name, e)

    @app_commands.command(name="kanal", description="Kanal festlegen, in dem die Liste gepostet wird")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def kanal(self, interaction: discord.Interaction, kanal: discord.TextChannel):
        c = cfg(interaction.guild_id)
        c["channel_id"] = kanal.id
        c["message_id"] = None  # Im neuen Kanal eine neue Nachricht anlegen
        storage.save()
        await interaction.response.send_message(f"Die Liste wird jetzt in {kanal.mention} gepostet.", ephemeral=True)
        await self.update_list(interaction.guild)

    @app_commands.command(name="rolle", description="Rolle festlegen, deren Mitglieder aufgelistet werden")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def rolle(self, interaction: discord.Interaction, rolle: discord.Role):
        cfg(interaction.guild_id)["role_id"] = rolle.id
        storage.save()
        await interaction.response.send_message(f"Die Liste zeigt jetzt alle mit der Rolle {rolle.mention}.", ephemeral=True)
        await self.update_list(interaction.guild)

    @commands.Cog.listener()
    async def on_ready(self):
        # Beim Start einmal für alle Server die Liste prüfen
        for guild in self.bot.guilds:
            await self.update_list(guild)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # Nur aktualisieren, wenn sich genau die Ersatzfahrer-Rolle geändert hat
        role_id = cfg(after.guild.id)["role_id"]
        had = any(r.id == role_id for r in before.roles)
        has = any(r.id == role_id for r in after.roles)
        if role_id and had != has:
            await self.update_list(after.guild)

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent):
        guild = self.bot.get_guild(payload.guild_id)
        if guild:
            await self.update_list(guild)


async def setup(bot: commands.Bot):
    migrate_legacy_config()
    await bot.add_cog(Ersatzfahrer(bot))
