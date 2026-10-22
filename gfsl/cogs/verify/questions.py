"""Fragen: Prüfen, Beschreiben, Standardfragen und Aufteilung auf Formular-Seiten."""
import re

from .constants import HINT_MAX, LABEL_MAX, MODAL_PAGE_SIZE, PURPOSE_TYPES, PURPOSES, TYPES
from .flags import DEFAULT_FLAG_OPTIONS, flag_for


def parse_options(text: str | None) -> list[str]:
    if not text:
        return []
    return [s.strip() for s in re.split(r"[,;\n]", text) if s.strip()]


def options_for(q: dict) -> list[str]:
    if q["type"] == "flag":
        return q["options"] or DEFAULT_FLAG_OPTIONS
    if q["type"] == "yesno":
        return ["Ja", "Nein"]
    return q["options"]


def is_number_picker(q: dict) -> bool:
    """Die Fahrernummer wird nicht im Formular abgefragt, sondern über eine Auswahl aller freien Nummern."""
    return q["type"] == "number" and q["purpose"] == "number"


def range_for(q: dict, settings: dict) -> tuple[int | None, int | None]:
    """Erlaubter Bereich: bei Zahlen der Wert, bei Freitext die Zeichenanzahl."""
    if q["type"] == "number":
        nr = q["purpose"] == "number"
        lo = q["min"] if q["min"] is not None else (settings["number_min"] if nr else None)
        hi = q["max"] if q["max"] is not None else (settings["number_max"] if nr else None)
        return lo, hi
    if q["type"] in ("text", "paragraph"):
        default_max = 32 if q["purpose"] == "name" else (1000 if q["type"] == "paragraph" else 300)
        return (q["min"] if q["min"] is not None else 1), (q["max"] if q["max"] is not None else default_max)
    return None, None


def validate(q: dict, questions: list[dict]) -> str | None:
    if q["type"] not in TYPES:
        return "Unbekannter Fragetyp."
    if not q["label"] or len(q["label"]) > LABEL_MAX:
        return f"Die Frage muss 1–{LABEL_MAX} Zeichen lang sein (Discord-Limit). Längere Erklärungen gehören in `hinweis`."
    if q["hint"] and len(q["hint"]) > HINT_MAX:
        return f"Der Hinweis darf maximal {HINT_MAX} Zeichen haben."
    if q["type"] == "select" and len(q["options"]) < 2:
        return "Für „Auswahl“ brauchst du mindestens 2 Optionen, z. B. `PlayStation, Xbox, PC`."
    if q["type"] in ("select", "flag") and len(q["options"]) > 25:
        return "Discord erlaubt maximal 25 Optionen pro Menü."
    if any(len(o) > 100 for o in q["options"]):
        return "Eine Option darf maximal 100 Zeichen haben."
    if q["min"] is not None and q["max"] is not None and q["min"] > q["max"]:
        return "`min` ist größer als `max`."
    if q["type"] in ("text", "paragraph") and q["max"] is not None and q["max"] > 4000:
        return "Freitext darf maximal 4000 Zeichen lang sein."
    if q["purpose"] != "none":
        allowed = PURPOSE_TYPES[q["purpose"]]
        if q["type"] not in allowed:
            ok = ", ".join(TYPES[t] for t in allowed)
            return f"Das Feld „{PURPOSES[q['purpose']]}“ passt nur zum Typ: {ok}."
        dup = next((o for o in questions if o["id"] != q["id"] and o["purpose"] == q["purpose"]), None)
        if dup:
            return f"Es gibt schon eine Frage mit dem Feld „{PURPOSES[q['purpose']]}“: „{dup['label']}“."
    return None


def describe(q: dict, index: int, settings: dict) -> str:
    parts = [TYPES[q["type"]], "Pflicht" if q["required"] else "optional"]
    if q["purpose"] != "none":
        parts.append(f"→ {PURPOSES[q['purpose']]}")
    lo, hi = range_for(q, settings)
    if q["type"] == "number" and (lo is not None or hi is not None):
        parts.append(f"{'…' if lo is None else lo}–{'…' if hi is None else hi}")
    if q["type"] in ("text", "paragraph"):
        parts.append(f"max. {hi} Zeichen")
    line = f"**{index + 1}.** {q['label']}"
    if q["hint"]:
        line += f"\n└ _{q['hint']}_"
    line += f"\n└ {' · '.join(parts)}"
    if q["type"] in ("select", "flag"):
        opts = options_for(q)
        txt = f"Standard-Flaggenliste ({len(opts)} Länder)" if q["type"] == "flag" and not q["options"] else ", ".join(opts)
        line += f"\n└ Optionen: {txt}"
    return line


def new_question(qid: int, type_: str, label: str, **extra) -> dict:
    q = {"id": qid, "type": type_, "label": label, "hint": None, "required": True,
         "options": [], "min": None, "max": None, "purpose": "none"}
    q.update(extra)
    return q


TIME_HINT = "Format: 1:05.013 oder 1.05.013"


def default_questions(start_id: int) -> tuple[list[dict], int]:
    """Startvorlage, angelehnt an eure bisherigen Melpo-Fragen."""
    specs = [
        ("select", "Auf welcher Plattform spielst du F1?", {"options": ["PlayStation", "Xbox", "PC"], "purpose": "platform"}),
        ("text", "Ingame-Name (PSN- bzw. EA-Name)", {"purpose": "name", "max": 32}),
        ("time", "Zeitfahrzeit Österreich", {"hint": TIME_HINT}),
        ("time", "Zeitfahrzeit Japan", {"hint": TIME_HINT}),
        ("time", "Zeitfahrzeit Baku", {"hint": TIME_HINT}),
        ("image", "Screenshot deiner Zeitfahrzeiten", {"required": False, "hint": "Bis zu 4 Bilder"}),
        ("flag", "Flagge für die Rennergebnisse", {"purpose": "flag"}),
        ("confirm", "Grajo1987 in F1 geaddet?", {"hint": "Adde in F1: Grajo1987 (PS: GFSL_Grajo87), damit du in die Lobby eingeladen werden kannst."}),
        ("number", "Welche Fahrernummer möchtest du?", {"purpose": "number"}),
    ]
    qs = [new_question(start_id + n, t, label, **extra) for n, (t, label, extra) in enumerate(specs)]
    return qs, start_id + len(qs)


def pages(questions: list[dict]) -> list[dict]:
    """Teilt die Fragen auf: bis zu 5 Fragen pro Formular, die Fahrernummer bekommt eine eigene Auswahl-Seite."""
    result: list[dict] = []
    for q in questions:
        if is_number_picker(q):
            result.append({"kind": "number", "questions": [q]})
        elif result and result[-1]["kind"] == "modal" and len(result[-1]["questions"]) < MODAL_PAGE_SIZE:
            result[-1]["questions"].append(q)
        else:
            result.append({"kind": "modal", "questions": [q]})
    return result


def parse_time(text: str) -> str | None:
    """'1.05.013', '1:05,013' oder '65.013' -> '1:05.013'"""
    m = re.fullmatch(r"(?:(\d{1,2})[:.,])?(\d{1,2})[.,:](\d{3})", text.strip())
    if not m:
        return None
    minutes = int(m.group(1)) if m.group(1) else 0
    sec = int(m.group(2))
    if m.group(1) and sec > 59:
        return None
    if not m.group(1) and sec > 59:
        minutes, sec = divmod(sec, 60)
    return f"{minutes}:{sec:02d}.{m.group(3)}"


def display(q: dict, value) -> str:
    if value is None:
        return "— (übersprungen)"
    t = q["type"]
    if t == "flag":
        return f"{flag_for(value)} {value}"
    if t == "yesno":
        return "Ja" if value else "Nein"
    if t == "confirm":
        return "✅ Bestätigt" if value else "❌ Nicht bestätigt"
    if t == "image":
        return f"📎 {len(value)} Screenshot(s)"
    if t == "number" and q["purpose"] == "number":
        return f"#{value}"
    return str(value)
