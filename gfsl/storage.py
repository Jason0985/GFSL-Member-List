"""Einfache JSON-Datenbank (data/db.json). Reicht für eine Liga locker aus.

Aufbau:  {"guilds": {"<server-id>": {"<modul>": {...}}}}
Jedes Modul (verify, ersatzfahrer, ...) hat seinen eigenen Bereich pro Server.
"""
import copy
import json
import os
from pathlib import Path

# ponytail: JSON-Datei, geht bei Hostern ohne festen Speicher beim Redeploy verloren -> DATA_FILE auf ein Volume legen
DATA_FILE = Path(os.environ.get("DATA_FILE", "data/db.json"))

_db: dict = {"guilds": {}}


def load() -> None:
    global _db
    if DATA_FILE.exists():
        _db = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    _db.setdefault("guilds", {})


def save() -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_db, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(DATA_FILE)


def section(guild_id: int, module: str, defaults: dict) -> dict:
    """Bereich eines Moduls für einen Server. Fehlende Schlüssel werden mit den Standardwerten ergänzt."""
    g = _db["guilds"].setdefault(str(guild_id), {})
    data = g.setdefault(module, {})
    for key, value in defaults.items():
        if key not in data:
            data[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(data[key], dict):  # z. B. neue Einstellungen ergänzen
            for k, v in value.items():
                data[key].setdefault(k, copy.deepcopy(v))
    return data
