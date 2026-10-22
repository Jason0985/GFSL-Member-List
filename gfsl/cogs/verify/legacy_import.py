"""Altdaten übernehmen: aus den Nachrichten des alten Melpo-Verify-Bots, aus Nicknames oder aus einer CSV-Datei.

Erkannt werden zwei Arten von Nachrichten:
  * Formular  – Embed mit Feldern wie „Ingame-Name“, „Fahrernummer“, „Flagge“, „Plattform“ (eine Nachricht = ein Fahrer)
  * Liste     – Zeilen wie „#17 🇩🇪 Wenig-Luca @User“ (eine Zeile = ein Fahrer)
Spätere Nachrichten überschreiben frühere, so landet der aktuelle Stand in der Datenbank.
Vor dem Übernehmen gibt es immer eine Vorschau mit CSV-Datei, die man in Excel prüfen und korrigiert per
/verify fahrer import_csv wieder hochladen kann.
"""
import csv
import io
import re
import time
from dataclasses import dataclass, field

import discord

from gfsl import storage

from . import data, drivers
from .constants import Colors
from .flags import find_flag, lookup_flag

# Reihenfolge ist wichtig: „Fahrernummer“ ist eine Nummer, kein Name
KEYWORDS = [
    ("platform", ("plattform", "platform", "konsole")),
    ("number", ("fahrernummer", "startnummer", "wunschnummer", "nummer", "number")),
    ("flag", ("flagge", "nation", "land", "flag", "country", "herkunft")),
    ("name", ("ingame", "psn", "ea-name", "ea name", "ea-id", "ea id", "gamertag", "spielername")),
    ("name_weak", ("name",)),
]
REJECT_WORDS = ("abgelehnt", "denied", "declined", "rejected", "verweigert")
MENTION_RE = re.compile(r"<@!?(\d{17,20})>")
ID_RE = re.compile(r"(?<!\d)(\d{17,20})(?!\d)")
# Nummer wie „17“, „#17“ oder „17:“ – aber keine Rundenzeit wie 1:05.013
NUMBER_RE = re.compile(r"(?<![\d:.,])#?(\d{1,3})(?![\d.,]|:\d)")
CSV_COLUMNS = ["user_id", "nummer", "name", "flagge", "plattform", "hinweis", "quelle"]


@dataclass
class Record:
    user_id: str
    number: int | None = None
    name: str | None = None
    flag: str | None = None
    platform: str | None = None
    source: str = ""
    notes: list[str] = field(default_factory=list)

    def merge(self, other: "Record"):
        for attr in ("number", "name", "flag", "platform"):
            if getattr(other, attr) is not None:
                setattr(self, attr, getattr(other, attr))
        self.source = other.source or self.source


def clean(text: str | None) -> str | None:
    """Entfernt Markdown (fett, Code, Zitat), lässt aber Unterstriche in Namen wie GFSL_Max stehen."""
    text = (text or "").replace("\\_", "_")
    text = re.sub(r"\*\*|~~|`|^>\s*", "", text.strip())
    text = text.strip().strip("\"'„“").strip()
    return text or None


def classify(key: str) -> str | None:
    key = key.lower()
    for name, words in KEYWORDS:
        if any(w in key for w in words):
            return name
    return None


def record_from_values(user_id: str, values: dict[str, str], source: str) -> Record:
    r = Record(user_id=user_id, source=source)
    if v := values.get("number"):
        m = NUMBER_RE.search(v)
        r.number = int(m.group(1)) if m else None
    if v := values.get("flag"):
        r.flag = find_flag(v) or clean(v)
    if v := values.get("name") or values.get("name_weak"):
        r.name = (clean(MENTION_RE.sub("", v)) or "")[:32] or None
    if v := values.get("platform"):
        r.platform = clean(v)
    return r


def find_user_id(texts: list[str], bot_id: int) -> str | None:
    for regex in (MENTION_RE, ID_RE):
        for text in texts:
            for uid in regex.findall(text or ""):
                if int(uid) != bot_id:
                    return uid
    return None


def parse_form(message: discord.Message, guild: discord.Guild) -> Record | None:
    """Eine Nachricht mit Frage/Antwort-Paaren (Embed-Felder oder „Frage: Antwort“-Zeilen)."""
    for embed in message.embeds:
        values: dict[str, str] = {}
        for f in embed.fields:
            if (k := classify(f.name)) and k not in values:
                values[k] = f.value
        lines = (embed.description or "").splitlines()
        for n, line in enumerate(lines):
            if ":" in line:
                key, _, value = line.partition(":")
                if (k := classify(key)) and k not in values and value.strip():
                    values[k] = value
            elif line.strip().startswith("**") and n + 1 < len(lines):
                if (k := classify(line)) and k not in values:
                    values[k] = lines[n + 1]
        if not values or not ({"number", "name", "flag"} & set(values)):
            continue

        texts = [message.content, embed.description, embed.title, embed.footer.text if embed.footer else None,
                 embed.author.name if embed.author else None, *(f.value for f in embed.fields), *(f.name for f in embed.fields)]
        uid = find_user_id(texts, guild.me.id)
        if uid is None and embed.author and embed.author.name:
            member = guild.get_member_named(embed.author.name)
            uid = str(member.id) if member else None
        if uid is None:
            continue
        return record_from_values(uid, values, message.jump_url)
    return None


def parse_line(user_id: str, text: str, source: str) -> Record | None:
    """Eine Zeile wie „#17 🇩🇪 Wenig-Luca“ (der User wurde vorher herausgelöst)."""
    rest = MENTION_RE.sub(" ", text)
    rest = ID_RE.sub(" ", rest)
    m = NUMBER_RE.search(rest)
    if not m:
        return None
    flag = find_flag(rest)
    rest = rest[:m.start()] + " " + rest[m.end():]
    if flag and (emoji := lookup_flag(flag)):
        rest = rest.replace(emoji, " ")
    name = clean(re.sub(r"^[\s|·•:\-–—]+|[\s|·•:\-–—]+$", "", re.sub(r"\s{2,}", " ", rest)))
    return Record(user_id=user_id, number=int(m.group(1)), flag=flag, name=name[:32] if name else None, source=source)


def parse_list(message: discord.Message) -> list[Record]:
    texts = [message.content] + [e.description or "" for e in message.embeds] + [f.value for e in message.embeds for f in e.fields]
    records = []
    for text in texts:
        for line in text.splitlines():
            m = MENTION_RE.search(line)
            if m and (r := parse_line(m.group(1), line, message.jump_url)):
                records.append(r)
    return records


async def read_channel(channel: discord.TextChannel, limit: int, only_bots: bool) -> tuple[list[Record], int]:
    """Liest den Kanal vom ältesten zum neuesten Eintrag. Gibt die Fahrer (zusammengeführt) und die Anzahl Nachrichten zurück.
    Zeilen ohne User (z. B. „10: Pierre Gasly“ oder „17: //“) gelten als freie Nummern und werden ignoriert."""
    merged: dict[str, Record] = {}
    count = 0
    async for message in channel.history(limit=limit, oldest_first=True):
        count += 1
        if only_bots and not message.author.bot:
            continue
        all_text = " ".join([message.content] + [f"{e.title} {e.description} {e.footer.text if e.footer else ''}" for e in message.embeds]).lower()
        if any(w in all_text for w in REJECT_WORDS):
            continue
        found = [r] if (r := parse_form(message, channel.guild)) else parse_list(message)
        for r in found:
            if prev := merged.pop(r.user_id, None):  # neu einsortieren -> Reihenfolge = zuletzt gesehen
                prev.merge(r)
                r = prev
            merged[r.user_id] = r
    return list(merged.values()), count


def read_nicknames(role: discord.Role) -> list[Record]:
    records = []
    for member in role.members:
        r = parse_line(str(member.id), member.display_name, "Nickname")
        if r:
            records.append(r)
    return records


def read_csv(content: bytes) -> list[Record]:
    text = content.decode("utf-8-sig")
    dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=";,\t")
    records = []
    for row in csv.DictReader(io.StringIO(text), dialect=dialect):
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        uid = row.get("user_id", "")
        if not re.fullmatch(r"\d{17,20}", uid):
            continue
        nr = row.get("nummer", "").lstrip("#")
        records.append(Record(
            user_id=uid,
            number=int(nr) if nr.isdigit() else None,
            name=row.get("name") or None,
            flag=(find_flag(row["flagge"]) or row["flagge"]) if row.get("flagge") else None,
            platform=row.get("plattform") or None,
            source="CSV",
        ))
    return records


def to_csv(records: list[Record], guild: discord.Guild) -> discord.File:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(CSV_COLUMNS)
    for r in records:
        member = guild.get_member(int(r.user_id))
        notes = r.notes + ([] if member else ["nicht auf dem Server"])
        writer.writerow([r.user_id, "" if r.number is None else r.number, r.name or "", r.flag or "", r.platform or "",
                         " / ".join(notes), r.source])
    # utf-8-sig, damit Excel die Umlaute richtig anzeigt
    return discord.File(io.BytesIO(buf.getvalue().encode("utf-8-sig")), filename="fahrer_import.csv")


def check(records: list[Record], guild: discord.Guild, d: dict, only_members: bool) -> tuple[list[Record], int]:
    """Prüft Nummern und füllt fehlende Namen. Gibt die verwendbaren Fahrer und die Zahl der Nicht-Mitglieder zurück."""
    s = d["settings"]
    kept = []
    gone = 0
    for r in records:
        member = guild.get_member(int(r.user_id))
        if member is None:
            gone += 1
            if only_members:
                continue
        if not r.name and member:
            r.name = member.display_name[:32]
        kept.append(r)

    # Doppelte Nummern: der zuletzt gesehene Eintrag behält sie
    owner: dict[int, Record] = {}
    for r in kept:
        if r.number is None:
            continue
        if prev := owner.get(r.number):
            prev.notes.append(f"#{r.number} doppelt, jetzt bei {r.name or r.user_id}")
            prev.number = None
        owner[r.number] = r
    for r in kept:
        if r.number is None:
            continue
        if not s["number_min"] <= r.number <= s["number_max"]:
            r.notes.append(f"#{r.number} liegt außerhalb von {s['number_min']}–{s['number_max']}")
    return kept, gone


def apply(records: list[Record], d: dict, only_new: bool) -> tuple[int, list[str]]:
    imported_ids = {r.user_id for r in records}
    added = 0
    problems = []
    for r in records:
        if only_new and r.user_id in d["drivers"]:
            continue
        number = r.number
        if number is not None and (other := drivers.owner_of(d, number, r.user_id)) and other not in imported_ids:
            problems.append(f"#{number} gehört schon <@{other}>, <@{r.user_id}> wurde ohne Nummer eingetragen.")
            number = None
        old = d["drivers"].get(r.user_id, {})
        d["drivers"][r.user_id] = {
            "number": number,
            "name": r.name or old.get("name"),
            "flag": r.flag or old.get("flag"),
            "platform": r.platform or old.get("platform"),
            "verified_at": old.get("verified_at") or int(time.time()),
            "verified_by": old.get("verified_by") or "import",
        }
        added += 1
    storage.save()
    return added, problems


def preview_embed(records: list[Record], guild: discord.Guild, d: dict, read_info: str, gone: int, only_members: bool) -> discord.Embed:
    with_nr = sum(r.number is not None for r in records)
    with_notes = [r for r in records if r.notes]
    existing = sum(r.user_id in d["drivers"] for r in records)
    lines = [
        read_info,
        f"**Erkannte Fahrer:** {len(records)} (davon {with_nr} mit Nummer)",
        f"**Schon eingetragen:** {existing}",
        f"**Nicht mehr auf dem Server:** {gone}" + (" (ausgelassen)" if only_members else ""),
        f"**Mit Hinweis:** {len(with_notes)}",
        "",
        "**Beispiele:**",
    ]
    for r in records[:15]:
        lines.append(drivers.driver_line(r.user_id, {"number": r.number, "name": r.name, "flag": r.flag, "platform": r.platform})
                     + (f" ⚠️ {'; '.join(r.notes)}" if r.notes else ""))
    if len(records) > 15:
        lines.append(f"… und {len(records) - 15} weitere (siehe CSV)")
    lines += ["", "Bitte die CSV prüfen. Passt etwas nicht, korrigieren und mit `/verify fahrer import_csv` hochladen. "
                  "Rollen und Nicknames werden beim Import **nicht** verändert."]
    return discord.Embed(color=Colors.info, title="📥 Import-Vorschau", description="\n".join(lines)[:4096])


class ImportView(discord.ui.View):
    def __init__(self, user_id: int, records: list[Record]):
        super().__init__(timeout=900)
        self.user_id = user_id
        self.records = records

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def finish(self, interaction: discord.Interaction, only_new: bool):
        self.stop()
        await interaction.response.defer()
        d = data.get(interaction.guild_id)
        added, problems = apply(self.records, d, only_new)
        msg = f"✅ {added} Fahrer übernommen."
        try:
            await drivers.refresh_driver_list(interaction.guild, d)
            msg += " Fahrerliste aktualisiert."
        except (discord.HTTPException, RuntimeError) as e:
            msg += f"\n⚠️ Fahrerliste: {e}"
        if problems:
            msg += "\n" + "\n".join(f"⚠️ {p}" for p in problems[:20])
        await interaction.edit_original_response(content=msg[:2000], view=None)

    @discord.ui.button(label="Alle übernehmen", style=discord.ButtonStyle.success)
    async def all_(self, interaction: discord.Interaction, _):
        await self.finish(interaction, only_new=False)

    @discord.ui.button(label="Nur neue übernehmen", style=discord.ButtonStyle.primary, emoji="➕")
    async def new(self, interaction: discord.Interaction, _):
        await self.finish(interaction, only_new=True)

    @discord.ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, _):
        self.stop()
        await interaction.response.edit_message(content="Import abgebrochen, nichts geändert.", view=None)
