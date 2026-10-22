"""Klassen (Teams/Ersatzfahrer) und die automatische Einteilung der Ersatzfahrer in freie Cockpits.

Ab der Anmeldefrist: freie Plätze eines Teams = Plätze - rechtzeitig angemeldete Stammfahrer des Teams.
Wer einen freien Platz bekommt, entscheidet die Anmelde-Reihenfolge (wer zuerst angemeldet ist, fährt).
Stammfahrer, die sich zu spät anmelden, verlieren ihren festen Platz: Sie reihen sich wie Ersatzfahrer
hinten ein (bevorzugt ins eigene Team, falls dort noch frei) und verdrängen niemanden.
In welches Team ein Ersatzfahrer kommt, ist zufällig. Wer keinen Platz bekommt, landet unter „Keine Einteilung“.
Spätere Änderungen werden nachgetragen, ohne bestehende Einteilungen unnötig umzuwerfen.
"""
import random
import time

import discord


def classes(d: dict) -> list[dict]:
    return d["settings"]["classes"]


def class_by_id(d: dict, class_id) -> dict | None:
    return next((c for c in classes(d) if c["id"] == class_id), None)


def class_for(member: discord.Member, d: dict) -> dict | None:
    """Erste Klasse (in der eingestellten Reihenfolge), deren Rolle der Fahrer hat. Teams vor Ersatzfahrern."""
    ordered = [c for c in classes(d) if c["kind"] == "team"] + [c for c in classes(d) if c["kind"] == "reserve"]
    return next((c for c in ordered if member.get_role(c["role_id"])), None)


def candidates(d: dict, ev: dict) -> list[str]:
    """Alle, die auf einen freien Platz warten: Ersatzfahrer und zu spät angemeldete Stammfahrer, nach Anmelde-Reihenfolge."""
    kinds = {c["id"]: c["kind"] for c in classes(d)}
    yes = {uid: s for uid, s in ev["signups"].items() if s["status"] == "yes" and s["class_id"] in kinds}
    return sorted((uid for uid, s in yes.items() if kinds[s["class_id"]] == "reserve" or s.get("late")),
                  key=lambda u: yes[u]["order"])


def update(d: dict, ev: dict, rng: random.Random | None = None) -> list[tuple[str, int | None, int | None]]:
    """Bringt die Einteilung auf den aktuellen Stand. Gibt Änderungen zurück: (user_id, altes Team, neues Team)."""
    rng = rng or random.Random()
    teams = {c["id"]: c for c in classes(d) if c["kind"] == "team"}
    yes = {uid: s for uid, s in ev["signups"].items() if s["status"] == "yes"}
    # Feste Plätze nur für Stammfahrer, die rechtzeitig angemeldet waren
    on_time = {tid: sum(1 for s in yes.values() if s["class_id"] == tid and not s.get("late")) for tid in teams}
    left = {tid: max(0, team["seats"] - on_time[tid]) for tid, team in teams.items()}

    # Wer fährt: die ersten X Wartenden in Anmelde-Reihenfolge (X = alle freien Plätze zusammen)
    drivers = candidates(d, ev)[:sum(left.values())]

    before = {uid: a["class_id"] for uid, a in ev["assignments"].items()}
    assigned = {}
    # Bisherige Plätze behalten, wenn möglich (Frühere zuerst)
    for uid in drivers:
        prev = ev["assignments"].get(uid)
        if prev and left.get(prev["class_id"], 0) > 0:
            assigned[uid] = prev
            left[prev["class_id"]] -= 1
    # Neue Plätze: späte Stammfahrer bevorzugt ins eigene Team, sonst ein zufälliger freier Platz
    now = time.time()
    for uid in drivers:
        if uid in assigned:
            continue
        own = yes[uid]["class_id"]
        tid = own if left.get(own, 0) > 0 else rng.choice([t for t, n in left.items() for _ in range(n)])
        assigned[uid] = {"class_id": tid, "at": now}
        left[tid] -= 1

    ev["assignments"] = assigned
    after = {uid: a["class_id"] for uid, a in assigned.items()}
    return [(uid, before.get(uid), after.get(uid)) for uid in sorted(set(before) | set(after)) if before.get(uid) != after.get(uid)]


def waiting(d: dict, ev: dict) -> list[str]:
    """Ohne Platz („Keine Einteilung“): Ersatzfahrer und späte Stammfahrer, in Anmelde-Reihenfolge."""
    return [uid for uid in candidates(d, ev) if uid not in ev["assignments"]]
