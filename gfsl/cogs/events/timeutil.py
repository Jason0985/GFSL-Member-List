"""Zeiten immer in deutscher Zeit (Sommer-/Winterzeit automatisch). Gespeichert als Text „2026-10-16 20:00“."""
import os
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("BOT_TIMEZONE", "Europe/Berlin"))
FMT = "%Y-%m-%d %H:%M"
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def now() -> datetime:
    return datetime.now(TZ)


def parse_hhmm(text: str) -> str:
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*(?:uhr)?\s*", text, re.I)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"Uhrzeit „{text}“ nicht verstanden, bitte z. B. 20:00")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def parse_date(text: str) -> date:
    """16.10.2026, 16.10.26, 16.10. (dieses bzw. nächstes Jahr) oder 2026-10-16"""
    text = text.strip()
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text):
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if m := re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})?", text):
        day, month = int(m.group(1)), int(m.group(2))
        if m.group(3):
            year = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
            return date(year, month, day)
        today = now().date()
        result = date(today.year, month, day)
        return result if result >= today else date(today.year + 1, month, day)
    raise ValueError(f"Datum „{text}“ nicht verstanden, bitte z. B. 16.10.2026")


def parse_datetime(text: str, default_time: str | None = None) -> str:
    """„16.10.2026 20:00“ oder nur „16.10.2026“ (dann default_time) -> gespeicherter Text."""
    parts = text.strip().split()
    if len(parts) == 2:
        d, t = parse_date(parts[0]), parse_hhmm(parts[1])
    elif len(parts) == 1 and default_time:
        d, t = parse_date(parts[0]), default_time
    else:
        raise ValueError(f"„{text}“ nicht verstanden, bitte z. B. 16.10.2026 20:00")
    return f"{d.isoformat()} {t}"


def parse_offset(text: str) -> tuple[int, str]:
    """„3 10:00“ = 3 Tage vorher um 10:00, „0 15:00“ = am selben Tag um 15:00."""
    m = re.fullmatch(r"\s*(\d{1,2})\s+(\S+)\s*", text)
    if not m:
        raise ValueError(f"„{text}“ nicht verstanden, bitte „Tage Uhrzeit“, z. B. 3 10:00")
    return int(m.group(1)), parse_hhmm(m.group(2))


def from_str(text: str) -> datetime:
    return datetime.strptime(text, FMT).replace(tzinfo=TZ)


def to_str(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime(FMT)


def ts(text_or_dt) -> int:
    dt = from_str(text_or_dt) if isinstance(text_or_dt, str) else text_or_dt
    return int(dt.timestamp())


def offset(start: str, days_before: int, hhmm: str) -> datetime:
    """Zeitpunkt „X Tage vor dem Event um HH:MM“ (z. B. Posten oder Anmeldefrist)."""
    day = from_str(start).date() - timedelta(days=days_before)
    h, m = map(int, hhmm.split(":"))
    return datetime.combine(day, time(h, m), tzinfo=TZ)


def shift(start: str, days: int) -> str:
    """Verschiebt um ganze Tage, die Uhrzeit bleibt (auch über die Zeitumstellung hinweg)."""
    dt = datetime.strptime(start, FMT) + timedelta(days=days)
    return dt.strftime(FMT)


def pretty(text: str) -> str:
    dt = from_str(text)
    return f"{WEEKDAYS[dt.weekday()]}, {dt.strftime('%d.%m.%Y %H:%M')}"
