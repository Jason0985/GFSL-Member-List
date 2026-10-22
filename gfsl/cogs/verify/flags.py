"""Flaggen-Liste. Die ersten 25 sind die Standard-Auswahl (Discord erlaubt max. 25 Optionen pro Menü)."""
import re

FLAGS = {
    "Deutschland": "🇩🇪", "Österreich": "🇦🇹", "Schweiz": "🇨🇭", "Niederlande": "🇳🇱", "Belgien": "🇧🇪",
    "Luxemburg": "🇱🇺", "Frankreich": "🇫🇷", "Italien": "🇮🇹", "Spanien": "🇪🇸", "Portugal": "🇵🇹",
    "Großbritannien": "🇬🇧", "Irland": "🇮🇪", "Dänemark": "🇩🇰", "Schweden": "🇸🇪", "Norwegen": "🇳🇴",
    "Finnland": "🇫🇮", "Polen": "🇵🇱", "Tschechien": "🇨🇿", "Ungarn": "🇭🇺", "Kroatien": "🇭🇷",
    "Türkei": "🇹🇷", "Griechenland": "🇬🇷", "USA": "🇺🇸", "Brasilien": "🇧🇷", "Japan": "🇯🇵",
    # weitere, falls du eigene Optionen per /verify fragen angibst
    "Monaco": "🇲🇨", "Liechtenstein": "🇱🇮", "Slowakei": "🇸🇰", "Slowenien": "🇸🇮", "Serbien": "🇷🇸",
    "Bosnien": "🇧🇦", "Rumänien": "🇷🇴", "Bulgarien": "🇧🇬", "Ukraine": "🇺🇦", "Russland": "🇷🇺",
    "Litauen": "🇱🇹", "Lettland": "🇱🇻", "Estland": "🇪🇪", "Albanien": "🇦🇱", "Kosovo": "🇽🇰",
    "Nordmazedonien": "🇲🇰", "Kanada": "🇨🇦", "Mexiko": "🇲🇽",
    "Argentinien": "🇦🇷", "Australien": "🇦🇺", "Neuseeland": "🇳🇿", "China": "🇨🇳", "Thailand": "🇹🇭",
    "Marokko": "🇲🇦", "Tunesien": "🇹🇳", "Kasachstan": "🇰🇿", "Iran": "🇮🇷", "Syrien": "🇸🇾",
    "England": "🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",
    "Schottland": "🏴\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f",
    "Wales": "🏴\U000e0067\U000e0062\U000e0077\U000e006c\U000e0073\U000e007f",
}

DEFAULT_FLAG_OPTIONS = list(FLAGS)[:25]

# Länderflagge (zwei Regional-Indicator-Buchstaben) oder Flagge mit Tag-Zeichen (England, Schottland, Wales)
FLAG_EMOJI_RE = re.compile("[\U0001F1E6-\U0001F1FF]{2}|🏴[\U000E0061-\U000E007A]+\U000E007F")

_BY_EMOJI = {v: k for k, v in FLAGS.items()}


def lookup_flag(name: str | None) -> str | None:
    """Emoji zu einem Ländernamen (oder das Emoji selbst, falls schon eins gespeichert ist)."""
    if not name:
        return None
    name = str(name).strip()
    if FLAG_EMOJI_RE.fullmatch(name):
        return name
    if name in FLAGS:
        return FLAGS[name]
    lower = name.lower()
    return next((v for k, v in FLAGS.items() if k.lower() == lower), None)


def flag_for(name: str | None) -> str:
    return lookup_flag(name) or "🏳️"


def find_flag(text: str) -> str | None:
    """Sucht in einem beliebigen Text nach einer Flagge (Emoji oder Ländername) und gibt den Speicherwert zurück."""
    m = FLAG_EMOJI_RE.search(text or "")
    if m:
        return _BY_EMOJI.get(m.group(0), m.group(0))
    lower = (text or "").lower()
    return next((k for k in FLAGS if re.search(rf"\b{re.escape(k.lower())}\b", lower)), None)
