"""Verifizierung aus Sicht des Users.

Ablauf: Start-Button im Panel -> Formular (Discord-Dialog) -> privates Overlay ("Nur du kannst das sehen")
mit Fortschritt, Fahrernummer-Auswahl und Zusammenfassung -> Abschicken -> Antrag landet im Mod-Kanal.

Die Fragen werden auf Seiten verteilt (max. 5 pro Formular). Die Fahrernummer hat eine eigene Seite im
Overlay, weil dort alle freien Nummern als Auswahl angezeigt werden (ein Formular-Menü kann nur 25).
Laufende Verifizierungen liegen nur im Speicher: Nach einem Neustart des Bots muss der User neu starten.
"""
import logging
import re
import time
from dataclasses import dataclass, field

import discord

from gfsl import storage

from . import data, drivers, review
from . import questions as Q
from .constants import Colors
from .flags import lookup_flag

log = logging.getLogger("gfsl.verify")

SESSION_TTL = 2 * 3600
START_ID = "gv:start"


@dataclass
class Session:
    guild_id: int
    user_id: int
    questions: list[dict]       # Momentaufnahme, falls Fragen währenddessen geändert werden
    preview: bool = False       # Testlauf für Admins: es wird nichts gespeichert
    page: int = 0
    answers: dict = field(default_factory=dict)   # "frage-id" -> Wert
    raw: dict = field(default_factory=dict)       # "frage-id" -> Eingabe, um das Formular nach Fehlern vorzubefüllen
    files: dict = field(default_factory=dict)     # "frage-id" -> [discord.Attachment]
    errors: list = field(default_factory=list)
    back_to_summary: bool = False                 # beim Bearbeiten danach direkt zurück zur Zusammenfassung
    number_chunk: int | None = None
    started: float = field(default_factory=time.time)
    view: discord.ui.View | None = None
    submitted: bool = False

    @property
    def key(self):
        return self.guild_id, self.user_id, self.preview

    @property
    def pages(self) -> list[dict]:
        return Q.pages(self.questions)

    @property
    def finished(self) -> bool:
        return self.page >= len(self.pages)

    @property
    def active(self) -> bool:
        return sessions.get(self.key) is self


sessions: dict[tuple, Session] = {}


def get_session(guild_id: int, user_id: int, preview: bool) -> Session | None:
    s = sessions.get((guild_id, user_id, preview))
    if s and time.time() - s.started > SESSION_TTL:
        del sessions[s.key]
        return None
    return s


def start_blocker(d: dict, user_id: int) -> str | None:
    """Grund, warum jemand die Verifizierung (noch) nicht starten kann."""
    if not d["questions"]:
        return "Es sind noch keine Fragen eingerichtet. Bitte einem Admin Bescheid geben."
    if not d["settings"]["mod_channel_id"]:
        return "Der Bot ist noch nicht eingerichtet (`/verify setup`). Bitte einem Admin Bescheid geben."
    drv = d["drivers"].get(str(user_id))
    if drv:
        nr = f" (Fahrernummer #{drv['number']})" if drv.get("number") is not None else ""
        return f"Du bist bereits verifiziert{nr}."
    if any(a["user_id"] == str(user_id) and a["status"] == "pending" for a in d["applications"].values()):
        return "Deine Verifizierung wird gerade von den Mods geprüft. ⏳"
    return None


# ---------- Panel ----------

def panel_embed(s: dict) -> discord.Embed:
    return discord.Embed(color=Colors.info, title=s["panel_title"], description=s["panel_text"])


class PanelView(discord.ui.View):
    """Der Start-Button. Bleibt nach einem Neustart klickbar (fester custom_id, timeout=None)."""

    def __init__(self, label: str = "Verifizierung starten"):
        super().__init__(timeout=None)
        button = discord.ui.Button(label=label, style=discord.ButtonStyle.success, emoji="🏎️", custom_id=START_ID)
        button.callback = start
        self.add_item(button)


async def start(interaction: discord.Interaction, preview: bool = False):
    d = data.get(interaction.guild_id)
    if not preview and (reason := start_blocker(d, interaction.user.id)):
        return await interaction.response.send_message(reason, ephemeral=True)
    if preview and not d["questions"]:
        return await interaction.response.send_message("Noch keine Fragen. Nutze `/fragen standard` oder `/fragen neu`.", ephemeral=True)

    session = get_session(interaction.guild_id, interaction.user.id, preview)
    if session is None or preview:
        session = Session(interaction.guild_id, interaction.user.id, [dict(q) for q in d["questions"]], preview=preview)
        sessions[session.key] = session

    # Direkt mit dem Formular loslegen, wenn die aktuelle Seite eins ist
    if not session.finished and session.pages[session.page]["kind"] == "modal" and not session.errors:
        await interaction.response.send_modal(PageModal(session, session.page, from_overlay=False))
    else:
        await show_overlay(interaction, session, edit=False)


# ---------- Formular (Dialog) ----------

def build_input(q: dict, settings: dict, session: Session) -> discord.ui.Item:
    k = str(q["id"])
    answer = session.answers.get(k)
    t = q["type"]
    if t in ("text", "paragraph", "number", "time"):
        lo, hi = Q.range_for(q, settings)
        kwargs = {"required": q["required"], "default": session.raw.get(k) or None}
        if t in ("text", "paragraph"):
            kwargs.update(min_length=lo, max_length=hi,
                          style=discord.TextStyle.long if t == "paragraph" else discord.TextStyle.short)
        elif t == "number":
            rng = f"{'…' if lo is None else lo} bis {'…' if hi is None else hi}"
            kwargs.update(max_length=9, placeholder=f"Zahl ({rng})" if lo is not None or hi is not None else "Zahl")
        else:
            kwargs.update(max_length=12, placeholder="z. B. 1:05.013")
        return discord.ui.TextInput(**kwargs)
    if t in ("select", "flag"):
        options = [
            discord.SelectOption(label=o[:100], value=str(n), default=(o == answer),
                                 emoji=lookup_flag(o) if t == "flag" else None)
            for n, o in enumerate(Q.options_for(q)[:25])
        ]
        return discord.ui.Select(options=options, placeholder="Bitte auswählen …", required=q["required"],
                                 min_values=1 if q["required"] else 0, max_values=1)
    if t == "yesno":
        return discord.ui.RadioGroup(required=q["required"], options=[
            discord.RadioGroupOption(label="Ja", value="1", default=answer is True),
            discord.RadioGroupOption(label="Nein", value="0", default=answer is False),
        ])
    if t == "confirm":
        return discord.ui.CheckboxGroup(required=q["required"], min_values=1 if q["required"] else 0, max_values=1, options=[
            discord.CheckboxGroupOption(label="Erledigt / bestätigt", value="ok", default=bool(answer)),
        ])
    if t == "image":
        # Beim Bearbeiten bleiben schon hochgeladene Bilder erhalten, wenn nichts Neues kommt
        required = q["required"] and not session.files.get(k)
        return discord.ui.FileUpload(required=required, min_values=1 if required else 0, max_values=4)
    raise ValueError(f"Unbekannter Fragetyp {t}")


def read_input(q: dict, item: discord.ui.Item, settings: dict, session: Session) -> str | None:
    """Übernimmt eine Antwort in die Session. Gibt bei ungültiger Eingabe eine Fehlermeldung zurück."""
    k = str(q["id"])
    t = q["type"]
    value = None
    if t in ("text", "paragraph", "number", "time"):
        raw = item.value.strip()
        session.raw[k] = raw
        if raw:
            lo, hi = Q.range_for(q, settings)
            if t in ("text", "paragraph"):
                if len(raw) < lo or len(raw) > hi:
                    return f"{lo}–{hi} Zeichen erlaubt."
                value = raw
            elif t == "number":
                if not re.fullmatch(r"\d+", raw):
                    return "Bitte nur eine ganze Zahl eingeben."
                value = int(raw)
                if (lo is not None and value < lo) or (hi is not None and value > hi):
                    return f"Die Zahl muss zwischen {'…' if lo is None else lo} und {'…' if hi is None else hi} liegen."
            else:
                value = Q.parse_time(raw)
                if value is None:
                    return "Bitte im Format `1:05.013` oder `1.05.013` eingeben."
    elif t in ("select", "flag"):
        if item.values:
            value = Q.options_for(q)[int(item.values[0])]
    elif t == "yesno":
        if item.value is not None:
            value = item.value == "1"
    elif t == "confirm":
        value = bool(item.values)
        if not value and not q["required"]:
            value = None
    elif t == "image":
        uploads = item.values
        images = [a for a in uploads if (a.content_type or "").startswith("image/")]
        if uploads and not images:
            return "Bitte nur Bilder (Screenshots) hochladen."
        if images:
            session.files[k] = images[:4]
        if session.files.get(k):
            value = [a.filename for a in session.files[k]]

    if value is None and q["required"]:
        return "Das ist ein Pflichtfeld."
    session.answers[k] = value
    return None


class PageModal(discord.ui.Modal):
    def __init__(self, session: Session, page_index: int, from_overlay: bool = True):
        pages = session.pages
        super().__init__(title=f"GFSL Verifizierung · Teil {page_index + 1}/{len(pages)}", timeout=1800)
        self.session = session
        self.page_index = page_index
        self.from_overlay = from_overlay  # vom Overlay geöffnet -> Overlay bearbeiten, sonst neu senden
        self.inputs: list[tuple[dict, discord.ui.Item]] = []
        settings = data.get(session.guild_id)["settings"]
        for q in pages[page_index]["questions"]:
            item = build_input(q, settings, session)
            self.add_item(discord.ui.Label(text=q["label"], description=q["hint"] or None, component=item))
            self.inputs.append((q, item))

    async def on_submit(self, interaction: discord.Interaction):
        s = self.session
        if not s.active:
            return await interaction.response.send_message("Diese Verifizierung ist abgelaufen. Bitte über den Button neu starten.", ephemeral=True)
        settings = data.get(s.guild_id)["settings"]
        s.errors = []
        for q, item in self.inputs:
            if err := read_input(q, item, settings, s):
                s.answers.pop(str(q["id"]), None)
                s.errors.append(f"**{q['label']}:** {err}")
        if not s.errors and s.page == self.page_index:
            advance(s)
        await show_overlay(interaction, s, edit=self.from_overlay)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await report_error(interaction, error)


def advance(s: Session):
    if s.back_to_summary:
        s.page = len(s.pages)
        s.back_to_summary = False
    else:
        s.page += 1


async def open_current_modal(interaction: discord.Interaction, s: Session):
    if not s.finished and s.pages[s.page]["kind"] == "modal":
        await interaction.response.send_modal(PageModal(s, s.page))
    else:
        await show_overlay(interaction, s)


# ---------- Overlay (nur für den User sichtbar) ----------

class Overlay(discord.ui.View):
    def __init__(self, session: Session):
        super().__init__(timeout=3600)
        self.session = session

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not self.session.active:
            await interaction.response.edit_message(
                embed=discord.Embed(color=Colors.bad, description="Diese Verifizierung ist abgelaufen. Bitte über den Button neu starten."),
                view=None)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        await report_error(interaction, error)

    def button(self, label, style, callback, emoji=None, disabled=False, row=None):
        b = discord.ui.Button(label=label, style=style, emoji=emoji, disabled=disabled, row=row)
        b.callback = callback
        self.add_item(b)
        return b


async def report_error(interaction: discord.Interaction, error: Exception):
    log.exception("Fehler in der Verifizierung", exc_info=error)
    msg = f"⚠️ Fehler: {error}"
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


def answered_lines(s: Session, upto_page: int | None = None) -> str:
    pages = s.pages if upto_page is None else s.pages[:upto_page]
    lines = []
    for page in pages:
        for q in page["questions"]:
            k = str(q["id"])
            if k in s.answers:
                lines.append(f"✅ **{q['label']}:** {Q.display(q, s.answers[k])}")
    return "\n".join(lines)


async def show_overlay(interaction: discord.Interaction, s: Session, note: str | None = None, edit: bool = True):
    """edit=True: die Overlay-Nachricht, von der die Interaktion kommt, wird ersetzt. Sonst neu senden."""
    embed, view = build_overlay(s, note)
    if s.view:
        s.view.stop()
    s.view = view
    if edit:
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


def build_overlay(s: Session, note: str | None = None) -> tuple[discord.Embed, discord.ui.View]:
    view = Overlay(s)
    title = "🧪 Testlauf – GFSL Verifizierung" if s.preview else "🏁 GFSL Verifizierung"
    if s.finished:
        embed = build_summary_page(s, view)
    elif s.pages[s.page]["kind"] == "number":
        embed = build_number_page(s, view)
    else:
        embed = build_modal_page(s, view)
    embed.title = embed.title or title
    if note:
        embed.description = f"{note}\n\n{embed.description or ''}"
    view.button("Abbrechen", discord.ButtonStyle.secondary, lambda i: cancel(i, s), emoji="✖️", row=4)
    return embed, view


def build_modal_page(s: Session, view: Overlay) -> discord.Embed:
    total = len(s.pages)
    desc = [f"**Schritt {s.page + 1} von {total}**"]
    if s.errors:
        desc.append("⚠️ **Bitte korrigieren:**\n" + "\n".join(f"• {e}" for e in s.errors))
    if done := answered_lines(s, s.page):
        desc.append(done)
    label = f"Teil {s.page + 1}/{total} erneut öffnen" if s.errors else f"Teil {s.page + 1}/{total} ausfüllen"
    view.button(label, discord.ButtonStyle.primary, lambda i: open_current_modal(i, s), emoji="📝")
    return discord.Embed(color=Colors.bad if s.errors else Colors.info, description="\n\n".join(desc))


def number_chunks(free: list[int]) -> list[list[int]]:
    return [free[i:i + 25] for i in range(0, len(free), 25)]


def build_number_page(s: Session, view: Overlay) -> discord.Embed:
    page_index = s.page
    q = s.pages[page_index]["questions"][0]
    k = str(q["id"])
    d = data.get(s.guild_id)
    chosen = s.answers.get(k)
    free = drivers.free_numbers(d, str(s.user_id))
    taken = sorted(drivers.taken_numbers(d, str(s.user_id)))
    chunks = number_chunks(free)

    if s.number_chunk is None or s.number_chunk >= len(chunks):
        s.number_chunk = next((n for n, c in enumerate(chunks) if chosen in c), 0)

    desc = [f"**Schritt {s.page + 1} von {len(s.pages)}**", f"**{q['label']}**"]
    if q["hint"]:
        desc.append(q["hint"])
    desc.append(f"Gewählt: **#{chosen}**" if chosen is not None else "Wähle unten eine der freien Nummern.")
    if taken:
        txt = ", ".join(map(str, taken))
        desc.append(f"Bereits vergeben: {txt if len(txt) < 900 else txt[:900] + ' …'}")

    async def go_next(i: discord.Interaction):
        if s.page == page_index:
            advance(s)
        await open_current_modal(i, s)

    if not chunks:
        desc.append("⚠️ Es ist keine Nummer mehr frei. Die Mods vergeben dir eine.")
        s.answers[k] = None
        view.button("Weiter", discord.ButtonStyle.success, go_next, emoji="▶️")
        return discord.Embed(color=Colors.info, description="\n\n".join(desc))

    if len(chunks) > 1:
        rng = discord.ui.Select(placeholder="Nummernbereich wählen …", row=0, options=[
            discord.SelectOption(label=f"Nummern {c[0]}–{c[-1]}", description=f"{len(c)} frei", value=str(n),
                                 default=(n == s.number_chunk))
            for n, c in enumerate(chunks)
        ])

        async def pick_range(i: discord.Interaction):
            s.number_chunk = int(rng.values[0])
            await show_overlay(i, s)
        rng.callback = pick_range
        view.add_item(rng)

    nr = discord.ui.Select(placeholder="Freie Nummer wählen …", row=1, options=[
        discord.SelectOption(label=f"#{n}", value=str(n), default=(n == chosen)) for n in chunks[s.number_chunk]
    ])

    async def pick_number(i: discord.Interaction):
        s.answers[k] = int(nr.values[0])
        await show_overlay(i, s)
    nr.callback = pick_number
    view.add_item(nr)

    view.button("Weiter", discord.ButtonStyle.success, go_next, emoji="▶️", disabled=chosen is None and q["required"], row=2)
    if not q["required"]:
        async def skip(i: discord.Interaction):
            s.answers[k] = None
            await go_next(i)
        view.button("Überspringen", discord.ButtonStyle.secondary, skip, emoji="⏭️", row=2)
    return discord.Embed(color=Colors.info, description="\n\n".join(desc))


def build_summary_page(s: Session, view: Overlay) -> discord.Embed:
    embed = discord.Embed(color=Colors.wait, title="Fast geschafft – bitte prüfen",
                          description="Stimmt alles? Dann schick deine Angaben an die Mods.")
    for q in s.questions:
        value = Q.display(q, s.answers.get(str(q["id"])))
        embed.add_field(name=q["label"], value=value[:1024], inline=False)

    view.button("Abschicken", discord.ButtonStyle.success, lambda i: submit(i, s), emoji="📨")
    edit = discord.ui.Select(placeholder="✏️ Etwas ändern …", row=1, options=[
        discord.SelectOption(label=f"Teil {n + 1}: " + ", ".join(q["label"] for q in p["questions"])[:90], value=str(n))
        for n, p in enumerate(s.pages)
    ])

    async def edit_page(i: discord.Interaction):
        s.page = int(edit.values[0])
        s.back_to_summary = True
        s.errors = []
        await open_current_modal(i, s)
    edit.callback = edit_page
    view.add_item(edit)
    return embed


async def cancel(interaction: discord.Interaction, s: Session):
    sessions.pop(s.key, None)
    s.view.stop()
    await interaction.response.edit_message(
        embed=discord.Embed(color=Colors.bad, description="Verifizierung abgebrochen. Du kannst sie über den Button jederzeit neu starten."),
        view=None)


# ---------- Abschicken ----------

async def submit(interaction: discord.Interaction, s: Session):
    if s.submitted:
        return await interaction.response.defer()
    d = data.get(s.guild_id)

    app = {
        "id": str(d["next_app_id"]),
        "user_id": str(s.user_id),
        "questions": s.questions,
        "answers": dict(s.answers),
        "status": "pending",
        "created_at": int(time.time()),
    }

    if s.preview:
        sessions.pop(s.key, None)
        s.view.stop()
        mod_embed = await review.build_summary(interaction.client, interaction.guild, d, app, Colors.wait, "⏳ Wartet auf Prüfung",
                                               review.number_preview(d, app))
        info = discord.Embed(color=Colors.ok, description="🧪 **Testlauf beendet – es wurde nichts gespeichert.**\n"
                                                          "So würde der Antrag im Mod-Kanal aussehen:")
        return await interaction.response.edit_message(embeds=[info, mod_embed], view=None)

    if reason := start_blocker(d, s.user_id):
        return await show_overlay(interaction, s, note=f"⚠️ {reason}")
    mod_channel = interaction.guild.get_channel(d["settings"]["mod_channel_id"])
    if mod_channel is None:
        return await show_overlay(interaction, s, note="⚠️ Der Mod-Kanal wurde nicht gefunden. Bitte einem Admin Bescheid geben.")

    s.submitted = True
    await interaction.response.edit_message(embed=discord.Embed(color=Colors.wait, description="⏳ Wird gesendet …"), view=None)

    files = []
    for q in s.questions:
        for attachment in s.files.get(str(q["id"]), []):
            try:
                files.append(await attachment.to_file())
            except discord.HTTPException as e:
                log.warning("Screenshot nicht geladen: %s", e)

    d["next_app_id"] += 1
    d["applications"][app["id"]] = app
    storage.save()
    try:
        await review.post_application(interaction.client, mod_channel, d, app, files[:10])
    except discord.HTTPException as e:
        del d["applications"][app["id"]]
        storage.save()
        s.submitted = False
        log.exception("Antrag konnte nicht gesendet werden")
        embed, view = build_overlay(s, note=f"⚠️ Senden fehlgeschlagen ({e.text or e}). Bitte nochmal versuchen oder einem Admin Bescheid geben.")
        s.view = view
        return await interaction.edit_original_response(embed=embed, view=view)

    sessions.pop(s.key, None)
    await interaction.edit_original_response(embed=discord.Embed(
        color=Colors.ok,
        description="📨 **Danke!** Deine Angaben wurden an die Mods geschickt.\n"
                    "Du bekommst eine Direktnachricht, sobald du freigeschaltet bist."))
