"""Anwesenheit: Wie oft war jeder Fahrer bei den vergangenen Events angemeldet?"""
import discord

from . import assign
from . import data as D


def stats(guild: discord.Guild, d: dict, template_id: str | None = None) -> tuple[list[dict], int]:
    """Alle aktuellen Fahrer (Mitglieder mit einer Anmelde-Rolle) mit ihren Zahlen über alle abgeschlossenen Events."""
    events = [e for e in D.sorted_events(d, template_id) if e["status"] == "closed"]
    members = {m.id: m for c in assign.classes(d) if (role := guild.get_role(c["role_id"])) for m in role.members if not m.bot}
    rows = []
    for mid, member in members.items():
        uid = str(mid)
        row = {"member": member, "yes": 0, "maybe": 0, "no": 0, "none": 0, "late": 0}
        for ev in events:
            s = ev["signups"].get(uid)
            if s is None:
                row["none"] += 1
            else:
                row[s["status"]] += 1
                row["late"] += bool(s["status"] == "yes" and s.get("late"))
        row["pct"] = round(100 * row["yes"] / len(events)) if events else 0
        rows.append(row)
    rows.sort(key=lambda r: (-r["pct"], -r["yes"], r["member"].display_name.lower()))
    return rows, len(events)


def line(r: dict) -> str:
    name = discord.utils.escape_markdown(r["member"].display_name)[:28]
    extra = f" · ⏰{r['late']}" if r["late"] else ""
    return f"`{r['pct']:>3}%` **{name}** · ✅{r['yes']} ❔{r['maybe']} ❌{r['no']} ➖{r['none']}{extra}"


def embeds(rows: list[dict], total: int, title: str) -> list[discord.Embed]:
    active = [line(r) for r in rows if r["yes"] > 0]
    never = [discord.utils.escape_markdown(r["member"].display_name) for r in rows if r["yes"] == 0]
    blocks = active or ["Noch keine Anmeldungen."]
    chunks, cur = [], ""
    for b in blocks:
        if len(cur) + len(b) > 3900:
            chunks.append(cur)
            cur = ""
        cur += b + "\n"
    chunks.append(cur)
    result = [discord.Embed(color=0xE10600, description=c) for c in chunks[:9]]
    result[0].title = f"📊 {title} – {total} Event(s)"
    if never:
        result[-1].add_field(name=f"🚫 Noch nie angemeldet ({len(never)})", value=", ".join(never)[:1024], inline=False)
    result[-1].set_footer(text="✅ angemeldet · ❔ vielleicht · ❌ abgemeldet · ➖ keine Rückmeldung · ⏰ nach der Frist")
    return result
