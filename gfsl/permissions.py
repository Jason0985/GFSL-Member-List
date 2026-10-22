"""Wer darf was: Admin = „Server verwalten“ oder die Admin-Rolle aus /verify setup (Schritt Rollen)."""
import discord
from discord import app_commands

from . import storage


class NotAdmin(app_commands.CheckFailure):
    pass


def admin_role_id(guild_id: int) -> int | None:
    return storage.section(guild_id, "verify", {}).get("settings", {}).get("mod_role_id")


def is_admin(interaction: discord.Interaction) -> bool:
    if interaction.permissions.manage_guild:
        return True
    rid = admin_role_id(interaction.guild_id)
    return bool(rid) and any(r.id == rid for r in getattr(interaction.user, "roles", []))


def is_admin_member(member: discord.Member | None) -> bool:
    """Für Stellen ohne Server-Kontext, z. B. Buttons in einer DM."""
    if member is None:
        return False
    if member.guild_permissions.manage_guild:
        return True
    rid = admin_role_id(member.guild.id)
    return bool(rid) and member.get_role(rid) is not None


def require_admin(interaction: discord.Interaction) -> bool:
    """Für Cog.interaction_check: wirft NotAdmin, damit der User eine klare Meldung bekommt."""
    if not is_admin(interaction):
        raise NotAdmin("⛔ Dafür brauchst du die Admin-Rolle.")
    return True
