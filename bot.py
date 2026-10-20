import os
import discord
from discord.ext import commands

# Intents aktivieren (Wichtig für Member-Daten)
intents = discord.Intents.default()
intents.members = True  # Erlaubt das Auslesen von Server-Mitgliedern und Rollen

bot = commands.Bot(command_prefix="!", intents=intents)

# Trage hier deine IDs ein (Rechtsklick auf Channel/Rolle -> ID kopieren im Entwicklermodus)
CHANNEL_ID = 1383036780504158288  # Der Kanal, in dem die Liste stehen soll
ROLE_ID = 1186734973751930980     # Die ID der "Ersatzfahrer"-Rolle

# Wir speichern uns die Message-ID zwischen, damit der Bot immer dieselbe Nachricht bearbeitet
persistent_message_id = None

async def update_driver_list(guild):
    global persistent_message_id
    channel = guild.get_channel(CHANNEL_ID)
    if not channel:
        return

    role = guild.get_role(ROLE_ID)
    if not role:
        return
    
    # Alle Mitglieder mit der Rolle filtern
    drivers = [member.display_name for member in role.members]
    
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
        if persistent_message_id:
            # Versuche, die bestehende Nachricht zu bearbeiten
            msg = await channel.fetch_message(persistent_message_id)
            await msg.edit(embed=embed)
        else:
            # Suche im Kanal nach einer alten Bot-Nachricht, um Spam zu vermeiden
            async for message in channel.history(limit=10):
                if message.author == bot.user and message.embeds:
                    persistent_message_id = message.id
                    await message.edit(embed=embed)
                    return
            
            # Wenn keine existiert, schicke eine neue
            msg = await channel.send(embed=embed)
            persistent_message_id = msg.id
    except Exception as e:
        print(f"Fehler beim Aktualisieren der Liste: {e}")

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