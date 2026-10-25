"""Group agent for hangouts: asks each personal agent about candidate times, resolves conflicts,
and proposes up to two options. Keeps a log of exactly what each agent shared, for '@squad why'."""
from datetime import datetime, timedelta

from .timeparse import fmt_slot

FALLBACK_IDEAS = [
    # (earliest hour, latest hour, idea, price per person, diet tags)
    (7, 11, "breakfast tacos + coffee", 10, ["vegetarian"]),
    (11, 15, "lunch at the MU food court", 12, ["vegetarian", "vegan"]),
    (14, 18, "boba run + walk on Palm Walk", 7, ["vegetarian", "vegan"]),
    (17, 21, "dinner on Mill Ave", 15, ["vegetarian"]),
    (19, 24, "game night at someone's place, everyone brings a snack", 5, ["vegetarian", "vegan"]),
    (20, 24, "late-night Insomnia Cookies + a movie", 8, ["vegetarian"]),
]


def _overlaps(a0, a1, b0, b1):
    return a0 < b1 and b0 < a1


class HangoutPlanner:
    def __init__(self, agents, events, tz):
        self.agents = agents
        self.events = events
        self.tz = tz
        self.log = []

    def _event_dt(self, e):
        return datetime.fromisoformat(e["start"]).replace(tzinfo=self.tz)

    def group_budget_ok(self, price):
        return all(a.budget_ok(price) for a in self.agents)

    def group_diet_ok(self, tags):
        return all(a.diet_ok(tags) for a in self.agents)

    def evaluate(self, start, end):
        missing, soft, notes = [], 0, []
        for a in self.agents:
            r = a.check(start, end)
            if not r["ok"]:
                missing.append((a.name, r["reason"]))
            soft += r["soft"]
            for n in r["notes"]:
                notes.append("%s: %s" % (a.name, n))
        return missing, soft, notes

    def activity_for(self, start, end, hint=""):
        for e in self.events:
            es = self._event_dt(e)
            if start - timedelta(minutes=15) <= es <= start + timedelta(minutes=45):
                if self.group_budget_ok(e.get("price", 0)) and self.group_diet_ok(e.get("diet", ["vegetarian", "vegan"])):
                    return {"what": e["title"], "where": e.get("where", ""), "price": e.get("price", 0),
                            "free_food": e.get("free_food", False), "source": "event"}
        h = start.hour
        hint = (hint or "").lower()
        ideas = [i for i in FALLBACK_IDEAS if i[0] <= h < i[1]]
        if hint:
            pref = [i for i in ideas if any(w in i[2] for w in hint.split())]
            ideas = pref or ideas
        for lo, hi, idea, price, tags in ideas:
            if self.group_budget_ok(price) and self.group_diet_ok(tags):
                return {"what": idea, "where": "", "price": price, "free_food": False, "source": "idea"}
        return {"what": "hang out on campus (free)", "where": "", "price": 0, "free_food": False, "source": "idea"}

    def plan(self, win_start, win_end, minutes, hint=""):
        self.log = []
        dur = timedelta(minutes=minutes)
        cands = set()
        day = win_start.date()
        while day <= win_end.date():
            t = datetime(day.year, day.month, day.day, 10, 0, tzinfo=self.tz)
            last = datetime(day.year, day.month, day.day, 22, 30, tzinfo=self.tz)
            while t <= last:
                if t >= win_start and t + dur <= win_end + timedelta(minutes=1):
                    cands.add(t)
                t += timedelta(minutes=30)
            day += timedelta(days=1)
        for e in self.events:
            es = self._event_dt(e)
            if win_start <= es and es + dur <= win_end + timedelta(minutes=1):
                cands.add(es)
        scored = []
        for s in sorted(cands):
            e = s + dur
            missing, soft, notes = self.evaluate(s, e)
            act = self.activity_for(s, e)
            pref = 0
            if 17 <= s.hour <= 20:
                pref -= 2
            if s.weekday() >= 5 and 11 <= s.hour <= 20:
                pref -= 1
            if act["source"] == "event":
                pref -= 3 + (1 if act["free_food"] else 0)
                if any(self._event_dt(ev) == s for ev in self.events):
                    pref -= 2
            score = len(missing) * 100 + soft + pref
            scored.append((score, s, e, missing, notes, act))
        scored.sort(key=lambda x: (x[0], x[1]))
        picks = []
        for cand in scored:
            if len(picks) == 2:
                break
            if all(cand[1].date() != p[1].date() or abs((cand[1] - p[1]).total_seconds()) >= 3 * 3600 for p in picks):
                picks.append(cand)
        # transparency log: what each agent shared, never their raw schedule
        for a in self.agents:
            blocks = a.free_blocks(win_start, win_end)
            self.log.append("%s's agent shared %d free block%s, budget fit for each option: %s%s" % (
                a.name, len(blocks), "" if len(blocks) == 1 else "s",
                ", ".join("yes" if a.budget_ok(p[5]["price"]) else "no" for p in picks) or "n/a",
                "" if a.share else " (keeps reasons private)"))
        self.worked_around = []
        for a in self.agents:
            for bs, be, label in a.busy_public(win_start, win_end):
                if be - bs >= timedelta(hours=1) and bs.hour < 22 and be.hour >= 11:
                    self.worked_around.append((bs, "%s busy %s%s" % (a.name, fmt_slot(bs, be), (" (%s)" % label) if label else "")))
            for night, label in a.late_limits(win_start, win_end):
                self.worked_around.append((night.replace(hour=21), "nothing late %s (%s)" % (
                    night.strftime("%a"), ("%s: %s" % (a.name, label)) if label else "%s has an early start" % a.name)))
        self.worked_around.sort(key=lambda x: x[0])
        options = []
        for score, s, e, missing, notes, act in picks:
            options.append({"start": s.isoformat(), "end": e.isoformat(), "missing": missing, "notes": notes,
                            "activity": act, "everyone": not missing})
        if options and not options[0]["everyone"]:
            self.log.append("No time fits everyone in this window, so I picked the times that miss the fewest people.")
        return options
