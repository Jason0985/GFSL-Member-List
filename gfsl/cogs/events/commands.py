"""Slash-Befehle des Anmelde-Moduls und der Zeitplaner, der jede Minute nachschaut, was zu tun ist."""
import logging
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from gfsl import storage
from gfsl.permissions import require_admin

from . import actions, assign, attendance, render, review
from . import data as D
from . import timeutil as tu
from .template_editor import TemplateEditor

log = logging.getLogger("gfsl.events")
ADMIN = discord.Permissions(manage_guild=True)

KIND_CHOICES = [app_commands.Choice(name="Team (Stammfahrer, feste Plätze)", value="team"),
                app_commands.Choice(name="Ersatzfahrer (wird nach der Frist eingeteilt)", value="reserve")]
STATUS_CHOICES = [app_commands.Choice(name="✅ angemeldet", value="yes"), app_commands.Choice(name="❔ vielleicht", value="maybe"),
                  app_commands.Choice(name="❌ abgemeldet", value="no"), app_commands.Choice(name="🗑️ Eintrag löschen", value="remove")]


async def reply(interaction: discord.Interaction, content: str):
    await interaction.response.send_message(content, ephemeral=True)


def event_label(d: dict, ev: dict) -> str:
    return f"#{ev['id']} {tu.from_str(ev['starts_at']).strftime('%d.%m.')} {D.field(d, ev, 'title')} ({D.STATUS_LABEL[ev['status']]})"


async def event_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    d = D.get(interaction.guild_id)
    evs = [e for e in D.sorted_events(d) if e["status"] in ("planned", "posted")] + \
          [e for e in reversed(D.sorted_events(d)) if e["status"] not in ("planned", "posted")]
    return [app_commands.Choice(name=event_label(d, e)[:100], value=e["id"])
            for e in evs if current.lower() in event_label(d, e).lower()][:25]


async def template_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    d = D.get(interaction.guild_id)
    return [app_commands.Choice(name=t["name"][:100], value=tid) for tid, t in d["templates"].items()
            if current.lower() in t["name"].lower()][:25]


async def class_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    d = D.get(interaction.guild_id)
    return [app_commands.Choice(name=f"{c['name']} ({'Team' if c['kind'] == 'team' else 'Ersatz'})"[:100], value=c["id"])
            for c in assign.classes(d) if current.lower() in c["name"].lower()][:25]


def check_emoji(text: str) -> str:
    emoji = discord.PartialEmoji.from_str(text.strip())
    if not emoji.id and not emoji.name:
        raise ValueError("Emoji nicht erkannt")
    return str(emoji)


class EventsCog(commands.Cog):
    anmeldung = app_commands.Group(name="anmeldung", description="Anmelde-Bot: Rollen/Teams für die Anmeldung",
                                   default_permissions=ADMIN, guild_only=True)
    vorlage = app_commands.Group(name="vorlage", description="Anmelde-Bot: Vorlagen (was bei jedem Rennen gleich ist)",
                                 default_permissions=ADMIN, guild_only=True)
    kalender = app_commands.Group(name="kalender", description="Anmelde-Bot: Rennkalender / Season",
                                  default_permissions=ADMIN, guild_only=True)
    event = app_commands.Group(name="event", description="Anmelde-Bot: laufende Events verwalten",
                               default_permissions=ADMIN, guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return require_admin(interaction)

    async def cog_load(self):
        self.scheduler.start()

    async def cog_unload(self):
        self.scheduler.cancel()

    # ---------- Zeitplaner ----------

    @tasks.loop(minutes=1)
    async def scheduler(self):
        for guild in self.bot.guilds:
            d = D.get(guild.id)
            for ev in list(d["events"].values()):
                try:
                    await self.process(guild, d, ev)
                except Exception:
                    log.exception("Event #%s auf %s", ev["id"], guild.name)

    @scheduler.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()

    async def process(self, guild: discord.Guild, d: dict, ev: dict):
        now = tu.now()
        t = render.times(d, ev)
        template = D.template_of(d, ev)
        if ev["status"] == "planned":
            if now >= t["start"]:
                ev["status"] = "missed"
                storage.save()
                return
            needs_ok = template["review"] and not ev["confirmed"]
            if needs_ok:
                if not ev["review_sent"] and now >= t["post"] - timedelta(hours=template["review_hours"]):
                    await review.send_review(self.bot, guild, d, ev)
                if now >= t["post"] and not ev["overdue_sent"]:
                    await review.send_overdue(self.bot, guild, d, ev)
                return
            if now >= t["post"]:
                log.info("Event #%s: %s", ev["id"], await actions.post(guild, d, ev))
        elif ev["status"] == "posted":
            if now >= t["start"]:
                await actions.close(guild, d, ev)
            elif not ev["deadline_done"] and now >= t["deadline"]:
                await actions.deadline_reached(guild, d, ev)
            elif template["nudge_hours"] and not ev["nudge_done"] and now >= t["deadline"] - timedelta(hours=template["nudge_hours"]):
                await actions.nudge(guild, d, ev)

    # ---------- /anmeldung: Klassen ----------

    @anmeldung.command(name="klassen", description="Zeigt alle Rollen/Teams, mit denen man sich anmelden kann")
    async def klassen(self, interaction: discord.Interaction):
        d = D.get(interaction.guild_id)
        if not assign.classes(d):
            return await reply(interaction, "Noch keine. Lege sie mit `/anmeldung klasse_neu` an – je Team eine und eine für Ersatzfahrer.")
        lines = [f"{c['emoji']} **{c['name']}** – <@&{c['role_id']}> · "
                 + (f"Team, {c['seats']} Plätze" if c["kind"] == "team" else "Ersatzfahrer") for c in assign.classes(d)]
        await interaction.response.send_message(embed=discord.Embed(color=0x3B82F6, title="🏎️ Anmelde-Klassen", description="\n".join(lines)),
                                                ephemeral=True)

    @anmeldung.command(name="klasse_neu", description="Rolle hinzufügen, mit der man sich anmelden kann (z. B. Team Ferrari)")
    @app_commands.describe(rolle="Discord-Rolle (z. B. Ferrari oder Ersatzfahrer)", art="Team oder Ersatzfahrer",
                           emoji="Icon, z. B. 🏎️ oder ein Server-Emoji wie :ferrari:", plaetze="Cockpits im Team (Standard 2)",
                           name="Anzeigename (Standard: Rollenname)", position="Stelle in der Übersicht (Standard: hinten)")
    @app_commands.choices(art=KIND_CHOICES)
    async def klasse_neu(self, interaction: discord.Interaction, rolle: discord.Role, art: app_commands.Choice[str], emoji: str,
                         plaetze: app_commands.Range[int, 1, 10] = 2, name: app_commands.Range[str, 1, 40] | None = None,
                         position: app_commands.Range[int, 1, 25] | None = None):
        d = D.get(interaction.guild_id)
        if any(c["role_id"] == rolle.id for c in assign.classes(d)):
            return await reply(interaction, f"{rolle.mention} ist schon eingetragen.")
        if len(assign.classes(d)) >= 20:
            return await reply(interaction, "Maximal 20 Klassen (Discord-Limit für die Übersicht).")
        try:
            icon = check_emoji(emoji)
        except ValueError:
            return await reply(interaction, "❌ Das Emoji wurde nicht erkannt.")
        c = {"id": d["settings"]["next_class_id"], "name": name or rolle.name, "role_id": rolle.id, "emoji": icon,
             "kind": art.value, "seats": plaetze}
        d["settings"]["next_class_id"] += 1
        classes = assign.classes(d)
        classes.insert(min(position - 1, len(classes)) if position else len(classes), c)
        storage.save()
        await reply(interaction, f"✅ {icon} **{c['name']}** ({rolle.mention}) hinzugefügt.")

    @anmeldung.command(name="klasse_bearbeiten", description="Icon, Plätze, Name oder Position einer Klasse ändern")
    @app_commands.autocomplete(klasse=class_autocomplete)
    async def klasse_bearbeiten(self, interaction: discord.Interaction, klasse: int, emoji: str | None = None,
                                plaetze: app_commands.Range[int, 1, 10] | None = None,
                                name: app_commands.Range[str, 1, 40] | None = None,
                                position: app_commands.Range[int, 1, 25] | None = None):
        d = D.get(interaction.guild_id)
        c = assign.class_by_id(d, klasse)
        if c is None:
            return await reply(interaction, "Diese Klasse gibt es nicht.")
        if emoji:
            try:
                c["emoji"] = check_emoji(emoji)
            except ValueError:
                return await reply(interaction, "❌ Das Emoji wurde nicht erkannt.")
        if plaetze:
            c["seats"] = plaetze
        if name:
            c["name"] = name
        if position:
            classes = assign.classes(d)
            classes.remove(c)
            classes.insert(min(position - 1, len(classes)), c)
        storage.save()
        await self.refresh_open_events(interaction.guild, d)
        await reply(interaction, f"✅ {c['emoji']} **{c['name']}** gespeichert.")

    @anmeldung.command(name="klasse_entfernen", description="Klasse entfernen (Anmeldungen bleiben in der Statistik)")
    @app_commands.autocomplete(klasse=class_autocomplete)
    async def klasse_entfernen(self, interaction: discord.Interaction, klasse: int):
        d = D.get(interaction.guild_id)
        c = assign.class_by_id(d, klasse)
        if c is None:
            return await reply(interaction, "Diese Klasse gibt es nicht.")
        assign.classes(d).remove(c)
        storage.save()
        await reply(interaction, f"🗑️ {c['emoji']} **{c['name']}** entfernt.")

    async def refresh_open_events(self, guild: discord.Guild, d: dict):
        for ev in d["events"].values():
            if ev["status"] == "posted":
                await actions.refresh(guild, d, ev)

    # ---------- /vorlage ----------

    @vorlage.command(name="erstellen", description="Neue Vorlage anlegen (z. B. „Liga-Rennen“)")
    @app_commands.describe(name="Name der Vorlage", kanal="Kanal, in dem die Anmeldungen gepostet werden")
    async def vorlage_erstellen(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 50], kanal: discord.TextChannel):
        d = D.get(interaction.guild_id)
        t = D.new_template(d, name, interaction.user.id)
        t["channel_id"] = kanal.id
        t["ping_role_ids"] = [c["role_id"] for c in assign.classes(d)][:10]
        storage.save()
        editor = TemplateEditor(interaction.guild, interaction.user.id, t["id"])
        editor.note = "✅ Angelegt. Jetzt Texte & Zeiten einstellen, danach Rennen mit `/kalender` eintragen."
        await interaction.response.send_message(embed=editor.render(), view=editor, ephemeral=True)

    @vorlage.command(name="bearbeiten", description="Einstellungen einer Vorlage ändern")
    @app_commands.autocomplete(vorlage=template_autocomplete)
    async def vorlage_bearbeiten(self, interaction: discord.Interaction, vorlage: str):
        if vorlage not in D.get(interaction.guild_id)["templates"]:
            return await reply(interaction, "Diese Vorlage gibt es nicht.")
        editor = TemplateEditor(interaction.guild, interaction.user.id, vorlage)
        await interaction.response.send_message(embed=editor.render(), view=editor, ephemeral=True)

    @vorlage.command(name="bild", description="Standard-Bild einer Vorlage setzen oder entfernen")
    @app_commands.describe(datei="Bild hochladen (leer lassen = Bild entfernen)")
    @app_commands.autocomplete(vorlage=template_autocomplete)
    async def vorlage_bild(self, interaction: discord.Interaction, vorlage: str, datei: discord.Attachment | None = None):
        d = D.get(interaction.guild_id)
        t = d["templates"].get(vorlage)
        if t is None:
            return await reply(interaction, "Diese Vorlage gibt es nicht.")
        try:
            t["image"] = await D.save_image(datei, f"vorlage{vorlage}") if datei else None
        except ValueError as e:
            return await reply(interaction, f"❌ {e}")
        storage.save()
        await reply(interaction, "✅ Bild gespeichert." if datei else "🗑️ Bild entfernt.")

    @vorlage.command(name="liste", description="Alle Vorlagen")
    async def vorlage_liste(self, interaction: discord.Interaction):
        d = D.get(interaction.guild_id)
        if not d["templates"]:
            return await reply(interaction, "Noch keine Vorlagen. Lege eine mit `/vorlage erstellen` an.")
        lines = []
        for tid, t in d["templates"].items():
            planned = sum(1 for e in d["events"].values() if e["template_id"] == tid and e["status"] == "planned")
            lines.append(f"**{t['name']}** – <#{t['channel_id']}> · {planned} geplante Rennen")
        await reply(interaction, "\n".join(lines))

    @vorlage.command(name="loeschen", description="Vorlage löschen (nur ohne geplante/offene Rennen)")
    @app_commands.autocomplete(vorlage=template_autocomplete)
    async def vorlage_loeschen(self, interaction: discord.Interaction, vorlage: str):
        d = D.get(interaction.guild_id)
        if vorlage not in d["templates"]:
            return await reply(interaction, "Diese Vorlage gibt es nicht.")
        if any(e["template_id"] == vorlage and e["status"] in ("planned", "posted") for e in d["events"].values()):
            return await reply(interaction, "Es gibt noch geplante oder offene Rennen mit dieser Vorlage. Erst diese entfernen.")
        name = d["templates"].pop(vorlage)["name"]
        storage.save()
        await reply(interaction, f"🗑️ Vorlage „{name}“ gelöscht. Vergangene Rennen bleiben für die Anwesenheit erhalten.")

    # ---------- /kalender ----------

    def get_template(self, d: dict, tid: str) -> dict | None:
        t = d["templates"].get(tid)
        return {**D.TEMPLATE_DEFAULTS, **t} if t else None

    @kalender.command(name="hinzufuegen", description="Ein Rennen in den Kalender eintragen")
    @app_commands.describe(vorlage="Vorlage", datum="z. B. 16.10.2026 (Uhrzeit aus der Vorlage) oder 16.10.2026 21:00",
                           titel="z. B. Abu Dhabi GP", bild="Eigenes Bild für dieses Rennen (optional)")
    @app_commands.autocomplete(vorlage=template_autocomplete)
    async def kalender_hinzufuegen(self, interaction: discord.Interaction, vorlage: str, datum: str,
                                   titel: app_commands.Range[str, 1, 200], bild: discord.Attachment | None = None):
        d = D.get(interaction.guild_id)
        t = self.get_template(d, vorlage)
        if t is None:
            return await reply(interaction, "Diese Vorlage gibt es nicht.")
        try:
            starts_at = tu.parse_datetime(datum, t["event_time"])
            image = await D.save_image(bild, "event") if bild else None
        except ValueError as e:
            return await reply(interaction, f"❌ {e}")
        ev = D.new_event(d, t, starts_at, titel.strip(), interaction.user.id)
        ev["image"] = image
        storage.save()
        await reply(interaction, f"✅ Eingetragen: **{event_label(d, ev)}** am {tu.pretty(starts_at)}\n" + self.schedule_text(d, ev))

    @kalender.command(name="woechentlich", description="Mehrere Rennen im Wochenabstand anlegen (Titel danach anpassen)")
    @app_commands.describe(vorlage="Vorlage", erstes_datum="Datum des ersten Rennens, z. B. 16.10.2026",
                           anzahl="Wie viele Wochen", titel="Platzhalter-Titel, „{n}“ = laufende Nummer")
    @app_commands.autocomplete(vorlage=template_autocomplete)
    async def kalender_woechentlich(self, interaction: discord.Interaction, vorlage: str, erstes_datum: str,
                                    anzahl: app_commands.Range[int, 1, 52], titel: str = "Rennen {n}"):
        d = D.get(interaction.guild_id)
        t = self.get_template(d, vorlage)
        if t is None:
            return await reply(interaction, "Diese Vorlage gibt es nicht.")
        try:
            first = tu.parse_datetime(erstes_datum, t["event_time"])
        except ValueError as e:
            return await reply(interaction, f"❌ {e}")
        created = []
        for n in range(anzahl):
            ev = D.new_event(d, t, tu.shift(first, 7 * n), titel.replace("{n}", str(n + 1)), interaction.user.id)
            created.append(ev)
        storage.save()
        hint = ("Du bekommst vor jedem Posten eine DM zur Freigabe – dann Strecke/Titel anpassen."
                if t["review"] else "⚠️ Freigabe ist bei dieser Vorlage aus: Die Rennen werden mit diesen Titeln automatisch gepostet.")
        lines = [f"• {tu.pretty(e['starts_at'])} – {e['title']} (#{e['id']})" for e in created]
        await reply(interaction, (f"✅ {len(created)} Rennen angelegt:\n" + "\n".join(lines))[:1800] + f"\n\n{hint}")

    def schedule_text(self, d: dict, ev: dict) -> str:
        t = render.times(d, ev)
        tmpl = D.template_of(d, ev)
        text = f"📢 Posten <t:{tu.ts(t['post'])}:f> · 📝 Frist <t:{tu.ts(t['deadline'])}:f>"
        if tmpl["review"] and not ev["confirmed"]:
            text += f" · ✋ Freigabe-DM <t:{tu.ts(t['post'] - timedelta(hours=tmpl['review_hours']))}:R>"
        return text

    @kalender.command(name="liste", description="Rennkalender anzeigen")
    @app_commands.describe(vorlage="Nur diese Vorlage", alle="Auch vergangene/ausgesetzte Rennen zeigen")
    @app_commands.autocomplete(vorlage=template_autocomplete)
    async def kalender_liste(self, interaction: discord.Interaction, vorlage: str | None = None, alle: bool = False):
        d = D.get(interaction.guild_id)
        evs = [e for e in D.sorted_events(d, vorlage) if alle or e["status"] in ("planned", "posted")]
        if not evs:
            return await reply(interaction, "Keine Rennen im Kalender. Eintragen mit `/kalender hinzufuegen` oder `/kalender woechentlich`.")
        lines = []
        for e in evs:
            ok = " ✋ wartet auf Freigabe" if e["status"] == "planned" and D.template_of(d, e)["review"] and not e["confirmed"] else ""
            lines.append(f"`#{e['id']}` {tu.pretty(e['starts_at'])} – **{D.field(d, e, 'title')}** · {D.STATUS_LABEL[e['status']]}{ok}")
        chunks, cur = [], ""
        for line in lines:
            if len(cur) + len(line) > 3900:
                chunks.append(cur)
                cur = ""
            cur += line + "\n"
        chunks.append(cur)
        embeds = [discord.Embed(color=0x3B82F6, description=c) for c in chunks[:10]]
        embeds[0].title = "🗓️ Rennkalender"
        await interaction.response.send_message(embeds=embeds, ephemeral=True)

    def find_event(self, d: dict, ev_id: str) -> dict | None:
        return d["events"].get(ev_id)

    @kalender.command(name="bearbeiten", description="Titel, Beschreibung, Datum oder Bild eines Rennens ändern (= Freigabe)")
    @app_commands.autocomplete(eintrag=event_autocomplete)
    async def kalender_bearbeiten(self, interaction: discord.Interaction, eintrag: str):
        ev = self.find_event(D.get(interaction.guild_id), eintrag)
        if ev is None or ev["status"] not in ("planned", "posted"):
            return await reply(interaction, "Nur geplante oder offene Rennen können bearbeitet werden.")
        await interaction.response.send_modal(review.EventEditModal(interaction.guild, eintrag))

    @kalender.command(name="verschieben", description="Rennen verschieben – auf Wunsch rutschen alle späteren mit")
    @app_commands.describe(tage="Um wie viele Tage (Standard 7 = eine Woche, negativ = früher)",
                           nachfolgende="Alle späteren Rennen derselben Vorlage mitverschieben (Standard: ja)")
    @app_commands.autocomplete(eintrag=event_autocomplete)
    async def kalender_verschieben(self, interaction: discord.Interaction, eintrag: str,
                                   tage: app_commands.Range[int, -365, 365] = 7, nachfolgende: bool = True):
        d = D.get(interaction.guild_id)
        ev = self.find_event(d, eintrag)
        if ev is None or ev["status"] not in ("planned", "posted"):
            return await reply(interaction, "Nur geplante oder offene Rennen können verschoben werden.")
        moved = [ev]
        if nachfolgende:
            moved += [e for e in D.sorted_events(d, ev["template_id"])
                      if e is not ev and e["starts_at"] > ev["starts_at"] and e["status"] == "planned"]
        for e in moved:
            e["starts_at"] = tu.shift(e["starts_at"], tage)
        storage.save()
        await interaction.response.defer(ephemeral=True, thinking=True)
        if ev["status"] == "posted":
            await actions.refresh(interaction.guild, d, ev)
        lines = [f"• {D.field(d, e, 'title')} → {tu.pretty(e['starts_at'])}" for e in moved]
        await interaction.followup.send((f"✅ {len(moved)} Rennen verschoben:\n" + "\n".join(lines))[:2000], ephemeral=True)

    @kalender.command(name="aussetzen", description="Rennen aussetzen: wird nicht gepostet (die anderen bleiben, wo sie sind)")
    @app_commands.autocomplete(eintrag=event_autocomplete)
    async def kalender_aussetzen(self, interaction: discord.Interaction, eintrag: str):
        d = D.get(interaction.guild_id)
        ev = self.find_event(d, eintrag)
        if ev is None or ev["status"] != "planned":
            return await reply(interaction, "Nur geplante (noch nicht gepostete) Rennen können ausgesetzt werden.")
        ev["status"] = "skipped"
        storage.save()
        await reply(interaction, f"⏭️ **{D.field(d, ev, 'title')}** ({tu.pretty(ev['starts_at'])}) wird nicht gepostet.")

    @kalender.command(name="freigeben", description="Rennen so freigeben, wie es ist")
    @app_commands.autocomplete(eintrag=event_autocomplete)
    async def kalender_freigeben(self, interaction: discord.Interaction, eintrag: str):
        d = D.get(interaction.guild_id)
        ev = self.find_event(d, eintrag)
        if ev is None or ev["status"] != "planned":
            return await reply(interaction, "Nur geplante Rennen können freigegeben werden.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(await review.confirm(interaction.guild, d, ev), ephemeral=True)

    @kalender.command(name="posten", description="Rennen sofort posten (statt zum geplanten Zeitpunkt)")
    @app_commands.autocomplete(eintrag=event_autocomplete)
    async def kalender_posten(self, interaction: discord.Interaction, eintrag: str):
        d = D.get(interaction.guild_id)
        ev = self.find_event(d, eintrag)
        if ev is None or ev["status"] != "planned":
            return await reply(interaction, "Nur geplante Rennen können gepostet werden.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(await actions.post(interaction.guild, d, ev), ephemeral=True)

    @kalender.command(name="entfernen", description="Rennen löschen (eine schon gepostete Anmeldung wird mitgelöscht)")
    @app_commands.autocomplete(eintrag=event_autocomplete)
    async def kalender_entfernen(self, interaction: discord.Interaction, eintrag: str):
        d = D.get(interaction.guild_id)
        ev = d["events"].pop(eintrag, None)
        if ev is None:
            return await reply(interaction, "Dieses Rennen gibt es nicht.")
        storage.save()
        channel = interaction.guild.get_channel(ev.get("channel_id") or 0)
        for msg_id in (ev.get("message_id"), ev.get("assignment_message_id")):
            if channel and msg_id:
                try:
                    await channel.get_partial_message(msg_id).delete()
                except discord.HTTPException:
                    pass
        await reply(interaction, f"🗑️ **{D.field(d, ev, 'title')}** gelöscht.")

    # ---------- /event ----------

    @event.command(name="teilnehmer", description="Jemanden an-/abmelden (z. B. wenn er sich per Nachricht gemeldet hat)")
    @app_commands.choices(status=STATUS_CHOICES)
    @app_commands.autocomplete(eintrag=event_autocomplete)
    async def teilnehmer(self, interaction: discord.Interaction, eintrag: str, fahrer: discord.Member,
                         status: app_commands.Choice[str]):
        d = D.get(interaction.guild_id)
        ev = self.find_event(d, eintrag)
        if ev is None or ev["status"] not in ("posted", "closed"):
            return await reply(interaction, "Das geht nur bei geposteten oder vergangenen Rennen.")
        uid = str(fahrer.id)
        if status.value == "remove":
            ev["signups"].pop(uid, None)
            ev["assignments"].pop(uid, None)
            text = f"🗑️ {fahrer.mention} aus **{D.field(d, ev, 'title')}** entfernt."
        else:
            cls = assign.class_for(fahrer, d)
            if cls is None:
                return await reply(interaction, f"{fahrer.mention} hat keine Anmelde-Rolle.")
            _, late = actions.set_signup(d, ev, uid, status.value, cls["id"], fahrer.display_name)
            text = f"{actions.STATUS_TEXT[status.value]}: {fahrer.mention} bei **{D.field(d, ev, 'title')}**."
            if late:
                text += " Nach der Frist → mit ⏰ (Q-Sperre) markiert, aber ohne DM an ihn."
        storage.save()
        await interaction.response.defer(ephemeral=True, thinking=True)
        if ev["status"] == "posted":
            await actions.refresh(interaction.guild, d, ev)
        else:
            await actions.update_message(interaction.guild, d, ev)
        await interaction.followup.send(text, ephemeral=True)

    @event.command(name="neu_einteilen", description="Ersatzfahrer komplett neu auslosen")
    @app_commands.autocomplete(eintrag=event_autocomplete)
    async def neu_einteilen(self, interaction: discord.Interaction, eintrag: str):
        d = D.get(interaction.guild_id)
        ev = self.find_event(d, eintrag)
        if ev is None or ev["status"] != "posted":
            return await reply(interaction, "Das geht nur bei offenen Rennen.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        ev["assignments"] = {}
        ev["deadline_done"] = True
        await actions.refresh(interaction.guild, d, ev)
        await interaction.followup.send("🔁 Neu eingeteilt.", ephemeral=True)

    # ---------- /anwesenheit ----------

    @app_commands.command(name="anwesenheit", description="Anwesenheit aller Fahrer in Prozent (vergangene Rennen)")
    @app_commands.describe(vorlage="Nur Rennen dieser Vorlage", oeffentlich="Für alle sichtbar posten")
    @app_commands.autocomplete(vorlage=template_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def anwesenheit(self, interaction: discord.Interaction, vorlage: str | None = None, oeffentlich: bool = False):
        d = D.get(interaction.guild_id)
        rows, total = attendance.stats(interaction.guild, d, vorlage)
        if not rows:
            return await reply(interaction, "Keine Fahrer gefunden. Sind Anmelde-Rollen eingetragen (`/anmeldung klassen`)?")
        title = f"Anwesenheit {d['templates'][vorlage]['name']}" if vorlage in d["templates"] else "Anwesenheit"
        await interaction.response.send_message(embeds=attendance.embeds(rows, total, title), ephemeral=not oeffentlich)
