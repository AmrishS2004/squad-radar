"""Group agent for projects: splits tasks using what each personal agent shares (hours free, skills,
things to avoid, whether it's an exam-heavy week), settles 'dibs' conflicts, and sets internal deadlines."""
from datetime import datetime, timedelta

GENERIC_TASKS = [
    {"name": "research + sources", "hours": 3, "skill": "research"},
    {"name": "outline + intro", "hours": 2, "skill": "writing"},
    {"name": "main section draft", "hours": 4, "skill": "writing"},
    {"name": "slides / visuals", "hours": 3, "skill": "slides"},
    {"name": "final edit + submit", "hours": 1.5, "skill": "editing"},
]


def allocate(tasks, agents, due, now, dibs=None):
    """Return (assignments, notes, overload). dibs: {task_name: [(when, member), ...]} in claim order."""
    dibs = dibs or {}
    cap = {a.name: a.hours for a in agents}
    heavy = {a.name: a.heavy_between(now, due) for a in agents}
    by = {a.name: a for a in agents}
    out, notes = {}, []
    remaining = list(tasks)

    # 1. dibs first, earliest claim wins; a second claimant is told and gets something else
    for t in tasks:
        claims = dibs.get(t["name"].lower(), [])
        for i, (_, who) in enumerate(claims):  # claim order, not alphabetical
            if who not in by:
                continue
            if t["name"] in out:
                notes.append("%s also called dibs on '%s' but %s got there first, so %s gets another part." % (
                    who, t["name"], out[t["name"]], who))
                continue
            if cap[who] >= t["hours"] * 0.6:
                out[t["name"]] = who
                cap[who] -= t["hours"]
                remaining.remove(t)
            else:
                notes.append("%s called dibs on '%s' but only has about %.0fh free, so I gave it to someone with more time." % (
                    who, t["name"], max(0, cap[who])))

    # 2. everything else: best fit by skill, things to avoid, free hours and exam weeks
    def fit(a, t):
        s = 0.0
        if t.get("skill") and t["skill"].lower() in a.skills:
            s += 3
        if any(av in t["name"].lower() or av == (t.get("skill") or "").lower() for av in a.avoid):
            s -= 5
        s += 2 * min(1.0, cap[a.name] / max(1.0, t["hours"]))
        if heavy[a.name]:
            s -= 1
        return s

    overload = False
    for t in sorted(remaining, key=lambda x: -x["hours"]):
        able = [a for a in agents if cap[a.name] >= t["hours"]]
        pool = able or agents
        if not able:
            overload = True
        best = max(pool, key=lambda a: (fit(a, t), cap[a.name]))
        out[t["name"]] = best.name
        cap[best.name] -= t["hours"]

    # 3. internal deadlines: two days before the real one, one day before for anyone in an exam week
    deadlines = {}
    for t in tasks:
        who = out[t["name"]]
        buffer = 1 if heavy[who] else 2
        d = due - timedelta(days=buffer)
        deadlines[t["name"]] = max(d, now + timedelta(hours=12)).isoformat()
        if heavy[who]:
            if by[who].share:
                notes.append("%s has an exam this stretch, so their part is due a day later." % who)
            else:
                notes.append("%s's part is due a day later to fit their week." % who)
    if overload:
        notes.append("Heads up: the parts add up to more hours than the group has free before the deadline. "
                     "Cut scope or start earlier?")
    seen, uniq = set(), []
    for n in notes:
        if n not in seen:
            uniq.append(n); seen.add(n)
    return out, deadlines, uniq, overload


def parse_tasks(text):
    """'intro slides 2h, research 3h, demo video' -> task dicts."""
    tasks = []
    for part in [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]:
        import re
        m = re.search(r"(\d+(?:\.\d+)?)\s*h(?:ours?|rs?)?\b", part)
        hours = float(m.group(1)) if m else 2.0
        name = re.sub(r"\(?\s*\d+(?:\.\d+)?\s*h(?:ours?|rs?)?\b\)?", "", part).strip(" -:")
        skill = None
        low = name.lower()
        for k, v in (("slide", "slides"), ("deck", "slides"), ("research", "research"), ("source", "research"),
                     ("video", "video"), ("edit", "editing"), ("code", "coding"), ("demo", "coding"), ("write", "writing"),
                     ("draft", "writing"), ("intro", "writing"), ("present", "presenting"), ("design", "design")):
            if k in low:
                skill = v; break
        if name:
            tasks.append({"name": name, "hours": hours, "skill": skill})
    return tasks
