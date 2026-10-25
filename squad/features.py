"""Wires every feature into the bot: replies that don't need "@squad", commands, passive helpers,
private DM settings and timed jobs."""
import re
from datetime import datetime

from .life import LifeMixin
from .market import MarketMixin
from .money import MoneyMixin
from .rides import RidesMixin

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
FOOD_PASSIVE = re.compile(r"\b(any(?:one|body) want|who wants)\s+(boba|coffee|food|tacos|pizza|dutch|starbucks|chipotle|insomnia|cookies|"
                          r"panda|raising cane'?s|canes|in-n-out|wings|sushi|ramen|chick-fil-a|cfa)\b", re.I)

HELP_TOPICS = {
    "plans": "📅 plans: \"anyone free this weekend?\" → reply 1/2 → \"in\" → invite + reminder\n"
             "@squad decide tacos, sushi · @squad weather · @squad game day · @squad study buddies",
    "money": "💸 money: @squad bill (type it or attach the receipt) → \"claim 1 3\" → Venmo links · \"paid\" · @squad owes\n"
             "@squad food run boba 15 → \"+ taro tea $6\"",
    "market": "🛒 move-out market: @squad sell desk $40, lamp $10 pickup by 5/10 @ Tooker\n"
              "\"want #12\" (24h hold, fair line) · \"offer #12 $30\" · \"sold #12\" · \"drop #12 $25\" · \"pass #12\"\n"
              "@squad market furniture under $30 · @squad looking for mini fridge under $50",
    "rides": "🚗 rides: @squad ride to the game sat → \"driving 3\" / \"ride from Tooker\" → @squad match rides\n"
             "@squad airport (DM me \"squad flight dec 12 2pm\" first)",
    "project": "📚 projects: @squad project <name> due <date> → @squad tasks ... / suggest → dibs → @squad split\n"
               "\"done <part>\" · @squad status · @squad receipts",
    "home": "🏠 roommates: @squad chores dishes, trash · \"done dishes\" · @squad add oat milk · \"got oat milk\"",
    "fun": "🎲 fun: @squad this or that (on/off) · @squad flake meter on → \"track me\" · secret birthday planning by DM",
    "lang": "🌐 @squad translate <text> to spanish · @squad translate (last message) · @squad translate on (auto)",
    "catchup": "📝 @squad tldr · @squad tldr 6h",
    "privacy": "🔒 DM me \"squad setup\" to add your schedule privately. friends only ever see free/busy. @squad why shows what was shared",
}
HELP = ("hey 👋 i'm Squad Radar. what i do:\n" + "\n".join("- " + v.split("\n")[0] for v in HELP_TOPICS.values())
        + "\n\"@squad help market\" (or money, rides, project, home, fun, lang) for details")


class FeaturesMixin(MarketMixin, MoneyMixin, RidesMixin, LifeMixin):
    def contextual(self, msg, st, who, text, low):
        for h in (self.ctx_market, self.ctx_listit, self.ctx_bill, self.ctx_food, self.ctx_rides, self.ctx_tix, self.ctx_tot,
                  self.ctx_flake, self.ctx_paid, self.ctx_got, self.ctx_chore_done):
            r = h(msg, st, who, low)
            if r is not None:
                return r
        return None

    def feature_command(self, msg, st, who, body):
        low = body.lower()
        table = [
            (r"^help\s+(\w+)", lambda: [self.say(msg["chat_id"], HELP_TOPICS.get(next((k for k in HELP_TOPICS if k.startswith(re.match(r"^help\s+(\w+)", low).group(1)[:4])), ""), HELP))]),
            (r"^(sell|selling|list)\b", lambda: self.cmd_sell(msg, st, who, re.sub(r"(?i)^(sell|selling|list)\s*:?\s*", "", body))),
            (r"^(market|for sale|listings|my listings|what'?s for sale)", lambda: self.cmd_market(msg, st, who, body)),
            (r"^(iso|looking for|lf\b|in search of)", lambda: self.cmd_iso(msg, st, who, body)),
            (r"^(bill|split (the )?(bill|check)|receipt\b)", lambda: self.cmd_bill(msg, st, who, body)),
            (r"^close bill", lambda: self.close_bill(msg, st)),
            (r"^(owes|who owes|money|debts)", lambda: self.cmd_owes(msg, st, who, body)),
            (r"^(food run|run to|order from)", lambda: self.cmd_foodrun(msg, st, who, body)),
            (r"^close (run|food)", lambda: self.close_food(msg["chat_id"], st)),
            (r"^match rides", lambda: self.cmd_match_rides(msg, st, who, body)),
            (r"^(rides?|carpool)\b", lambda: self.cmd_ride(msg, st, who, body)),
            (r"^(airport|flights?)\b", lambda: self.cmd_airport(msg, st, who, body)),
            (r"^game ?day|^(next )?game\b", lambda: self.cmd_gameday(msg, st, who, body)),
            (r"^(tldr|tl;dr|catch me up|what did i miss|summary)", lambda: self.cmd_tldr(msg, st, who, body)),
            (r"^translate", lambda: self.cmd_translate(msg, st, who, body)),
            (r"^study", lambda: self.cmd_study(msg, st, who, body)),
            (r"^(weather|heat|forecast)", lambda: self.cmd_weather(msg, st, who, body)),
            (r"^flake", lambda: self.cmd_flake(msg, st, who, body)),
            (r"^this or that|^tot\b", lambda: self.cmd_tot(msg, st, who, body)),
            (r"^chores?\b", lambda: self.cmd_chores(msg, st, who, body)),
            (r"^(add|need|grocery|groceries|buy)\b", lambda: self.cmd_grocery(msg, st, who, body)),
            (r"^list$", lambda: self.cmd_grocery(msg, st, who, "list")),
        ]
        for pat, fn in table:
            if re.match(pat, low):
                return fn()
        return None

    def passive(self, msg, st, who, text):
        r = self.passive_market(msg, st, who, text)
        if r:
            return r
        m = FOOD_PASSIVE.search(text)
        if m and not st.get("food"):
            return [self.say(msg["chat_id"], "want me to run orders? %s, say \"@squad food run %s 15\" and everyone replies \"+ order $price\"" % (
                who, m.group(2).lower()))]
        return None

    def setup_extra(self, p, line, low):
        m = re.match(r"^flight\s+([a-z]{3})[a-z]*\.?\s+(\d{1,2})\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", low)
        if m:
            h = int(m.group(3)) % 12 + (12 if (m.group(5) or "am") == "pm" else 0)
            y = self.clock().year + (1 if MONTHS[m.group(1)] < self.clock().month - 1 else 0)
            p.setdefault("flights", []).append({"at": datetime(y, MONTHS[m.group(1)], int(m.group(2)), h, int(m.group(4) or 0)).isoformat()})
            return "flight"
        m = re.match(r"^(?:birthday|bday)\s+([a-z]{3})[a-z]*\.?\s+(\d{1,2})", low)
        if m:
            p["birthday"] = "%02d-%02d" % (MONTHS[m.group(1)], int(m.group(2)))
            return "birthday"
        m = re.match(r"^venmo\s+@?([\w-]+)", low)
        if m:
            p.setdefault("prefs", {})["venmo"] = m.group(1)
            return "venmo"
        m = re.match(r"^(?:language|lang)\s+([a-z]+)", low)
        if m:
            p.setdefault("prefs", {})["language"] = m.group(1)
            return "language"
        m = re.match(r"^(?:dorm|i live (?:in|at))\s+(.+)", low)
        if m:
            p.setdefault("prefs", {})["dorm"] = m.group(1).strip()
            return "dorm"
        m = re.match(r"^study buddy\s+(on|off|yes|no)", low)
        if m:
            p["study_buddy"] = m.group(1) in ("on", "yes")
            return "study buddy"
        return None

    def feature_tick(self, now):
        out = []
        for cid, st in list(self.state.items()):
            if cid.startswith("_"):
                continue
            out += self.tick_market(cid, st, now)
            out += self.tick_money(cid, st, now)
            out += self.heat_check(cid, st, now)
            out += self.tick_flake(cid, st, now)
            out += self.tick_tot(cid, st, now)
            out += self.tick_chores(cid, st, now)
            if 18 <= now.hour < 19 and st.get("members") and st.get("study_day") != now.date().isoformat():
                st["study_day"] = now.date().isoformat()
                out += self.cmd_study({"chat_id": cid, "members": st["members"]}, st, None, "", auto=True)
        out += self.tick_birthdays(now)
        return out
