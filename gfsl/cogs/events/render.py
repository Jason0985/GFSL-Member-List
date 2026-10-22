"""Darstellung im Stil von Raid-Helper: Teams als Spalten mit Icons, Ersatzfahrer extra, Zähler unten."""
from datetime import datetime

import discord

from . import assign
from . import data as D
from . import timeutil as tu

COLOR = {"planned": 0x64748B, "posted": 0xE10600, "closed": 0x334155, "skipped": 0x334155, "missed": 0x334155}
NONE = "–"


def times(d: dict, ev: dict) -> dict:
    t = D.template_of(d, ev)
    remind = None if t["remind_days_before"] is None else tu.offset(ev["starts_at"], t["remind_days_before"], t["remind_time"])
    return {
        "start": tu.from_str(ev["starts_at"]),
        "post": tu.offset(ev["starts_at"], t["post_days_before"], t["post_time"]),
        "remind": remind,
        "deadline": tu.offset(ev["starts_at"], t["deadline_days_before"], t["deadline_time"]),
    }


def counts(ev: dict) -> dict:
    c = {"yes": 0, "maybe": 0, "no": 0}
    for s in ev["signups"].values():
        c[s["status"]] += 1
    return c


def name_of(guild: discord.Guild | None, uid: str, s: dict) -> str:
    member = guild.get_member(int(uid)) if guild else None
    return discord.utils.escape_markdown((member.display_name if member else s.get("name")) or uid)[:24]


SPACER = "\u200b"
HOVER_LINK = "https://discord.com"  # Ziel ist egal, es geht um den Tooltip beim Drüberfahren


def number(s: dict, hover: bool) -> str:
    """Anmelde-Nummer; beim Drüberfahren mit der Maus erscheint die genaue Anmeldezeit."""
    if not hover or not s.get("at"):
        return f"`{s['order']}`"
    when = datetime.fromtimestamp(s["at"], tu.TZ)
    verb = {"yes": "Angemeldet", "maybe": "Vielleicht", "no": "Abgemeldet"}[s["status"]]
    tip = f"{verb} {tu.WEEKDAYS[when.weekday()]}, {when.strftime('%d.%m.%Y %H:%M:%S')}"
    return f'[`{s["order"]}`]({HOVER_LINK} "{tip}")'


def entry(guild, uid: str, s: dict, emoji: str, hover: bool = True) -> str:
    late = " ⏰" if s.get("late") else ""
    return f"{emoji} {number(s, hover)} **{name_of(guild, uid, s)}**{late}"


def clip(lines: list[str], limit: int = 1024) -> str:
    text = "\n".join(lines) or NONE
    if len(text) <= limit:
        return text
    cut = text[:limit - 12].rsplit("\n", 1)[0]
    return cut + f"\n… (+{text.count(chr(10)) - cut.count(chr(10))})"


def spacer(embed: discord.Embed, name: str = SPACER):
    embed.add_field(name=name, value=SPACER, inline=False)


def event_embed(guild: discord.Guild | None, d: dict, ev: dict, hover: bool = True) -> discord.Embed:
    t = times(d, ev)
    c = counts(ev)
    title = D.field(d, ev, "title")
    desc = D.field(d, ev, "description")
    start_ts, deadline_ts = int(t["start"].timestamp()), int(t["deadline"].timestamp())
    deadline_passed = tu.now() >= t["deadline"]

    # Kopf: großer Titel, Beschreibung, Infoblock – getrennt nur durch Abstand
    # (Leerzeilen am Ende kürzt Discord weg, daher ein unsichtbares Zeichen als letzte Zeile)
    head = [f"# 🏁 {title}"]
    if desc:
        head += [desc]
    head += [
        "",
        f"📅 **<t:{start_ts}:D>**　🕗 **<t:{start_ts}:t> Uhr**　⏳ <t:{start_ts}:R>",
        f"📝 Anmeldefrist: **<t:{deadline_ts}:f>** "
        + ("· **abgelaufen – Q-Sperre**" if deadline_passed else f"(<t:{deadline_ts}:R>)"),
        SPACER,
    ]
    embed = discord.Embed(color=COLOR.get(ev["status"], 0xE10600), description="\n".join(head)[:4096])

    signups = sorted(ev["signups"].items(), key=lambda e: e[1]["order"])
    yes = [(uid, s) for uid, s in signups if s["status"] == "yes"]
    teams = [k for k in assign.classes(d) if k["kind"] == "team"]
    reserves = [k for k in assign.classes(d) if k["kind"] == "reserve"]
    team_by_id = {k["id"]: k for k in teams}

    # Stammfahrer: 2 Teams pro Reihe, Abstand zwischen den Reihen
    for n, team in enumerate(teams):
        lines = [entry(guild, uid, s, team["emoji"], hover) for uid, s in yes if s["class_id"] == team["id"]]
        subs = [(uid, s) for uid, s in yes if ev["assignments"].get(uid, {}).get("class_id") == team["id"]]
        lines += [f"🔁 {number(s, hover)} **{name_of(guild, uid, s)}**" for uid, s in subs]
        embed.add_field(name=f"{team['emoji']} {team['name']} ({len(lines)}/{team['seats']})"[:256], value=clip(lines), inline=True)
        if n % 2 == 1 or n == len(teams) - 1:
            spacer(embed)

    # Ersatzfahrer: eigener Abschnitt
    for res in reserves:
        lines = []
        for uid, s in yes:
            if s["class_id"] != res["id"]:
                continue
            target = team_by_id.get(ev["assignments"].get(uid, {}).get("class_id"))
            marker = f" → {target['emoji']} {target['name']}" if target else (" 🚫" if ev.get("deadline_done") else "")
            lines.append(entry(guild, uid, s, res["emoji"], hover) + marker)
        spacer(embed)  # Abstand zwischen Stammfahrern und Ersatzfahrern
        embed.add_field(name=f"{res['emoji']} {res['name']} ({len(lines)})"[:256], value=clip(lines), inline=False)

    for status, label in (("maybe", "❔ Vielleicht"), ("no", "❌ Abgemeldet")):
        people = [f"{number(s, hover)} **{name_of(guild, uid, s)}**" for uid, s in signups if s["status"] == status]
        if people:
            embed.add_field(name=f"{label} ({len(people)})", value=clip([", ".join(people)]), inline=False)

    if url := D.image_url(D.field(d, ev, "image")):
        spacer(embed)
        embed.set_image(url=url)
    # Zähler ganz unten, damit man sie auf einen Blick sieht
    footer = f"✅ {c['yes']} angemeldet  ·  ❔ {c['maybe']} vielleicht  ·  ❌ {c['no']} abgemeldet"
    if ev["status"] == "closed":
        footer = "🔒 Anmeldung geschlossen  ·  " + footer
    elif ev["status"] == "planned":
        footer = f"🗓️ Wird am {t['post'].strftime('%d.%m. um %H:%M')} gepostet · Event #{ev['id']}"
    embed.set_footer(text=footer)

    # Discord erlaubt 6000 Zeichen pro Embed: bei sehr vielen Anmeldungen ohne Hover-Zeiten
    if hover and len(embed) > 5900:
        return event_embed(guild, d, ev, hover=False)
    return embed


def info_embed(guild: discord.Guild | None, d: dict, ev: dict, kind: str) -> discord.Embed:
    """Info beim Posten („Anmeldung ist offen“) bzw. Anmelde-Erinnerung, mit Link zur Anmeldung."""
    t = times(d, ev)
    c = counts(ev)
    title = D.field(d, ev, "title")
    start_ts, deadline_ts = int(t["start"].timestamp()), int(t["deadline"].timestamp())
    link = (f"https://discord.com/channels/{guild.id}/{ev['channel_id']}/{ev['message_id']}"
            if guild and ev.get("message_id") else None)
    if kind == "open":
        head = f"## 📢 Die Anmeldung für {title} ist offen!"
        color = 0x22C55E
    else:
        head = f"## ⏰ Erinnerung: Anmeldung für {title}"
        color = 0xF59E0B
    lines = [head, "",
             f"🏁 Rennen: **<t:{start_ts}:F>**",
             f"📝 Anmeldefrist: **<t:{deadline_ts}:f>** (<t:{deadline_ts}:R>)"]
    if kind == "remind":
        lines.append(f"👥 Bisher **{c['yes']}** angemeldet – noch nicht dabei? Jetzt an- oder abmelden!")
    if link:
        lines += ["", f"➡️ **[Zur Anmeldung]({link})**"]
    return discord.Embed(color=color, description="\n".join(lines))


def assignment_embed(guild: discord.Guild | None, d: dict, ev: dict) -> discord.Embed:
    lines = []
    for team in [k for k in assign.classes(d) if k["kind"] == "team"]:
        subs = [uid for uid, a in ev["assignments"].items() if a["class_id"] == team["id"]]
        if subs:
            lines.append(f"{team['emoji']} **{team['name']}:** " + ", ".join(f"<@{u}>" for u in subs))
    if not lines:
        lines.append("Keine freien Cockpits – alle Stammfahrer sind angemeldet.")
    if wait := assign.waiting(d, ev):
        lines += ["", f"🚫 **Keine Einteilung – alle Plätze belegt ({len(wait)}):**"]
        lines += [f"`{ev['signups'][u]['order']}` <@{u}>" for u in wait]
        lines.append("-# Wird ein Platz frei, rückt der Nächste in Anmelde-Reihenfolge automatisch nach.")
    late = [uid for uid, s in sorted(ev["signups"].items(), key=lambda e: e[1]["order"]) if s["status"] == "yes" and s.get("late")]
    if late:
        lines += ["", "⏰ **Nach der Frist angemeldet (Q-Sperre):** " + ", ".join(f"<@{u}>" for u in late)]
    return discord.Embed(color=0xF59E0B, title=f"🔁 Einteilung Ersatzfahrer – {D.field(d, ev, 'title')}"[:256],
                         description="\n".join(lines)[:4096])
