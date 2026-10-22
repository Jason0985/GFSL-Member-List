TYPES = {
    "text":      "Freitext",
    "paragraph": "Freitext (lang)",
    "number":    "Zahl",
    "time":      "Rundenzeit",
    "select":    "Auswahl",
    "flag":      "Flagge",
    "yesno":     "Ja/Nein",
    "confirm":   "Haken / Bestätigen",
    "image":     "Screenshot",
}

# "Feld" = was der Bot mit der Antwort automatisch macht
PURPOSES = {
    "none": "—",
    "name": "Ingame-Name → Nickname",
    "number": "Fahrernummer",
    "flag": "Flagge in der Fahrerliste",
    "platform": "Plattform",
}

# Welche Fragetypen zu welchem Feld passen
PURPOSE_TYPES = {
    "name": ["text"],
    "number": ["number"],
    "flag": ["flag", "select"],
    "platform": ["select", "text"],
}

MAX_QUESTIONS = 15
MODAL_PAGE_SIZE = 5     # Discord: max. 5 Felder pro Formular
LABEL_MAX = 45          # Discord: Überschrift eines Formularfelds
HINT_MAX = 100          # Discord: Beschreibung eines Formularfelds


class Colors:
    info = 0x3B82F6
    ok = 0x22C55E
    bad = 0xEF4444
    wait = 0xF59E0B
    list = 0xE10600
