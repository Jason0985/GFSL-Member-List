"""Rollen je Antwort: Bei Dropdown-Fragen (z. B. Plattform) bekommt der Fahrer die Rolle, die zu seiner Auswahl gehört.

Die Zuordnung steht an der Frage: q["option_roles"] = {"PlayStation": rollen_id, ...}
Der Wert eines Fahrers kommt aus seinen Fahrerdaten (Plattform/Flagge), sonst aus seiner letzten
akzeptierten Verifizierung. Fehlt beides, wird er aus einer schon vorhandenen passenden Rolle übernommen.
"""
from dataclasses import dataclass, field

import discord

from gfsl import storage

from . import drivers
from . import questions as Q
from .constants import Colors
from .flags import lookup_flag

ROLE_TYPES = ("select", "flag", "yesno")
PURPOSE_FIELDS = {"platform": "platform", "flag": "flag"}  # Feld der Frage -> Schlüssel in den Fahrerdaten


@dataclass
class SyncReport:
    added: int = 0
    removed: int = 0
    from_role: int = 0
    unknown: list[str] = field(default_factory=list)   # User-IDs ohne Angabe
    errors: list[str] = field(default_factory=list)

    def text(self) -> str:
        lines = [f"✅ {self.added} Rolle(n) vergeben, {self.removed} entfernt."]
        if self.from_role:
            lines.append(f"ℹ️ Bei {self.from_role} Fahrer(n) wurde die Angabe aus einer vorhandenen Rolle übernommen.")
        if self.unknown:
            self.unknown = list(dict.fromkeys(self.unknown))
            users = " ".join(f"<@{uid}>" for uid in self.unknown[:30]) + (" …" if len(self.unknown) > 30 else "")
            lines.append(f"❔ {len(self.unknown)} Fahrer ohne Angabe (mit `/verify fahrer setzen` nachtragen): {users}")
        lines += [f"⚠️ {e}" for e in dict.fromkeys(self.errors)][:10]
        return "\n".join(lines)


def mapped_questions(questions: list[dict]) -> list[dict]:
    return [q for q in questions if q["type"] in ROLE_TYPES and q.get("option_roles")]


def match_option(q: dict, value) -> str | None:
    """Ordnet einen gespeicherten Wert einer Option zu, auch bei leicht anderer Schreibweise (z. B. „PS5“ → PlayStation)."""
    if value is None:
        return None
    if isinstance(value, bool):
        value = "Ja" if value else "Nein"
    value = str(value).strip().lower()
    options = Q.options_for(q)
    for o in options:
        if o.lower() == value:
            return o
    for o in options:
        ol = o.lower()
        if value and (value in ol or ol in value):
            return o
        if "playstation" in ol and value.startswith("ps"):
            return o
    return None


def prune(q: dict) -> None:
    """Zuordnungen zu Optionen entfernen, die es nicht mehr gibt."""
    if q["type"] not in ROLE_TYPES:
        q["option_roles"] = {}
    elif q.get("option_roles"):
        options = set(Q.options_for(q))
        q["option_roles"] = {o: r for o, r in q["option_roles"].items() if o in options}


def driver_value(d: dict, uid: str, q: dict):
    drv = d["drivers"].get(uid, {})
    if key := PURPOSE_FIELDS.get(q["purpose"]):
        if drv.get(key) is not None:
            return drv[key]
    apps = [a for a in d["applications"].values() if a["user_id"] == uid and a["status"] == "accepted"]
    for app in sorted(apps, key=lambda a: a["created_at"], reverse=True):
        if (v := app["answers"].get(str(q["id"]))) is not None:
            return v
    return None


async def sync_member(member: discord.Member, d: dict, questions: list[dict], report: SyncReport,
                      remove_others: bool = False) -> None:
    uid = str(member.id)
    for q in questions:
        mapping = q.get("option_roles") or {}
        option = match_option(q, driver_value(d, uid, q))
        if option is None:
            # Keine Angabe gespeichert: aus einer vorhandenen Rolle ableiten (nur wenn eindeutig)
            have = [o for o, rid in mapping.items() if member.get_role(rid)]
            if len(have) == 1:
                option = have[0]
                report.from_role += 1
                if key := PURPOSE_FIELDS.get(q["purpose"]):
                    d["drivers"].setdefault(uid, {})[key] = option
                    storage.save()
            else:
                report.unknown.append(uid)
            continue

        target = member.guild.get_role(mapping.get(option) or 0)
        if target and not member.get_role(target.id):
            try:
                await member.add_roles(target, reason=f"GFSL Verify: {q['label']} = {option}")
                report.added += 1
            except discord.HTTPException as e:
                report.errors.append(f"{target.mention} nicht vergeben: {e.text or e}")
        if remove_others:
            for o, rid in mapping.items():
                role = member.get_role(rid)
                if o != option and role and rid != (target.id if target else None):
                    try:
                        await member.remove_roles(role, reason=f"GFSL Verify: {q['label']} = {option}")
                        report.removed += 1
                    except discord.HTTPException as e:
                        report.errors.append(f"{role.mention} nicht entfernt: {e.text or e}")


async def sync_all(guild: discord.Guild, d: dict, questions: list[dict], remove_others: bool) -> SyncReport:
    report = SyncReport()
    for uid in list(d["drivers"]):
        member = guild.get_member(int(uid))
        if member is not None:
            await sync_member(member, d, questions, report, remove_others)
    return report


async def refresh_list_quietly(guild: discord.Guild, d: dict) -> None:
    try:
        await drivers.refresh_driver_list(guild, d)
    except (discord.HTTPException, RuntimeError):
        pass


def role_warning(guild: discord.Guild, role: discord.Role | None) -> str:
    if role and (role >= guild.me.top_role or role.managed):
        return " ⚠️ steht über meiner Bot-Rolle"
    return ""


class OptionRolesView(discord.ui.View):
    """/verify fragen rollen: Option wählen, dann die Rolle dazu."""

    def __init__(self, guild: discord.Guild, user_id: int, d: dict, question_id: int):
        super().__init__(timeout=900)
        self.guild = guild
        self.user_id = user_id
        self.d = d
        self.qid = question_id
        self.option: str | None = None
        self.note: str | None = None

    @property
    def q(self) -> dict | None:
        return next((q for q in self.d["questions"] if q["id"] == self.qid), None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.q is None:
            await interaction.response.edit_message(content="Diese Frage gibt es nicht mehr.", embed=None, view=None)
            return False
        return interaction.user.id == self.user_id

    async def refresh(self, interaction: discord.Interaction, note: str | None = None):
        self.note = note
        await interaction.response.edit_message(embed=self.render(), view=self)

    def render(self) -> discord.Embed:
        q = self.q
        mapping = q.setdefault("option_roles", {})
        options = Q.options_for(q)[:25]
        if self.option not in options:
            self.option = None
        self.clear_items()

        lines = []
        for o in options:
            role = self.guild.get_role(mapping.get(o) or 0)
            lines.append(f"**{o}** → {role.mention + role_warning(self.guild, role) if role else '—'}")
        embed = discord.Embed(color=Colors.info, title=f"🎭 Rollen für „{q['label']}“", description=(
            (f"{self.note}\n\n" if self.note else "")
            + "Wer im Formular diese Option wählt, bekommt beim Akzeptieren die Rolle.\n\n" + "\n".join(lines)
            + "\n\n**1.** Option wählen · **2.** Rolle wählen (Auswahl leeren = keine Rolle)\n"
              "Mit **Auf alle Fahrer anwenden** bekommen auch bereits verifizierte Fahrer ihre Rolle."))

        pick = discord.ui.Select(placeholder="1. Option wählen …", row=0, options=[
            discord.SelectOption(label=o[:100], value=str(n), default=(o == self.option),
                                 emoji=lookup_flag(o) if q["type"] == "flag" else None)
            for n, o in enumerate(options)
        ])

        async def on_pick(i: discord.Interaction):
            self.option = options[int(pick.values[0])]
            await self.refresh(i)
        pick.callback = on_pick
        self.add_item(pick)

        current = mapping.get(self.option) if self.option else None
        role_select = discord.ui.RoleSelect(
            placeholder=f"2. Rolle für „{self.option}“ …"[:150] if self.option else "2. Erst oben eine Option wählen",
            row=1, min_values=0, max_values=1, disabled=self.option is None,
            default_values=[discord.Object(current)] if current else [])

        async def on_role(i: discord.Interaction):
            if role_select.values:
                mapping[self.option] = role_select.values[0].id
            else:
                mapping.pop(self.option, None)
            storage.save()
            await self.refresh(i, "✅ Gespeichert.")
            await refresh_list_quietly(self.guild, self.d)  # Plattform in der Liste kommt aus den Rollen
        role_select.callback = on_role
        self.add_item(role_select)

        async def apply(i: discord.Interaction):
            await i.response.defer()
            report = await sync_all(self.guild, self.d, [self.q], remove_others=False)
            await refresh_list_quietly(self.guild, self.d)
            self.note = report.text()[:1500]
            await i.edit_original_response(embed=self.render(), view=self)
        button = discord.ui.Button(label="Auf alle Fahrer anwenden", style=discord.ButtonStyle.primary, emoji="🔁",
                                   row=2, disabled=not mapping)
        button.callback = apply
        self.add_item(button)
        return embed
