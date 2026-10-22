"""Fahrernummern, Nicknames und die automatische Fahrerliste."""
import re

import discord

from gfsl import storage

from .constants import Colors
from .flags import lookup_flag


def taken_numbers(d: dict, except_user_id: str | None = None) -> set[int]:
    taken = set()
    for uid, drv in d["drivers"].items():
        if uid != except_user_id and isinstance(drv.get("number"), int):
            taken.add(drv["number"])
    return taken


def free_numbers(d: dict, except_user_id: str | None = None) -> list[int]:
    s = d["settings"]
    taken = taken_numbers(d, except_user_id)
    return [n for n in range(s["number_min"], s["number_max"] + 1) if n not in taken]


def next_free_number(d: dict, wanted: int | None, except_user_id: str | None = None) -> int | None:
    """Wunschnummer frei? -> nehmen. Sonst die nächste freie danach (am Ende wieder von vorne)."""
    lo, hi = d["settings"]["number_min"], d["settings"]["number_max"]
    taken = taken_numbers(d, except_user_id)
    start = wanted if isinstance(wanted, int) and lo <= wanted <= hi else lo
    size = hi - lo + 1
    for i in range(size):
        n = lo + (start - lo + i) % size
        if n not in taken:
            return n
    return None


def owner_of(d: dict, number: int, except_user_id: str | None = None) -> str | None:
    return next((uid for uid, drv in d["drivers"].items() if uid != except_user_id and drv.get("number") == number), None)


def render_nickname(template: str, drv: dict) -> str:
    """Platzhalter: {name} {nummer} {flagge} {plattform}"""
    values = {
        "name": drv.get("name") or "",
        "nummer": "" if drv.get("number") is None else str(drv["number"]),
        "flagge": lookup_flag(drv.get("flag")) or "",
        "plattform": drv.get("platform") or "",
    }
    text = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), ""), template)
    return re.sub(r"\s{2,}", " ", text).strip()[:32]


async def set_nickname(member: discord.Member, template: str, drv: dict) -> str | None:
    """Setzt den Nickname. Gibt eine Warnung zurück, falls es nicht geklappt hat."""
    nick = render_nickname(template, drv)
    if not nick:
        return None
    try:
        await member.edit(nick=nick, reason="GFSL Verify")
    except discord.HTTPException as e:
        return (f"Nickname nicht gesetzt ({e.text or e}). Die Bot-Rolle muss über der Rolle des Users stehen; "
                "den Server-Owner kann kein Bot umbenennen.")
    return None


def profile_value(d: dict, guild: discord.Guild | None, uid: str, key: str) -> str | None:
    """Plattform bzw. Flagge eines Fahrers. Sind der Frage Rollen zugeordnet (/verify fragen rollen), zählt die Rolle,
    die er gerade hat. Sonst der gespeicherte Wert (Formular, Import, /verify-profil, /verify fahrer setzen)."""
    q = next((q for q in d["questions"] if q["purpose"] == key and q.get("option_roles")), None)
    member = guild.get_member(int(uid)) if q and guild else None
    if member:
        for option, role_id in q["option_roles"].items():
            if member.get_role(role_id):
                return option
    return d["drivers"].get(uid, {}).get(key)


def driver_text(uid: str, flag: str | None, platform: str | None) -> str:
    """„🇩🇪 @User (PC)“"""
    parts = [lookup_flag(flag), f"<@{uid}>"]
    if platform:
        parts.append(f"({discord.utils.escape_markdown(platform)})")
    return " ".join(p for p in parts if p)


def driver_line(uid: str, drv: dict, d: dict | None = None, guild: discord.Guild | None = None) -> str:
    nr = drv["number"] if isinstance(drv.get("number"), int) else "–"
    if d is None:
        return f"{nr}: {driver_text(uid, drv.get('flag'), drv.get('platform'))}"
    return f"{nr}: {driver_text(uid, profile_value(d, guild, uid, 'flag'), profile_value(d, guild, uid, 'platform'))}"


def list_lines(d: dict, guild: discord.Guild | None = None) -> list[str]:
    """Alle Nummern untereinander: frei = leer, vergeben = Flagge @User (Plattform)."""
    s = d["settings"]
    by_number = {drv["number"]: uid for uid, drv in d["drivers"].items() if isinstance(drv.get("number"), int)}
    lines = []
    for n in range(s["number_min"], s["number_max"] + 1):
        if n in by_number:
            lines.append(driver_line(by_number[n], d["drivers"][by_number[n]], d, guild))
        else:
            lines.append(f"{n}:")
    # Fahrer ohne Nummer oder außerhalb des Bereichs nicht verschlucken
    rest = [(uid, drv) for uid, drv in d["drivers"].items()
            if not isinstance(drv.get("number"), int) or not s["number_min"] <= drv["number"] <= s["number_max"]]
    if rest:
        lines += ["", "**Ohne Nummer / außerhalb des Bereichs:**"] + [driver_line(uid, drv, d, guild) for uid, drv in rest]
    return lines


async def refresh_driver_list(guild: discord.Guild, d: dict) -> None:
    ch_id = d["settings"]["list_channel_id"]
    if not ch_id:
        return
    channel = guild.get_channel(ch_id)
    if channel is None:
        raise RuntimeError("Listen-Kanal nicht gefunden.")

    lines = list_lines(d, guild)

    # Aufteilen, falls die Liste für eine Nachricht zu lang wird
    chunks: list[str] = []
    cur = ""
    for line in lines:
        if len(cur) + len(line) + 1 > 3800:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur or not chunks:
        chunks.append(cur or "_Noch keine Fahrer verifiziert._")

    old_ids = d["settings"]["list_message_ids"]
    new_ids = []
    none = discord.AllowedMentions.none()
    for i, chunk in enumerate(chunks):
        embed = discord.Embed(color=Colors.list, description=chunk)
        if i == 0:
            embed.title = f"🏁 GFSL Fahrerliste ({len(d['drivers'])} Fahrer)"
        if i == len(chunks) - 1:
            embed.set_footer(text="Wird automatisch aktualisiert")
            embed.timestamp = discord.utils.utcnow()
        msg = None
        if i < len(old_ids):
            try:
                msg = await channel.fetch_message(old_ids[i])
                await msg.edit(embed=embed, allowed_mentions=none)
            except discord.NotFound:
                msg = None
        if msg is None:
            msg = await channel.send(embed=embed, allowed_mentions=none)
        new_ids.append(msg.id)
    for old in old_ids[len(chunks):]:
        try:
            await channel.get_partial_message(old).delete()
        except discord.HTTPException:
            pass
    d["settings"]["list_message_ids"] = new_ids
    storage.save()
