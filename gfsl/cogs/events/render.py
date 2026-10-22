"""Darstellung im Stil von Raid-Helper: Teams als Spalten mit Icons, Ersatzfahrer extra, Zähler unten."""
import discord

from . import assign
from . import data as D
from . import timeutil as tu

COLOR = {"planned": 0x64748B, "posted": 0xE10600, "closed": 0x334155, "skipped": 0x334155, "missed": 0x334155}
NONE = "–"


def times(d: dict, ev: dict) -> dict:
    t = D.template_of(d, ev)
    return {
        "start": tu.from_str(ev["starts_at"]),
        "post": tu.offset(ev["starts_at"], t["post_days_before"], t["post_time"]),
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


def entry(guild, uid: str, s: dict, emoji: str) -> str:
    late = " ⏰" if s.get("late") else ""
    return f"{emoji} `{s['order']}` **{name_of(guild, uid, s)}**{late}"


def clip(lines: list[str], limit: int = 1024) -> str:
    text = "\n".join(lines) or NONE
    if len(text) <= limit:
        return text
    cut = text[:limit - 12].rsplit("\n", 1)[0]
    return cut + f"\n… (+{text.count(chr(10)) - cut.count(chr(10))})"


def event_embed(guild: discord.Guild | None, d: dict, ev: dict) -> discord.Embed:
    t = times(d, ev)
    c = counts(ev)
    title = D.field(d, ev, "title")
    desc = D.field(d, ev, "description")
    start_ts, deadline_ts = int(t["start"].timestamp()), int(t["deadline"].timestamp())

    info = [
        f"👥 **{c['yes']}** angemeldet",
        f"📅 <t:{start_ts}:D>  🕗 <t:{start_ts}:t>  ⏳ <t:{start_ts}:R>",
        f"📝 Anmeldefrist: <t:{deadline_ts}:f>" + (" **(abgelaufen – Q-Sperre)**" if tu.now() >= t["deadline"] else f" (<t:{deadline_ts}:R>)"),
    ]
    embed = discord.Embed(color=COLOR.get(ev["status"], 0xE10600), title=f"🏁 {title}"[:256],
                          description=(f"{desc}\n\n" if desc else "") + "\n".join(info))

    signups = sorted(ev["signups"].items(), key=lambda e: e[1]["order"])
    yes = [(uid, s) for uid, s in signups if s["status"] == "yes"]
    teams = [k for k in assign.classes(d) if k["kind"] == "team"]
    reserves = [k for k in assign.classes(d) if k["kind"] == "reserve"]
    team_by_id = {k["id"]: k for k in teams}

    for team in teams:
        lines = [entry(guild, uid, s, team["emoji"]) for uid, s in yes if s["class_id"] == team["id"]]
        subs = [(uid, s) for uid, s in yes if ev["assignments"].get(uid, {}).get("class_id") == team["id"]]
        lines += [f"🔁 `{s['order']}` **{name_of(guild, uid, s)}**" for uid, s in subs]
        embed.add_field(name=f"{team['emoji']} {team['name']} ({len(lines)}/{team['seats']})"[:256], value=clip(lines), inline=True)

    for res in reserves:
        lines = []
        for uid, s in yes:
            if s["class_id"] != res["id"]:
                continue
            target = team_by_id.get(ev["assignments"].get(uid, {}).get("class_id"))
            lines.append(entry(guild, uid, s, res["emoji"]) + (f" → {target['emoji']}" if target else ""))
        embed.add_field(name=f"{res['emoji']} {res['name']} ({len(lines)})"[:256], value=clip(lines), inline=True)

    for status, label in (("maybe", "❔ Vielleicht"), ("no", "❌ Abgemeldet")):
        people = [f"`{s['order']}` **{name_of(guild, uid, s)}**" for uid, s in signups if s["status"] == status]
        if people:
            embed.add_field(name=f"{label} ({len(people)})", value=clip([", ".join(people)]), inline=False)

    if url := D.image_url(D.field(d, ev, "image")):
        embed.set_image(url=url)
    footer = f"✅ {c['yes']} angemeldet · ❔ {c['maybe']} vielleicht · ❌ {c['no']} abgemeldet"
    if ev["status"] == "closed":
        footer = "🔒 Anmeldung geschlossen · " + footer
    elif ev["status"] == "planned":
        footer = f"🗓️ Wird am {t['post'].strftime('%d.%m. um %H:%M')} gepostet · Event #{ev['id']}"
    embed.set_footer(text=footer)
    return embed


def assignment_embed(guild: discord.Guild | None, d: dict, ev: dict) -> discord.Embed:
    lines = []
    for team in [k for k in assign.classes(d) if k["kind"] == "team"]:
        subs = [uid for uid, a in ev["assignments"].items() if a["class_id"] == team["id"]]
        if subs:
            lines.append(f"{team['emoji']} **{team['name']}:** " + ", ".join(f"<@{u}>" for u in subs))
    if not lines:
        lines.append("Keine freien Cockpits – alle Stammfahrer sind angemeldet.")
    if wait := assign.waiting(d, ev):
        lines += ["", "⏳ **Ohne Cockpit (Warteliste):** " + ", ".join(f"<@{u}>" for u in wait)]
    late = [uid for uid, s in sorted(ev["signups"].items(), key=lambda e: e[1]["order"]) if s["status"] == "yes" and s.get("late")]
    if late:
        lines += ["", "⏰ **Nach der Frist angemeldet (Q-Sperre):** " + ", ".join(f"<@{u}>" for u in late)]
    return discord.Embed(color=0xF59E0B, title=f"🔁 Einteilung Ersatzfahrer – {D.field(d, ev, 'title')}"[:256],
                         description="\n".join(lines)[:4096])
