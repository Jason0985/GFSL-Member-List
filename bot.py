import os
import json
import discord
from discord import app_commands
from discord.ext import commands

# Intents aktivieren (Wichtig für Member-Daten)
intents = discord.Intents.default()
intents.members = True  # Erlaubt das Auslesen von Server-Mitgliedern und Rollen

bot = commands.Bot(command_prefix="!", intents=intents)

# Einstellungen pro Server: {guild_id: {"channel_id": ..., "role_id": ..., "message_id": ...}}
# Werden per /setchannel und /setrole in Discord gesetzt
# ponytail: JSON-Datei, geht bei Hostern ohne festen Speicher beim Redeploy verloren -> CONFIG_FILE auf ein Volume legen
CONFIG_FILE = os.environ.get("CONFIG_FILE", "config.json")

try:
    with open(CONFIG_FILE) as f:
        config = json.load(f)
except FileNotFoundError:
    config = {}

def save_config():
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

async def update_driver_list(guild):
    cfg = config.setdefault(str(guild.id), {})
    channel = guild.get_channel(cfg.get("channel_id", 0))
    if not channel:
        return

    role = guild.get_role(cfg.get("role_id", 0))
    if not role:
        return

    # Alle Mitglieder mit der Rolle filtern
    drivers = [member.mention for member in role.members]

    if drivers:
        driver_text = "\n".join([f"• {name}" for name in drivers])
    else:
        driver_text = "Aktuell sind keine Ersatzfahrer eingetragen."

    embed = discord.Embed(
        title="🏎️ Aktuelle Ersatzfahrer",
        description=driver_text,
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="Automatisch aktualisiert")

    try:
        msg = None
        if cfg.get("message_id"):
            try:
                msg = await channel.fetch_message(cfg["message_id"])
            except discord.NotFound:
                pass  # Nachricht wurde gelöscht -> unten neue schicken

        if not msg:
            # Suche im Kanal nach einer alten Bot-Nachricht, um Spam zu vermeiden
            async for message in channel.history(limit=10):
                if message.author == bot.user and message.embeds:
                    msg = message
                    break

        if msg:
            await msg.edit(embed=embed)
        else:
            # Wenn keine existiert, schicke eine neue
            msg = await channel.send(embed=embed)

        if cfg.get("message_id") != msg.id:
            cfg["message_id"] = msg.id
            save_config()
    except Exception as e:
        print(f"Fehler beim Aktualisieren der Liste: {e}")

# Slash-Befehle bei Discord registrieren
@bot.event
async def setup_hook():
    await bot.tree.sync()

@bot.tree.command(name="setchannel", description="Kanal festlegen, in dem die Liste gepostet wird")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def setchannel(interaction: discord.Interaction, kanal: discord.TextChannel):
    cfg = config.setdefault(str(interaction.guild_id), {})
    cfg["channel_id"] = kanal.id
    cfg.pop("message_id", None)  # Im neuen Kanal eine neue Nachricht anlegen
    save_config()
    await interaction.response.send_message(f"Die Liste wird jetzt in {kanal.mention} gepostet.", ephemeral=True)
    await update_driver_list(interaction.guild)

@bot.tree.command(name="setrole", description="Rolle festlegen, deren Mitglieder aufgelistet werden")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def setrole(interaction: discord.Interaction, rolle: discord.Role):
    config.setdefault(str(interaction.guild_id), {})["role_id"] = rolle.id
    save_config()
    await interaction.response.send_message(f"Die Liste zeigt jetzt alle mit der Rolle {rolle.mention}.", ephemeral=True)
    await update_driver_list(interaction.guild)

@bot.event
async def on_ready():
    print(f"Eingeloggt als {bot.user.name} (ID: {bot.user.id})")
    print("Bot ist bereit und läuft!")

    # Beim Start einmal für alle Server des Bots die Liste prüfen
    for guild in bot.guilds:
        await update_driver_list(guild)

# Event: Wenn ein Mitglied geupdated wird (z.B. Rolle bekommt oder verliert)
@bot.event
async def on_member_update(before, after):
    if before.roles != after.roles:
        await update_driver_list(after.guild)

# Event: Wenn jemand den Server betritt oder verlässt
@bot.event
async def on_member_join(member):
    await update_driver_list(member.guild)

@bot.event
async def on_member_remove(member):
    await update_driver_list(member.guild)

# Bot starten (Token kommt aus der Umgebungsvariable DISCORD_TOKEN)
bot.run(os.environ["DISCORD_TOKEN"])
