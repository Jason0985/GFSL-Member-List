"""Gespeicherte Daten des Rennvorfall-Moduls pro Server."""
from gfsl import storage

DEFAULT_TITLE = "⚖️ Rennvorfall melden"
DEFAULT_TEXT = (
    "Ihr hattet einen Vorfall im Rennen? Klickt unten auf den Button. Es öffnet sich ein privater Kanal, den nur ihr, "
    "die Ligaleitung und die FIA-Stewards sehen.\n\n"
    "Dort füllt ihr in Ruhe das **Meldeformular** aus – gerne auch später: Beschuldigte, Vorfall, Beschreibung und "
    "euer Video (Upload oder YouTube-Link). Große Videos bitte immer als YouTube-Link."
)

DEFAULTS = {
    "settings": {
        "panel_channel_id": None,           # hier steht der „Vorfall melden“-Button
        "panel_message_channel_id": None,
        "panel_message_id": None,
        "panel_title": DEFAULT_TITLE,
        "panel_text": DEFAULT_TEXT,
        "panel_button": "Vorfall melden",
        "category_id": None,                # hier werden die Ticket-Kanäle angelegt
        "staff_role_ids": [],               # Ligaleitung + FIA-Stewards
        "result_channel_id": None,          # hier wird die Entscheidung veröffentlicht
        "archive_channel_id": None,         # Text- oder Forum-Kanal, je Vorfall ein Thread
    },
    "tickets": {},
    "next_ticket_id": 1,
}

PHASES = {
    "reporter": "✍️ Einsender schreibt",
    "statements": "🗣️ Stellungnahmen",
    "review": "⚖️ Bewertung durch die Stewards",
    "closed": "✅ Abgeschlossen",
    "cancelled": "✖️ Abgebrochen",
}


def get(guild_id: int) -> dict:
    return storage.section(guild_id, "incidents", DEFAULTS)


def new_ticket(d: dict, reporter_id: int, now: int) -> dict:
    tid = str(d["next_ticket_id"])
    d["next_ticket_id"] += 1
    t = {
        "id": tid, "channel_id": None, "status_message_id": None, "phase": "reporter",
        "reporter_id": str(reporter_id), "accused": [], "extra": [],
        "entries": {},         # user_id -> {message_id, at, fields, links}: Meldung bzw. Stellungnahme aus dem Formular
        "submitted": {},       # user_id -> Zeitpunkt, an dem er abgeschickt hat
        "skipped": [],         # ohne Stellungnahme weiter (Button der Stewards)
        "opened_at": now, "closed_at": None, "closed_by": None,
        "decision": None, "penalty": None, "log": [],
    }
    d["tickets"][tid] = t
    return t


def number(t: dict) -> str:
    return f"#{int(t['id']):04d}"
