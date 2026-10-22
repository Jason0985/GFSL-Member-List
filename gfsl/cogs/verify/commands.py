"""Slash-Befehle des Verify-Moduls: /verify, /fragen, /fahrer"""
import asyncio
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from gfsl import storage

from . import data, drivers, flow, option_roles
from .profile import ProfileModal
from . import legacy_import as imp
from . import questions as Q
from .constants import MAX_QUESTIONS, Colors
from .flags import FLAGS, find_flag
from .setup_wizard import STEPS, SetupWizard, post_panel, settings_overview

TYPE_CHOICES = [
    app_commands.Choice(name="Freitext (kurz)", value="text"),
    app_commands.Choice(name="Freitext (lang)", value="paragraph"),
    app_commands.Choice(name="Zahl", value="number"),
    app_commands.Choice(name="Rundenzeit (1:05.013)", value="time"),
    app_commands.Choice(name="Auswahl (Dropdown)", value="select"),
    app_commands.Choice(name="Flagge (Dropdown mit Flaggen)", value="flag"),
    app_commands.Choice(name="Ja/Nein", value="yesno"),
    app_commands.Choice(name="Haken / Bestätigen", value="confirm"),
    app_commands.Choice(name="Screenshot hochladen", value="image"),
]
PURPOSE_CHOICES = [
    app_commands.Choice(name="Keins", value="none"),
    app_commands.Choice(name="Ingame-Name → wird Nickname", value="name"),
    app_commands.Choice(name="Fahrernummer (Auswahl aller freien Nummern)", value="number"),
    app_commands.Choice(name="Flagge für die Fahrerliste", value="flag"),
    app_commands.Choice(name="Plattform", value="platform"),
]
STEP_CHOICES = [app_commands.Choice(name=f"{n + 1}. {name}", value=n) for n, (_, name) in enumerate(STEPS)]

ADMIN = discord.Permissions(manage_guild=True)
log = logging.getLogger("gfsl.verify")


async def reply(interaction: discord.Interaction, content: str):
    await interaction.response.send_message(content, ephemeral=True)


def question_index(d: dict, nr: int) -> int | None:
    return nr - 1 if 1 <= nr <= len(d["questions"]) else None


async def question_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    d = data.get(interaction.guild_id)
    return [
        app_commands.Choice(name=f"{n + 1}. {q['label']}"[:100], value=n + 1)
        for n, q in enumerate(d["questions"])
        if current.lower() in f"{n + 1} {q['label']}".lower()
    ][:25]


async def number_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    d = data.get(interaction.guild_id)
    user = getattr(interaction.namespace, "user", None)
    uid = str(user.id) if user else None
    current = current.strip().lstrip("#")
    if current.isdigit():
        n = int(current)
        owner = drivers.owner_of(d, n, uid)
        if owner:
            name = d["drivers"][owner].get("name") or owner
            return [app_commands.Choice(name=f"#{n} – belegt von {name}"[:100], value=n)]
        return [app_commands.Choice(name=f"#{n} – frei", value=n)]
    return [app_commands.Choice(name=f"#{n} – frei", value=n) for n in drivers.free_numbers(d, uid)[:25]]


async def flag_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=f"{emoji} {name}", value=name)
            for name, emoji in FLAGS.items() if current.lower() in name.lower()][:25]


class VerifyCog(commands.Cog):
    verify = app_commands.Group(name="verify", description="GFSL Verifizierung einrichten",
                                default_permissions=ADMIN, guild_only=True)
    fragen = app_commands.Group(name="fragen", description="Verifizierungsfragen verwalten",
                                default_permissions=ADMIN, guild_only=True)
    fahrer = app_commands.Group(name="fahrer", description="Fahrer und Nummern verwalten",
                                default_permissions=ADMIN, guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._pending_refresh: dict[int, asyncio.Task] = {}

    # ---------- Fahrerliste bei Rollenwechsel aktualisieren ----------

    def schedule_refresh(self, guild: discord.Guild, delay: float = 10):
        """Fasst viele Rollenänderungen kurz hintereinander zu einer Aktualisierung zusammen."""
        if guild.id in self._pending_refresh:
            return

        async def run():
            await asyncio.sleep(delay)
            self._pending_refresh.pop(guild.id, None)
            try:
                await drivers.refresh_driver_list(guild, data.get(guild.id))
            except (discord.HTTPException, RuntimeError) as e:
                log.warning("Fahrerliste auf %s nicht aktualisiert: %s", guild.name, e)
        self._pending_refresh[guild.id] = asyncio.create_task(run())

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles == after.roles:
            return
        d = data.get(after.guild.id)
        if str(after.id) not in d["drivers"]:
            return
        watched = {rid for q in option_roles.mapped_questions(d["questions"]) if q["purpose"] in ("platform", "flag")
                   for rid in q["option_roles"].values()}
        changed = {r.id for r in before.roles} ^ {r.id for r in after.roles}
        if watched & changed:
            self.schedule_refresh(after.guild)

    # ---------- /profil (für Fahrer) ----------

    @app_commands.command(name="profil", description="Deine Flagge und Plattform für die Fahrerliste eintragen oder ändern")
    @app_commands.guild_only()
    async def profil(self, interaction: discord.Interaction):
        if str(interaction.user.id) not in data.get(interaction.guild_id)["drivers"]:
            return await reply(interaction, "Du bist noch nicht verifiziert. Nutze dafür den Verifizierungs-Button.")
        await interaction.response.send_modal(ProfileModal(interaction.guild, interaction.user.id))

    # ---------- /verify ----------

    @verify.command(name="setup", description="Einrichtung Schritt für Schritt: Kanäle, Start-Nachricht, Rollen, Nummern, Fragen")
    @app_commands.describe(schritt="Direkt zu einem Schritt springen")
    @app_commands.choices(schritt=STEP_CHOICES)
    async def setup(self, interaction: discord.Interaction, schritt: app_commands.Choice[int] | None = None):
        wizard = SetupWizard(interaction.guild, interaction.user.id, schritt.value if schritt else 0)
        await interaction.response.send_message(embed=wizard.render(), view=wizard, ephemeral=True)

    @verify.command(name="einstellungen", description="Zeigt alle aktuellen Einstellungen")
    async def einstellungen(self, interaction: discord.Interaction):
        d = data.get(interaction.guild_id)
        embed = discord.Embed(color=Colors.info, title="⚙️ GFSL Verify – Einstellungen",
                              description=settings_overview(interaction.guild, d) + "\n\nÄndern mit `/verify setup`.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @verify.command(name="panel", description="Postet den Start-Button neu bzw. aktualisiert ihn")
    async def panel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await post_panel(interaction.guild, data.get(interaction.guild_id)), ephemeral=True)

    # ---------- /fragen ----------

    @fragen.command(name="liste", description="Alle aktuellen Fragen anzeigen")
    async def liste(self, interaction: discord.Interaction):
        d = data.get(interaction.guild_id)
        if not d["questions"]:
            return await reply(interaction, "Noch keine Fragen. Nutze `/fragen standard` oder `/fragen neu`.")
        pages = Q.pages(d["questions"])
        page_of = {q["id"]: n for n, p in enumerate(pages) for q in p["questions"]}
        blocks = []
        last_page = None
        for n, q in enumerate(d["questions"]):
            if page_of[q["id"]] != last_page:
                last_page = page_of[q["id"]]
                kind = "Formular" if pages[last_page]["kind"] == "modal" else "Nummern-Auswahl"
                blocks.append(f"__**Teil {last_page + 1}/{len(pages)} · {kind}**__")
            blocks.append(Q.describe(q, n, d["settings"]))
        embeds = []
        cur = ""
        for block in blocks:
            if len(cur) + len(block) > 3900:
                embeds.append(cur)
                cur = ""
            cur += block + "\n\n"
        embeds.append(cur)
        result = [discord.Embed(color=Colors.info, description=e) for e in embeds[:10]]
        result[0].title = f"📋 Verifizierungsfragen ({len(d['questions'])}/{MAX_QUESTIONS})"
        await interaction.response.send_message(embeds=result, ephemeral=True)

    @fragen.command(name="vorschau", description="Testlauf: die Verifizierung so durchgehen, wie ein User sie sieht")
    async def vorschau(self, interaction: discord.Interaction):
        await flow.start(interaction, preview=True)

    @fragen.command(name="neu", description="Neue Frage hinzufügen")
    @app_commands.describe(
        typ="Antwortart", frage="Überschrift im Formular (max. 45 Zeichen)", hinweis="Erklärung unter der Frage (max. 100 Zeichen)",
        pflicht="Pflichtfrage? (Standard: ja)", optionen="Für Auswahl/Flagge: mit Komma getrennt, z. B. PlayStation, Xbox, PC",
        minimum="Zahl: kleinster Wert · Freitext: min. Zeichen", maximum="Zahl: größter Wert · Freitext: max. Zeichen",
        feld="Was der Bot automatisch mit der Antwort macht", position="An welcher Stelle einfügen (Standard: ans Ende)")
    @app_commands.rename(minimum="min", maximum="max")
    @app_commands.choices(typ=TYPE_CHOICES, feld=PURPOSE_CHOICES)
    async def neu(self, interaction: discord.Interaction, typ: app_commands.Choice[str], frage: app_commands.Range[str, 1, 45],
                  hinweis: app_commands.Range[str, 1, 100] | None = None, pflicht: bool = True, optionen: str | None = None,
                  minimum: int | None = None, maximum: int | None = None, feld: app_commands.Choice[str] | None = None,
                  position: app_commands.Range[int, 1] | None = None):
        d = data.get(interaction.guild_id)
        if len(d["questions"]) >= MAX_QUESTIONS:
            return await reply(interaction, f"Maximal {MAX_QUESTIONS} Fragen möglich.")
        q = Q.new_question(d["next_question_id"], typ.value, frage.strip(), hint=hinweis, required=pflicht,
                           options=Q.parse_options(optionen), min=minimum, max=maximum, purpose=feld.value if feld else "none")
        if err := Q.validate(q, d["questions"]):
            return await reply(interaction, f"❌ {err}")
        d["next_question_id"] += 1
        idx = len(d["questions"]) if position is None else min(position - 1, len(d["questions"]))
        d["questions"].insert(idx, q)
        storage.save()
        await reply(interaction, f"✅ Frage hinzugefügt:\n{Q.describe(q, idx, d['settings'])}")

    @fragen.command(name="bearbeiten", description="Eine Frage ändern")
    @app_commands.describe(
        nr="Nummer der Frage", typ="Neue Antwortart", frage="Neue Überschrift (max. 45 Zeichen)",
        hinweis='Neuer Hinweis (max. 100 Zeichen) – oder "-" zum Leeren', pflicht="Pflichtfrage?",
        optionen='Neue Optionen mit Komma – oder "-" zum Leeren', minimum="Neuer Mindestwert (-1 = entfernen)",
        maximum="Neuer Höchstwert (-1 = entfernen)", feld="Was der Bot automatisch mit der Antwort macht")
    @app_commands.rename(minimum="min", maximum="max")
    @app_commands.choices(typ=TYPE_CHOICES, feld=PURPOSE_CHOICES)
    @app_commands.autocomplete(nr=question_autocomplete)
    async def bearbeiten(self, interaction: discord.Interaction, nr: int, typ: app_commands.Choice[str] | None = None,
                         frage: app_commands.Range[str, 1, 45] | None = None, hinweis: app_commands.Range[str, 1, 100] | None = None,
                         pflicht: bool | None = None, optionen: str | None = None, minimum: int | None = None,
                         maximum: int | None = None, feld: app_commands.Choice[str] | None = None):
        d = data.get(interaction.guild_id)
        idx = question_index(d, nr)
        if idx is None:
            return await reply(interaction, "Diese Nummer gibt es nicht. Schau mit `/fragen liste` nach.")
        q = dict(d["questions"][idx], options=list(d["questions"][idx]["options"]))
        if typ:
            q["type"] = typ.value
        if frage:
            q["label"] = frage.strip()
        if hinweis is not None:
            q["hint"] = None if hinweis.strip() == "-" else hinweis.strip()
        if pflicht is not None:
            q["required"] = pflicht
        if optionen is not None:
            q["options"] = [] if optionen.strip() == "-" else Q.parse_options(optionen)
        if minimum is not None:
            q["min"] = None if minimum == -1 else minimum
        if maximum is not None:
            q["max"] = None if maximum == -1 else maximum
        if feld:
            q["purpose"] = feld.value
        option_roles.prune(q)
        if err := Q.validate(q, d["questions"]):
            return await reply(interaction, f"❌ {err}")
        d["questions"][idx] = q
        storage.save()
        await reply(interaction, f"✅ Frage geändert:\n{Q.describe(q, idx, d['settings'])}")

    @fragen.command(name="rollen", description="Rollen je Antwort festlegen, z. B. PlayStation → @PS-Fahrer")
    @app_commands.describe(nr="Nummer der Frage (Auswahl, Flagge oder Ja/Nein)")
    @app_commands.autocomplete(nr=question_autocomplete)
    async def rollen(self, interaction: discord.Interaction, nr: int):
        d = data.get(interaction.guild_id)
        idx = question_index(d, nr)
        if idx is None:
            return await reply(interaction, "Diese Nummer gibt es nicht. Schau mit `/fragen liste` nach.")
        q = d["questions"][idx]
        if q["type"] not in option_roles.ROLE_TYPES:
            return await reply(interaction, "Rollen gehen nur bei Fragen vom Typ Auswahl, Flagge oder Ja/Nein.")
        view = option_roles.OptionRolesView(interaction.guild, interaction.user.id, d, q["id"])
        await interaction.response.send_message(embed=view.render(), view=view, ephemeral=True)

    @fragen.command(name="verschieben", description="Reihenfolge ändern")
    @app_commands.describe(nr="Nummer der Frage", position="Neue Position")
    @app_commands.autocomplete(nr=question_autocomplete)
    async def verschieben(self, interaction: discord.Interaction, nr: int, position: app_commands.Range[int, 1]):
        d = data.get(interaction.guild_id)
        idx = question_index(d, nr)
        if idx is None:
            return await reply(interaction, "Diese Nummer gibt es nicht.")
        q = d["questions"].pop(idx)
        to = min(position - 1, len(d["questions"]))
        d["questions"].insert(to, q)
        storage.save()
        await reply(interaction, f"✅ „{q['label']}“ ist jetzt Frage {to + 1}.")

    @fragen.command(name="entfernen", description="Frage löschen")
    @app_commands.describe(nr="Nummer der Frage")
    @app_commands.autocomplete(nr=question_autocomplete)
    async def entfernen(self, interaction: discord.Interaction, nr: int):
        d = data.get(interaction.guild_id)
        idx = question_index(d, nr)
        if idx is None:
            return await reply(interaction, "Diese Nummer gibt es nicht.")
        q = d["questions"].pop(idx)
        storage.save()
        await reply(interaction, f"🗑️ Frage entfernt: „{q['label']}“")

    @fragen.command(name="standard", description="Lädt die GFSL-Standardfragen (ersetzt alle aktuellen!)")
    @app_commands.describe(ueberschreiben="Zum Bestätigen auf True setzen")
    async def standard(self, interaction: discord.Interaction, ueberschreiben: bool):
        if not ueberschreiben:
            return await reply(interaction, "Abgebrochen – nichts geändert.")
        d = data.get(interaction.guild_id)
        d["questions"], d["next_question_id"] = Q.default_questions(d["next_question_id"])
        storage.save()
        await reply(interaction, f"✅ {len(d['questions'])} Standardfragen geladen. Mit `/fragen liste` ansehen "
                                 "und mit `/fragen bearbeiten` anpassen.")

    # ---------- /fahrer ----------

    async def refresh_list(self, guild: discord.Guild, d: dict) -> str | None:
        try:
            await drivers.refresh_driver_list(guild, d)
        except (discord.HTTPException, RuntimeError) as e:
            return f"Fahrerliste: {e}"
        return None

    @fahrer.command(name="aktualisieren", description="Fahrerliste neu posten/aktualisieren")
    async def aktualisieren(self, interaction: discord.Interaction):
        d = data.get(interaction.guild_id)
        if not d["settings"]["list_channel_id"]:
            return await reply(interaction, "Erst einen Kanal für die Fahrerliste setzen: `/verify setup`")
        await interaction.response.defer(ephemeral=True)
        err = await self.refresh_list(interaction.guild, d)
        await interaction.followup.send(f"⚠️ {err}" if err else "✅ Fahrerliste aktualisiert.", ephemeral=True)

    @fahrer.command(name="setzen", description="Fahrer manuell eintragen oder Nummer ändern")
    @app_commands.describe(user="Discord-User", nummer="Fahrernummer (Vorschläge = freie Nummern)", name="Ingame-Name",
                           flagge="Land, z. B. Deutschland", plattform="z. B. PlayStation")
    @app_commands.autocomplete(nummer=number_autocomplete, flagge=flag_autocomplete)
    async def setzen(self, interaction: discord.Interaction, user: discord.Member, nummer: app_commands.Range[int, 0, 999],
                     name: app_commands.Range[str, 1, 32] | None = None, flagge: str | None = None, plattform: str | None = None):
        d = data.get(interaction.guild_id)
        uid = str(user.id)
        if owner := drivers.owner_of(d, nummer, uid):
            return await reply(interaction, f"❌ #{nummer} ist schon an <@{owner}> vergeben.")
        await interaction.response.defer(ephemeral=True)
        drv = d["drivers"].get(uid) or {"name": None, "flag": None, "platform": None,
                                        "verified_at": int(time.time()), "verified_by": str(interaction.user.id)}
        drv["number"] = nummer
        if name:
            drv["name"] = name
        if flagge:
            drv["flag"] = find_flag(flagge) or flagge
        if plattform:
            drv["platform"] = plattform
        drv["name"] = drv["name"] or user.name
        d["drivers"][uid] = drv
        storage.save()

        warnings = [w for w in (await drivers.set_nickname(user, d["settings"]["nickname_template"], drv),
                                await self.refresh_list(interaction.guild, d)) if w]
        # Rollen je Antwort anpassen; bei geänderter Plattform/Flagge die alte Rolle entfernen
        report = option_roles.SyncReport()
        await option_roles.sync_member(user, d, option_roles.mapped_questions(d["questions"]), report,
                                       remove_others=bool(plattform or flagge))
        warnings += list(dict.fromkeys(report.errors))
        await interaction.followup.send(f"✅ {user.mention} ist jetzt **#{nummer}**." + "".join(f"\n⚠️ {w}" for w in warnings), ephemeral=True)

    @fahrer.command(name="entfernen", description="Fahrer austragen (Nummer wird frei)")
    async def fahrer_entfernen(self, interaction: discord.Interaction, user: discord.User):
        d = data.get(interaction.guild_id)
        drv = d["drivers"].pop(str(user.id), None)
        if drv is None:
            return await reply(interaction, "Dieser User ist nicht in der Fahrerliste.")
        storage.save()
        await interaction.response.defer(ephemeral=True)
        err = await self.refresh_list(interaction.guild, d)
        nr = f", #{drv['number']} ist wieder frei" if drv.get("number") is not None else ""
        await interaction.followup.send(f"🗑️ {user.mention} ausgetragen{nr}. Rollen und Nickname wurden nicht verändert."
                                        + (f"\n⚠️ {err}" if err else ""), ephemeral=True)

    @fahrer.command(name="rollen_sync", description="Gibt allen eingetragenen Fahrern die Rollen passend zu ihrer Auswahl (z. B. Plattform)")
    @app_commands.describe(andere_entfernen="Rollen anderer Optionen entfernen, z. B. bei Plattformwechsel (Standard: nein)")
    async def rollen_sync(self, interaction: discord.Interaction, andere_entfernen: bool = False):
        d = data.get(interaction.guild_id)
        questions = option_roles.mapped_questions(d["questions"])
        if not questions:
            return await reply(interaction, "Noch keine Rollen festgelegt. Erst mit `/fragen rollen` einstellen.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        report = await option_roles.sync_all(interaction.guild, d, questions, andere_entfernen)
        err = await self.refresh_list(interaction.guild, d)
        await interaction.followup.send(report.text()[:1900] + (f"\n⚠️ {err}" if err else ""), ephemeral=True)

    @fahrer.command(name="fehlende_angaben", description="Zeigt, bei welchen Fahrern Flagge oder Plattform fehlt")
    @app_commands.describe(oeffentlich="In den Kanal posten und die Fahrer anpingen, damit sie es mit /profil nachtragen")
    async def fehlende_angaben(self, interaction: discord.Interaction, oeffentlich: bool = False):
        d = data.get(interaction.guild_id)
        lines = []
        for uid, drv in sorted(d["drivers"].items(), key=lambda e: e[1].get("number") if isinstance(e[1].get("number"), int) else 9999):
            missing = [label for key, label in (("flag", "Flagge"), ("platform", "Plattform"))
                       if not drivers.profile_value(d, interaction.guild, uid, key)]
            if missing and interaction.guild.get_member(int(uid)):
                lines.append(f"<@{uid}>: {', '.join(missing)}")
        if not lines:
            return await reply(interaction, "✅ Bei allen Fahrern sind Flagge und Plattform eingetragen.")
        head = (f"**Bei {len(lines)} Fahrer(n) fehlt noch etwas für die Fahrerliste.** "
                "Bitte mit `/profil` nachtragen (oder auf den Verifizierungs-Button klicken):")
        chunks, cur = [], head
        for line in lines:
            if len(cur) + len(line) + 1 > 1900:
                chunks.append(cur)
                cur = ""
            cur += "\n" + line
        chunks.append(cur)
        if not oeffentlich:
            return await interaction.response.send_message(chunks[0], ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        await interaction.response.send_message("📣 Wird gepostet …", ephemeral=True)
        for chunk in chunks:
            await interaction.channel.send(chunk, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))

    @fahrer.command(name="nummern", description="Übersicht aller Nummern: frei und vergeben")
    @app_commands.describe(oeffentlich="Für alle sichtbar in den Kanal posten")
    async def nummern(self, interaction: discord.Interaction, oeffentlich: bool = False):
        d = data.get(interaction.guild_id)
        s = d["settings"]
        taken = {drv["number"] for drv in d["drivers"].values() if isinstance(drv.get("number"), int)}
        width = len(str(s["number_max"]))
        cells = []
        for n in range(s["number_min"], s["number_max"] + 1):
            cells.append("·" * width if n in taken else str(n).rjust(width))
        rows = [" ".join(cells[i:i + 10]) for i in range(0, len(cells), 10)]
        grid = "\n".join(rows)
        if len(grid) > 3800:
            grid = grid[:3800] + "\n…"
        free = len(drivers.free_numbers(d))
        embed = discord.Embed(color=Colors.list, title="🔢 Fahrernummern",
                              description=f"```\n{grid}\n```\n`{'·' * width}` vergeben · "
                                          f"**{free} frei** von {s['number_max'] - s['number_min'] + 1}")
        await interaction.response.send_message(embed=embed, ephemeral=not oeffentlich)

    @fahrer.command(name="export", description="Alle Fahrer als CSV-Datei (für Excel)")
    async def export(self, interaction: discord.Interaction):
        d = data.get(interaction.guild_id)
        records = [imp.Record(uid, drv.get("number"), drv.get("name"), drv.get("flag"), drv.get("platform"), "Datenbank")
                   for uid, drv in d["drivers"].items()]
        await interaction.response.send_message(f"📤 {len(records)} Fahrer.", file=imp.to_csv(records, interaction.guild), ephemeral=True)

    async def show_import_preview(self, interaction: discord.Interaction, records: list, read_info: str, only_members: bool):
        d = data.get(interaction.guild_id)
        records, gone = imp.check(records, interaction.guild, d, only_members)
        if not records:
            return await interaction.followup.send(
                f"{read_info}\nKeine Fahrer erkannt. Hat ein Mensch die alte Liste gepostet, probier `nur_bots:False`. Sonst schick mir am besten ein Beispiel einer alten Nachricht, dann passe ich die Erkennung an.",
                ephemeral=True)
        embed = imp.preview_embed(records, interaction.guild, d, read_info, gone, only_members)
        await interaction.followup.send(embed=embed, file=imp.to_csv(records, interaction.guild),
                                        view=imp.ImportView(interaction.user.id, records), ephemeral=True)

    @fahrer.command(name="import_kanal", description="Altdaten aus einem Kanal einlesen, z. B. die Nachrichten des alten Melpo-Bots")
    @app_commands.describe(kanal="Kanal mit den alten Verify-Nachrichten oder der alten Fahrerliste",
                           nur_mitglieder="Nur User übernehmen, die noch auf dem Server sind (Standard: ja)",
                           nur_bots="Nur Bot-Nachrichten lesen (Standard: ja). Bei einer von Hand gepflegten Liste: False",
                           anzahl="Wie viele Nachrichten maximal gelesen werden (Standard: 5000)")
    async def import_kanal(self, interaction: discord.Interaction, kanal: discord.TextChannel, nur_mitglieder: bool = True,
                           nur_bots: bool = True, anzahl: app_commands.Range[int, 1, 20000] = 5000):
        perms = kanal.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.read_message_history):
            return await reply(interaction, f"Ich kann {kanal.mention} nicht lesen (Kanal ansehen + Nachrichtenverlauf lesen fehlt).")
        await interaction.response.defer(ephemeral=True, thinking=True)
        records, count = await imp.read_channel(kanal, anzahl, nur_bots)
        await self.show_import_preview(interaction, records, f"**Gelesen:** {count} Nachrichten in {kanal.mention}", nur_mitglieder)

    @fahrer.command(name="import_nicknames", description="Fahrer aus Nicknames wie „#17 | Name“ übernehmen")
    @app_commands.describe(rolle="Rolle, deren Mitglieder gelesen werden (z. B. Fahrer)")
    async def import_nicknames(self, interaction: discord.Interaction, rolle: discord.Role):
        await interaction.response.defer(ephemeral=True, thinking=True)
        records = imp.read_nicknames(rolle)
        await self.show_import_preview(interaction, records, f"**Gelesen:** {len(rolle.members)} Mitglieder mit {rolle.mention}", True)

    @fahrer.command(name="import_csv", description="Fahrer aus einer CSV-Datei übernehmen (z. B. korrigierter Export)")
    @app_commands.describe(datei="CSV mit Spalten user_id;nummer;name;flagge;plattform")
    async def import_csv(self, interaction: discord.Interaction, datei: discord.Attachment):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            records = imp.read_csv(await datei.read())
        except (UnicodeDecodeError, ValueError, KeyError, IndexError, imp.csv.Error) as e:
            return await interaction.followup.send(f"❌ Die Datei konnte nicht gelesen werden: {e}", ephemeral=True)
        await self.show_import_preview(interaction, records, f"**Gelesen:** {len(records)} Zeilen aus `{datei.filename}`", False)
