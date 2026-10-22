"""Formulare im Ticket: Meldung (Einsender) und Aussage (Beschuldigte/Hinzugefügte).

Das Formular lässt sich jederzeit öffnen und beliebig oft ändern. Jede Eingabe ersetzt den bisherigen Eintrag im Ticket.
Abgeschickt wird erst mit dem roten Button „Einsendung endgültig abschicken“ bzw. „Aussage endgültig abschicken“.
"""
import discord

from gfsl import storage

from . import data as D
from .publish import URL_RE, download
from .ticket import add_log, lock, now, pending, repost_status


def entry_embed(guild: discord.Guild, t: dict, uid: str, kind: str, fields: dict, links: list[str],
                too_big: list[str]) -> discord.Embed:
    member = guild.get_member(int(uid))
    name = member.display_name if member else uid
    report = kind == "report"
    embed = discord.Embed(color=0x3B82F6 if report else 0x8B5CF6,
                          title=f"{'📝 Meldung' if report else '🗣️ Aussage'} von {name}"[:256],
                          description=fields["text"][:4000])
    if report:
        embed.add_field(name="Beschuldigt", value=", ".join(f"<@{u}>" for u in fields["accused"]), inline=False)
        embed.add_field(name="Vorfall", value=fields["incident"][:1024], inline=False)
    if links:
        embed.add_field(name="🎥 Videos & Links", value="\n".join(f"• {u}" for u in links)[:1024], inline=False)
    if too_big:
        embed.add_field(name="⚠️ Nicht angehängt (zu groß)", value="\n".join(f"• {n}" for n in too_big)[:1024]
                        + "\nBitte als YouTube-Link nachreichen.", inline=False)
    embed.add_field(name="Eingetragen", value=f"<t:{now()}:f>", inline=False)
    return embed


class EntryForm(discord.ui.Modal):
    def __init__(self, t: dict, uid: str, kind: str):
        report = kind == "report"
        super().__init__(title=f"Rennvorfall {D.number(t)} – {'Meldung' if report else 'Aussage'}", timeout=1800)
        self.tid, self.uid, self.kind = t["id"], uid, kind
        prev = t["entries"].get(uid, {}).get("fields", {})
        self.accused = self.incident = None
        if report:
            self.accused = discord.ui.UserSelect(min_values=1, max_values=10, required=True,
                                                 default_values=[discord.Object(int(u)) for u in prev.get("accused", [])])
            self.incident = discord.ui.TextInput(max_length=200, default=prev.get("incident"),
                                                 placeholder="z. B. Abu Dhabi, Runde 3, Kurve 1")
            self.add_item(discord.ui.Label(text="Beschuldigte", description="Wen betrifft der Vorfall? Mehrere möglich",
                                           component=self.accused))
            self.add_item(discord.ui.Label(text="Vorfall", component=self.incident))
        self.text = discord.ui.TextInput(style=discord.TextStyle.long, max_length=2000, default=prev.get("text"))
        self.links = discord.ui.TextInput(style=discord.TextStyle.long, required=False, max_length=500, default=prev.get("links"),
                                          placeholder="https://youtu.be/… (einer pro Zeile)")
        self.files = discord.ui.FileUpload(required=False, min_values=0, max_values=5)
        self.add_item(discord.ui.Label(text="Vorfallbeschreibung" if report else "Deine Aussage", component=self.text))
        self.add_item(discord.ui.Label(text="Video-Links (YouTube o. ä.)", component=self.links))
        self.add_item(discord.ui.Label(text="Video / Screenshots (optional)", component=self.files,
                                       description="Leer = bisherige Dateien bleiben · große Videos bitte als Link"))

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        d = D.get(guild.id)
        t = d["tickets"].get(self.tid)
        report = self.kind == "report"
        allowed = t and ((report and t["phase"] == "reporter") or (not report and t["phase"] == "statements" and self.uid in pending(t)))
        if not allowed:
            return await interaction.response.send_message("Das Formular kann in diesem Schritt nicht mehr gespeichert werden.", ephemeral=True)

        fields = {"text": self.text.value.strip(), "links": self.links.value.strip()}
        if report:
            accused = [u for u in self.accused.values if not u.bot and str(u.id) != self.uid]
            if not accused:
                return await interaction.response.send_message("❌ Bitte mindestens einen Beschuldigten (nicht dich selbst) auswählen.",
                                                               ephemeral=True)
            fields.update(accused=[str(u.id) for u in accused], incident=self.incident.value.strip())
        links = list(dict.fromkeys(u.rstrip(".,!?") for u in URL_RE.findall(fields["links"])))
        if fields["links"] and not links:
            return await interaction.response.send_message("❌ Bei den Video-Links bitte vollständige Links angeben (https://…).",
                                                           ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)

        channel = interaction.channel
        async with lock(guild.id, self.tid):
            old = t["entries"].get(self.uid)
            uploads = list(self.files.values)
            if not uploads and old:  # nichts Neues hochgeladen: bisherige Dateien übernehmen
                try:
                    uploads = (await channel.fetch_message(old["message_id"])).attachments
                except discord.HTTPException:
                    uploads = []
            files, too_big = await download(uploads, guild.filesize_limit)
            if old:
                try:
                    await channel.get_partial_message(old["message_id"]).delete()
                except discord.HTTPException:
                    pass
            msg = await channel.send(embed=entry_embed(guild, t, self.uid, self.kind, fields, links, [a.filename for a in too_big]),
                                     files=files, allowed_mentions=discord.AllowedMentions.none())
            t["entries"][self.uid] = {"message_id": msg.id, "at": now(), "fields": fields, "links": links}
            what = "Meldung" if report else "Aussage"
            await channel.send(add_log(t, f"{what} von <@{self.uid}> {'aktualisiert' if old else 'eingetragen'}"),
                               allowed_mentions=discord.AllowedMentions.none())
            await repost_status(channel, t)
            storage.save()

        button = "Einsendung endgültig abschicken" if report else "Aussage endgültig abschicken"
        text = (f"✅ {what} eingetragen. Schau sie dir im Ticket an und klicke dann **{button}**.\n"
                "Bis dahin kannst du sie jederzeit mit **📝 Aussage anpassen** ändern.")
        if too_big:
            text += (f"\n⚠️ Zu groß zum Anhängen (max. {guild.filesize_limit // 1_048_576} MB): "
                     + ", ".join(a.filename for a in too_big) + " – bitte als YouTube-Link angeben.")
        await interaction.followup.send(text, ephemeral=True)
