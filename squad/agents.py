"""Personal agents: one per student. Each holds that student's private schedule and preferences
and only ever shares the minimum the group needs (free/busy, a pass/fail on budget, diet tags,
hours available). Reasons are shared only if the student turned `share_reasons` on."""
from datetime import date, datetime, timedelta

from .timeparse import parse_clock

DAY_LETTERS = {"M": 0, "T": 1, "W": 2, "R": 3, "F": 4, "S": 5, "U": 6}


def parse_days(s):
    s = s.strip()
    s = s.replace("Th", "R").replace("TH", "R").replace("Su", "U").replace("SU", "U").replace("Sa", "S").replace("SA", "S")
    out = set()
    for ch in s:
        if ch.upper() in DAY_LETTERS:
            out.add(DAY_LETTERS[ch.upper()])
    return out


class PersonalAgent:
    def __init__(self, profile, tz):
        self.p = profile
        self.tz = tz
        self.name = profile["name"]
        self.handles = [h.lower() for h in profile.get("handles", [])]
        self.share = bool(profile.get("share_reasons", False))
        prefs = profile.get("prefs", {})
        self.earliest = parse_clock(prefs.get("earliest", "9:00"))
        self.latest = parse_clock(prefs.get("latest", "23:30"))
        self.budget = prefs.get("budget")
        self.diet = [d.lower() for d in prefs.get("diet", [])]
        self.quiet_before_exam = prefs.get("no_late_before_exam", True)
        proj = profile.get("project", {})
        self.hours = float(proj.get("hours_this_week", 5))
        self.skills = [s.lower() for s in proj.get("skills", [])]
        self.avoid = [s.lower() for s in proj.get("avoid", [])]

    # ----- private -----
    def _dt(self, d, hm):
        return datetime(d.year, d.month, d.day, hm[0], hm[1], tzinfo=self.tz)

    def _busy(self, d):
        """Private: every commitment on day d as (start, end, label, kind)."""
        out = []
        for c in self.p.get("classes", []):
            if d.weekday() in parse_days(c["days"]):
                out.append((self._dt(d, parse_clock(c["start"])), self._dt(d, parse_clock(c["end"])), c.get("label", "class"), "class"))
        for b in self.p.get("busy", []):
            if date.fromisoformat(b["date"]) == d:
                out.append((self._dt(d, parse_clock(b["start"])), self._dt(d, parse_clock(b["end"])), b.get("label", "busy"), "busy"))
        for e in self.p.get("exams", []):
            if date.fromisoformat(e["date"]) == d:
                st = self._dt(d, parse_clock(e.get("time", "9:00")))
                out.append((st, st + timedelta(minutes=int(e.get("minutes", 120))), e.get("label", "exam"), "exam"))
        return out

    def _exams_between(self, start, end):
        res = []
        for e in self.p.get("exams", []):
            st = self._dt(date.fromisoformat(e["date"]), parse_clock(e.get("time", "9:00")))
            if start <= st <= end:
                res.append((st, e.get("label", "exam")))
        return res

    # ----- shared with the group agent -----
    def check(self, start, end):
        """Can I make start-end? Shares only ok/not, a public reason, and a soft-cost number."""
        hard = None
        d = start.date()
        while d <= end.date():
            for bs, be, label, kind in self._busy(d):
                if bs < end and start < be:
                    hard = label if self.share else "a commitment"
                    break
            if hard:
                break
            d += timedelta(days=1)
        soft, notes = 0, []
        e_lo = self._dt(start.date(), self.earliest)
        e_hi = self._dt(start.date(), self.latest)
        if start < e_lo:
            soft += 3
            notes.append("prefers later")
        if end > e_hi:
            soft += 3
            notes.append("prefers earlier")
        if self.quiet_before_exam:
            late = end.hour >= 21 or end.date() > start.date()
            for ex_at, label in self._exams_between(end, end + timedelta(hours=14)):
                if late:
                    soft += 6
                    notes.append(("%s the next morning" % label) if self.share else "something early the next day")
                    break
        return {"ok": hard is None, "reason": hard, "soft": soft, "notes": notes}

    def free_blocks(self, start, end, step_min=30):
        """Shared: free intervals only, no labels."""
        blocks, cur, t = [], None, start
        while t < end:
            nxt = t + timedelta(minutes=step_min)
            free = self.check(t, nxt)["ok"]
            if free and cur is None:
                cur = t
            if not free and cur is not None:
                blocks.append((cur, t)); cur = None
            t = nxt
        if cur is not None:
            blocks.append((cur, end))
        return blocks

    def budget_ok(self, price):
        """Shared: yes/no only, never the number."""
        return self.budget is None or price is None or price <= self.budget

    def diet_ok(self, tags):
        if not self.diet:
            return True
        tags = [t.lower() for t in (tags or [])]
        return all(d in tags for d in self.diet)

    def heavy_between(self, start, end):
        """Shared: just whether this is an exam-heavy stretch, not which exams."""
        return len(self._exams_between(start, end)) > 0

    def busy_public(self, start, end):
        """Shared on purpose: busy times inside the window, with the reason only if the student allows it."""
        out, d = [], start.date()
        while d <= end.date():
            for bs, be, label, kind in self._busy(d):
                if bs < end and start < be:
                    out.append((max(bs, start), min(be, end), label if self.share else None))
            d += timedelta(days=1)
        return out

    def late_limits(self, start, end):
        """Shared on purpose: nights to keep short because of an early commitment the next morning."""
        out = []
        for ex_at, label in self._exams_between(start, end + timedelta(hours=14)):
            night = ex_at - timedelta(days=1)
            if start.date() <= night.date() <= end.date() and self.quiet_before_exam:
                out.append((night, ("%s next morning" % label) if self.share else None))
        return out

    def summary_public(self):
        return {"name": self.name, "shares_reasons": self.share}
