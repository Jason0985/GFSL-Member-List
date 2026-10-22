"""Gespeicherte Daten des Verify-Moduls pro Server."""
from gfsl import storage

DEFAULT_PANEL_TITLE = "🏁 GFSL Verifizierung"
DEFAULT_PANEL_TEXT = (
    "Klick auf den Button, um dich zu verifizieren. Es öffnet sich ein Formular, "
    "das nur du siehst. Nach dem Abschicken prüfen die Mods deine Angaben."
)

DEFAULTS = {
    "settings": {
        "panel_channel_id": None,   # hier steht der Start-Button
        "panel_message_channel_id": None,  # wo das Panel zuletzt gepostet wurde
        "panel_message_id": None,
        "panel_title": DEFAULT_PANEL_TITLE,
        "panel_text": DEFAULT_PANEL_TEXT,
        "panel_button": "Verifizierung starten",
        "mod_channel_id": None,     # hier landen die Anträge mit Akzeptieren/Ablehnen
        "list_channel_id": None,    # hier steht die Fahrerliste
        "list_message_ids": [],
        "verified_role_id": None,   # Rolle bei Freigabe
        "remove_role_id": None,     # Rolle, die bei Freigabe entfernt wird (z. B. "Unverifiziert")
        "mod_role_id": None,        # darf Anträge bearbeiten (zusätzlich zu "Server verwalten")
        "nickname_template": "{name}",
        "number_min": 1,
        "number_max": 99,
        "reserved_numbers": [],
    },
    "questions": [],
    "next_question_id": 1,
    "drivers": {},        # user_id -> {number, name, flag, platform, verified_at, verified_by}
    "applications": {},   # app_id -> Antrag
    "next_app_id": 1,
}


def get(guild_id: int) -> dict:
    return storage.section(guild_id, "verify", DEFAULTS)
