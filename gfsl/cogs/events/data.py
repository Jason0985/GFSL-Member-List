"""Gespeicherte Daten des Anmelde-Moduls pro Server.

Klasse   = eine Rolle, mit der man sich anmelden kann (Team mit Plätzen oder Ersatzfahrer), mit Icon
Vorlage  = alles, was bei jedem Event gleich ist (Kanal, Beschreibung, Bild, Uhrzeiten, Freigabe ...)
Event    = ein Eintrag im Kalender (Titel, Datum, optional eigenes Bild/Beschreibung) inkl. Anmeldungen
"""
from pathlib import Path

import discord

from gfsl import storage

DEFAULTS = {
    "settings": {
        "classes": [],              # [{id, name, role_id, emoji, kind: "team"|"reserve", seats}]
        "next_class_id": 1,
        "reminder_user_id": None,   # bekommt die Freigabe-DMs, falls bei der Vorlage niemand eingetragen ist
    },
    "templates": {},
    "next_template_id": 1,
    "events": {},
    "next_event_id": 1,
}

TEMPLATE_DEFAULTS = {
    "name": "",
    "title": "Rennen",
    "description": "",
    "image": None,                  # {"file": "..."} (vom Bot gespeichert) oder {"url": "..."}
    "channel_id": None,
    "ping_role_ids": [],
    "event_time": "20:00",
    "post_days_before": 3, "post_time": "10:00",          # wann die Anmeldung gepostet wird
    "deadline_days_before": 0, "deadline_time": "15:00",  # Anmeldefrist (danach Q-Sperre + Einteilung)
    "review": True,                 # vor dem Posten Freigabe per DM nötig
    "review_hours": 24,             # so viele Stunden vor dem Posten kommt die DM
    "nudge_hours": 0,               # so viele Stunden vor der Frist: Fahrer ohne Rückmeldung anpingen (0 = aus)
    "reminder_user_id": None,
}

STATUS_LABEL = {
    "planned": "🗓️ geplant", "posted": "📢 offen", "closed": "🔒 vorbei",
    "skipped": "⏭️ ausgesetzt", "missed": "⚠️ nicht gepostet",
}

IMAGE_DIR = storage.DATA_FILE.parent / "event_images"


def get(guild_id: int) -> dict:
    return storage.section(guild_id, "events", DEFAULTS)


def template_of(d: dict, ev: dict) -> dict:
    t = d["templates"].get(str(ev["template_id"]), {})
    return {**TEMPLATE_DEFAULTS, **t}


def field(d: dict, ev: dict, key: str):
    """Titel/Beschreibung/Bild: eigener Wert des Events, sonst der aus der Vorlage."""
    value = ev.get(key)
    return value if value not in (None, "") else template_of(d, ev)[key]


def new_template(d: dict, name: str, user_id: int) -> dict:
    tid = str(d["next_template_id"])
    d["next_template_id"] += 1
    t = {**TEMPLATE_DEFAULTS, "id": tid, "name": name, "reminder_user_id": user_id, "ping_role_ids": []}
    d["templates"][tid] = t
    return t


def new_event(d: dict, template: dict, starts_at: str, title: str | None, user_id: int) -> dict:
    eid = str(d["next_event_id"])
    d["next_event_id"] += 1
    ev = {
        "id": eid, "template_id": template["id"], "title": title, "description": None, "image": None,
        "starts_at": starts_at, "status": "planned", "confirmed": not template["review"],
        "review_sent": False, "overdue_sent": False, "nudge_done": False, "deadline_done": False,
        "channel_id": None, "message_id": None, "assignment_message_id": None,
        "signups": {},        # user_id -> {status: yes|maybe|no, class_id, name, at, order, late}
        "assignments": {},    # user_id (Ersatzfahrer) -> {class_id, at}
        "next_order": 1, "created_by": str(user_id),
    }
    d["events"][eid] = ev
    return ev


def sorted_events(d: dict, template_id: str | None = None) -> list[dict]:
    evs = [e for e in d["events"].values() if template_id is None or e["template_id"] == template_id]
    return sorted(evs, key=lambda e: e["starts_at"])


async def save_image(attachment: discord.Attachment, prefix: str) -> dict:
    """Discord-Links laufen nach einiger Zeit ab, deshalb speichert der Bot Bilder selbst."""
    if not (attachment.content_type or "").startswith("image/"):
        raise ValueError("Bitte ein Bild hochladen (PNG, JPG, GIF).")
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(attachment.filename).suffix.lower() or ".png"
    name = f"{prefix}_{attachment.id}{suffix}"
    await attachment.save(IMAGE_DIR / name)
    return {"file": name}


def image_file(image: dict | None) -> discord.File | None:
    if image and image.get("file") and (IMAGE_DIR / image["file"]).exists():
        return discord.File(IMAGE_DIR / image["file"], filename=image["file"])
    return None


def image_url(image: dict | None) -> str | None:
    if not image:
        return None
    if image.get("file"):
        return f"attachment://{image['file']}"
    return image.get("url")
