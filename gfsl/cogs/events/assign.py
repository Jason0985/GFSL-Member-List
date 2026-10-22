"""Klassen (Teams/Ersatzfahrer) und die automatische Einteilung der Ersatzfahrer in freie Cockpits.

Ab der Anmeldefrist: freie Plätze eines Teams = Plätze - angemeldete Stammfahrer des Teams.
Wer einen Platz bekommt, entscheidet die Anmelde-Reihenfolge (wer zuerst angemeldet ist, fährt).
In welches Team er kommt, ist zufällig. Wer keinen Platz bekommt, landet unter „Keine Einteilung“.
Spätere Änderungen (neue Ersatzfahrer, Abmeldungen, späte Stammfahrer) werden nachgetragen,
ohne bestehende Einteilungen unnötig umzuwerfen.
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


def update(d: dict, ev: dict, rng: random.Random | None = None) -> list[tuple[str, int | None, int | None]]:
    """Bringt die Einteilung auf den aktuellen Stand. Gibt Änderungen zurück: (user_id, altes Team, neues Team)."""
    rng = rng or random.Random()
    teams = {c["id"]: c for c in classes(d) if c["kind"] == "team"}
    reserve_ids = {c["id"] for c in classes(d) if c["kind"] == "reserve"}
    yes = {uid: s for uid, s in ev["signups"].items() if s["status"] == "yes"}
    reserves = sorted((uid for uid, s in yes.items() if s["class_id"] in reserve_ids), key=lambda u: yes[u]["order"])
    stamm = {tid: sum(1 for s in yes.values() if s["class_id"] == tid) for tid in teams}

    # Wer fährt: die ersten X Ersatzfahrer in Anmelde-Reihenfolge (X = alle freien Plätze zusammen)
    total_free = sum(max(0, team["seats"] - stamm[tid]) for tid, team in teams.items())
    drivers = reserves[:total_free]
    order = {uid: yes[uid]["order"] for uid in reserves}

    before = {uid: a["class_id"] for uid, a in ev["assignments"].items()}
    # Bisherige Plätze behalten, solange der Fahrer noch dran ist und das Team noch existiert
    assigned = {uid: a for uid, a in ev["assignments"].items() if uid in drivers and a["class_id"] in teams}
    # Team überbucht (z. B. Stammfahrer spät angemeldet): der zuletzt Angemeldete muss in ein anderes Team
    for tid, team in teams.items():
        in_team = sorted((uid for uid, a in assigned.items() if a["class_id"] == tid), key=lambda u: order[u])
        while in_team and stamm[tid] + len(in_team) > team["seats"]:
            assigned.pop(in_team.pop())

    free = [tid for tid, team in teams.items()
            for _ in range(max(0, team["seats"] - stamm[tid] - sum(1 for a in assigned.values() if a["class_id"] == tid)))]
    pool = [uid for uid in drivers if uid not in assigned]  # schon in Anmelde-Reihenfolge
    rng.shuffle(free)  # nur das Team ist Zufall
    now = time.time()
    for uid, tid in zip(pool, free):
        assigned[uid] = {"class_id": tid, "at": now}

    ev["assignments"] = assigned
    after = {uid: a["class_id"] for uid, a in assigned.items()}
    return [(uid, before.get(uid), after.get(uid)) for uid in sorted(set(before) | set(after)) if before.get(uid) != after.get(uid)]


def waiting(d: dict, ev: dict) -> list[str]:
    """Angemeldete Ersatzfahrer ohne Platz („Keine Einteilung“), in Anmelde-Reihenfolge."""
    reserve_ids = {c["id"] for c in classes(d) if c["kind"] == "reserve"}
    yes = [(uid, s) for uid, s in ev["signups"].items() if s["status"] == "yes" and s["class_id"] in reserve_ids]
    return [uid for uid, s in sorted(yes, key=lambda e: e[1]["order"]) if uid not in ev["assignments"]]
