"""Carpools, airport rides before breaks, and game day roll calls."""
import math
import re
from datetime import date, datetime, timedelta

from .timeparse import parse_clock, window

# approximate ASU Tempe pickup spots (lat, lon) — good enough to order pickups, not for navigation
SPOTS = {
    "tooker": (33.4158, -111.9258), "manzanita": (33.4167, -111.9349), "hassayampa": (33.4128, -111.9319),
    "barrett": (33.4157, -111.9279), "vista del sol": (33.4044, -111.9284), "sonora": (33.4117, -111.9303),
    "san pablo": (33.4176, -111.9276), "palo verde": (33.4140, -111.9348), "adelphi": (33.4074, -111.9233),
    "mu": (33.4183, -111.9346), "hayden": (33.4195, -111.9342), "brickyard": (33.4237, -111.9397),
    "mill ave": (33.4250, -111.9400), "district": (33.4176, -111.9228), "union": (33.4206, -111.9200),
}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def clock(dt):
    return dt.strftime("%I").lstrip("0") + ("" if dt.minute == 0 else dt.strftime(":%M")) + dt.strftime("%p").lower()


def spot(name):
    n = (name or "").lower()
    for k, v in SPOTS.items():
        if k in n:
            return k, v
    return None, None


def dist(a, b):
    return math.hypot((a[0] - b[0]) * 111, (a[1] - b[1]) * 93)


class RidesMixin:
    def cmd_ride(self, msg, st, who, text):
        cid = msg["chat_id"]
        to = re.sub(r"(?i)^(rides?|carpool)\s*(to|for)?\s*", "", text).strip() or "the plan"
        s, e, label = window(to, self.clock())
        st["rides"] = {"to": to, "drivers": {}, "riders": {}, "status": "open", "assign": None, "acks": [], "opened": self.clock().isoformat()}
        return [self.say(cid, "🚗 carpool to %s\ndrivers reply \"driving 3\" (seats)\nriders reply \"ride from Tooker\" (or your dorm)\nthen \"@squad match rides\"" % to)]

    def ctx_rides(self, msg, st, who, low):
        r = st.get("rides")
        if not r or r["status"] == "done":
            return None
        cid = msg["chat_id"]
        m = re.match(r"^(?:i'?m\s+|i\s+can\s+|can\s+)?driv(?:e|ing)(?:\s+(\d))?(?:\s+seats?)?(?:\s+from\s+(.+))?", low)
        if m:
            seats = int(m.group(1) or 3)
            r["drivers"][who] = {"seats": seats, "from": (m.group(2) or self._home(who) or "").strip()}
            return [self.say(cid, "🚘 %s driving, %d seats" % (who, seats))]
        m = re.match(r"^(?:need a |i need a |can i get a )?ride(?:\s+from\s+(.+))?$", low)
        if m:
            r["riders"][who] = {"from": (m.group(1) or self._home(who) or "").strip()}
            return [self.say(cid, "🙋 %s needs a ride%s" % (who, (" from " + r["riders"][who]["from"].title()) if r["riders"][who]["from"] else ""))]
        if r["status"] == "matched" and re.fullmatch(r"(ok|okay|👍|bet|got it)[.!]*", low) and who in r["drivers"] and who not in r["acks"]:
            r["acks"].append(who)
            if set(r["acks"]) >= set(d for d, riders in r["assign"].items() if riders):
                r["status"] = "done"
                return [self.say(cid, "all drivers confirmed ✅ see you there")]
            return [self.say(cid, "%s confirmed 🚘" % who)]
        return None

    def _home(self, who):
        return self.profiles.get(who, {}).get("prefs", {}).get("dorm", "")

    def cmd_match_rides(self, msg, st, who, text):
        cid = msg["chat_id"]
        r = st.get("rides")
        if not r:
            return [self.say(cid, "start one: \"@squad ride to the game sat 5pm\"")]
        if not r["drivers"]:
            return [self.say(cid, "no drivers yet 😭 anyone? reply \"driving 3\"")]
        left = {d: v["seats"] for d, v in r["drivers"].items()}
        assign = {d: [] for d in r["drivers"]}
        riders = sorted(r["riders"].items())
        waitlist = []
        for name, info in riders:
            if name in r["drivers"]:
                continue
            _, rp = spot(info["from"])
            best = None
            for d, v in r["drivers"].items():
                if left[d] <= 0:
                    continue
                _, dp = spot(v["from"])
                cost = dist(rp, dp) if rp and dp else 5
                if best is None or cost < best[0]:
                    best = (cost, d)
            if best:
                assign[best[1]].append(name)
                left[best[1]] -= 1
            else:
                waitlist.append(name)
        lines = ["🚗 rides to %s:" % r["to"]]
        for d, rs in assign.items():
            if not rs:
                lines.append("- %s: driving solo (seats open)" % d)
                continue
            start = spot(r["drivers"][d]["from"])[1]
            order, cur = [], start
            pool = list(rs)
            while pool:
                nxt = min(pool, key=lambda n: dist(cur, spot(r["riders"][n]["from"])[1]) if cur and spot(r["riders"][n]["from"])[1] else 0)
                order.append(nxt)
                cur = spot(r["riders"][nxt]["from"])[1] or cur
                pool.remove(nxt)
            stops = " → ".join("%s (%s)" % (n, (r["riders"][n]["from"] or "?").title()) for n in order)
            lines.append("- %s picks up: %s" % (d, stops))
        if waitlist:
            lines.append("⚠️ no seat yet: %s. split an Uber or another driver?" % ", ".join(waitlist))
        lines.append("riders chip in ~$3-5 for gas 🙏 drivers reply \"ok\" to confirm")
        r["assign"], r["status"] = assign, "matched"
        return [self.say(cid, "\n".join(lines))]

    # ----- airport rides before breaks -----
    def cmd_airport(self, msg, st, who, text):
        cid = msg["chat_id"]
        names = [self.name_of(h) for h in msg["members"]]
        flights = []
        now = self.clock()
        for n in names:
            for f in self.profiles.get(n, {}).get("flights", []):
                dt = datetime.fromisoformat(f["at"]).replace(tzinfo=self.tz)
                if now <= dt <= now + timedelta(days=45):
                    flights.append((dt, n))
        if not flights:
            return [self.say(cid, "nobody's told me their flight yet ✈️ DM me \"squad flight dec 12 2pm\" (only the leave time is shared)")]
        flights.sort()
        groups, cur = [], []
        for dt, n in flights:
            if cur and (dt - cur[0][0] > timedelta(hours=2) or dt.date() != cur[0][0].date()):
                groups.append(cur); cur = []
            cur.append((dt, n))
        if cur:
            groups.append(cur)
        lines = ["✈️ airport rides to Sky Harbor:"]
        for g in groups:
            leave = g[0][0] - timedelta(hours=2, minutes=30)
            people = ", ".join(n for _, n in g)
            if len(g) > 1:
                lines.append("- %s, leave %s: %s (one Uber ~$25, ~$%d each)" % (
                    leave.strftime("%a %b ") + str(leave.day), clock(leave), people, round(25 / len(g))))
            else:
                lines.append("- %s, leave %s: %s (solo, anyone driving?)" % (
                    leave.strftime("%a %b ") + str(leave.day), clock(leave), people))
        lines.append("(only leave times are shared, not flight details)")
        return [self.say(cid, "\n".join(lines))]

    # ----- game day -----
    def cmd_gameday(self, msg, st, who, text):
        cid = msg["chat_id"]
        now = self.clock()
        games = sorted([e for e in self.events if e.get("type") == "game" and datetime.fromisoformat(e["start"]).replace(tzinfo=self.tz) >= now],
                       key=lambda e: e["start"])
        if not games:
            return [self.say(cid, "no games on my list right now 🏈")]
        g = games[0]
        ko = datetime.fromisoformat(g["start"]).replace(tzinfo=self.tz)
        tail = ko - timedelta(hours=2, minutes=30)
        st["game"] = {"title": g["title"], "start": ko.isoformat(), "tix": {}, "need": {}}
        return [self.say(cid, "🏈 %s, %s @ %s\ntailgate from %s · reply \"got tix\" or \"need tix\"\nrides: \"@squad ride to the game\"" % (
            g["title"], ko.strftime("%a %b ") + str(ko.day) + " " + clock(ko), g.get("where", ""), clock(tail)))]

    def ctx_tix(self, msg, st, who, low):
        g = st.get("game")
        if not g or not re.fullmatch(r"(got|have|need)\s+(tix|tickets?)[!. 🎟️]*", low):
            return None
        if low.startswith("need"):
            g["need"][who] = True
            g["tix"].pop(who, None)
        else:
            g["tix"][who] = True
            g["need"].pop(who, None)
        return [self.say(msg["chat_id"], "🎟️ got tix: %s · need tix: %s" % (", ".join(sorted(g["tix"])) or "nobody", ", ".join(sorted(g["need"])) or "nobody"))]
