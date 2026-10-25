"""tldr, translation, study buddies, Phoenix heat check, flake meter, secret birthdays, this-or-that,
roommate chores and groceries."""
import json
import random
import re
import urllib.request
from datetime import date, datetime, timedelta

from .agents import PersonalAgent
from .llm import TLDR_SYSTEM, TRANSLATE_SYSTEM
from .planner import HangoutPlanner
from .timeparse import fmt_slot

COURSE = re.compile(r"\b([A-Z]{3})\s?(\d{3})\b")
OUTDOOR = re.compile(r"lawn|walk|park|lake|hike|market|tailgate|outdoor|pool|picnic|stadium|mountain|trail", re.I)
TOT = [("Taco Bell", "In-N-Out at 2am"), ("Hayden Library", "Noble Library"), ("Dutch Bros", "Starbucks"),
       ("110°F summer", "freezing winter"), ("8am class", "6pm class"), ("Insomnia cookies", "Crumbl"),
       ("group project", "solo final"), ("tailgate", "watch from the couch"), ("Mill Ave", "Tempe Marketplace"),
       ("light rail", "scooter")]
LANGS = ["english", "spanish", "hindi", "tamil", "telugu", "mandarin", "chinese", "korean", "japanese", "arabic", "french",
         "german", "portuguese", "vietnamese", "urdu", "bengali", "gujarati", "marathi", "kannada", "malayalam", "punjabi",
         "russian", "italian", "turkish", "farsi", "persian", "tagalog", "nepali", "indonesian", "thai"]


EN = set("the a an and or but is are was were be to of in on at for with you your i me my we us it this that what "
         "when where who how why do does did not no yes can will just so lol ok okay im i'm u ur pls thanks have has".split())
FOREIGN = set(("que de la el los las un una es por para con qué quiere alguien hoy mañana vamos dónde cuando pero muy "
               "le les est et pas vous nous avec pour je tu oui non "
               "kya hai hain nahi kab kahan kaise yaar bhai acha accha chalo karo kal aaj "
               "enna epdi enga illa vaa poda machan macha seri sapdu naalaiku "
               "eppudu ekkada ledu raa ra cheppu "
               "und ist nicht ich du wir sie das der die mit").split())


def looks_non_english(text):
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 4:
        return False
    non_ascii = sum(1 for c in letters if ord(c) > 127)
    if non_ascii / len(letters) > 0.4:
        return True
    words = re.findall(r"[a-zA-Záéíóúñü']+", text.lower())
    en = sum(1 for w in words if w in EN)
    fo = sum(1 for w in words if w in FOREIGN)
    return fo >= 1 and fo > en


class LifeMixin:
    # ----- memory of recent group messages (for tldr / translate last) -----
    def remember(self, st, who, text, guid=None):
        h = st.setdefault("history", [])
        st["hseq"] = st.get("hseq", 0) + 1
        x = {"id": st["hseq"], "who": who, "text": text[:500], "at": self.clock().isoformat()}
        if guid:
            x["guid"] = guid
        h.append(x)
        if len(h) > 300:
            del h[: len(h) - 300]

    def cmd_tldr(self, msg, st, who, text):
        cid = msg["chat_id"]
        h = st.get("history", [])
        mine = [i for i, m in enumerate(h) if m["who"] == who]
        start = mine[-1] + 1 if mine else max(0, len(h) - 60)
        m = re.search(r"(\d+)\s*h", text)
        if m:
            cutoff = self.clock() - timedelta(hours=int(m.group(1)))
            start = next((i for i, x in enumerate(h) if datetime.fromisoformat(x["at"]) >= cutoff), len(h))
        chunk = h[start:][-150:]
        if not chunk:
            return [self.say(cid, "you didn't miss anything 😌")]
        convo = "\n".join("%s: %s" % (x["who"], x["text"]) for x in chunk)
        if self.llm and self.llm.enabled:
            s = self.llm.chat("Reader: %s\n\nMessages:\n%s" % (who, convo), system=TLDR_SYSTEM, max_tokens=350)
            if s:
                return [self.say(cid, "📝 tldr (%d msgs):\n%s" % (len(chunk), s))]
        about = [x for x in chunk if who.lower() in x["text"].lower()]
        qs = [x for x in chunk if x["text"].rstrip().endswith("?")]
        lines = ["📝 tldr (%d msgs from %s):" % (len(chunk), ", ".join(sorted({x["who"] for x in chunk})))]
        if st.get("poll") and st["poll"]["status"] in ("open", "locking"):
            lines.append("- there's a plan being voted on, scroll up for the options")
        if about:
            lines.append("- you got mentioned: " + " · ".join("%s: \"%s\"" % (x["who"], x["text"][:60]) for x in about[-3:]))
        if qs:
            lines.append("- questions: " + " · ".join("%s: \"%s\"" % (x["who"], x["text"][:60]) for x in qs[-3:]))
        if len(lines) == 1:
            lines.append("- mostly chatting, last: %s: \"%s\"" % (chunk[-1]["who"], chunk[-1]["text"][:80]))
        return [self.say(cid, "\n".join(lines))]

    # ----- translation -----
    def cmd_translate(self, msg, st, who, text):
        cid = msg["chat_id"]
        t = re.sub(r"(?i)^translate\s*", "", text).strip()
        low = t.lower()
        settings = st.setdefault("settings", {})
        if low in ("on", "auto", "auto on"):
            settings["translate"] = True
            return [self.say(cid, "🌐 auto-translate on: messages in other languages get an English version")]
        if low in ("off", "auto off"):
            settings["translate"] = False
            return [self.say(cid, "🌐 auto-translate off")]
        if not (self.llm and self.llm.enabled):
            return [self.say(cid, "translation needs a language model key on the bot (LLM_API_KEY or XAI_API_KEY in .env) 🙏")]
        m = re.search(r"\s+(?:to|into|in)\s+([a-z]+)\s*$", t, re.I)
        target = m.group(1).lower() if m and m.group(1).lower() in LANGS else None
        if m and target:
            t = t[: m.start()].strip()
        if not target:
            target = (self.profiles.get(who, {}).get("prefs", {}).get("language") or "english").lower()
        if not t or t.lower() in ("last", "that", "this"):
            prev = [x for x in st.get("history", []) if x["who"] != who] or st.get("history", [])
            if not prev:
                return [self.say(cid, "nothing to translate yet")]
            src = prev[-1]
            out = self.llm.chat("Target language: %s\nText: %s" % (target, src["text"]), system=TRANSLATE_SYSTEM)
            return [self.say(cid, "🌐 %s (%s): %s" % (src["who"], target, out))] if out else [self.say(cid, "translation failed, try again?")]
        out = self.llm.chat("Target language: %s\nText: %s" % (target, t), system=TRANSLATE_SYSTEM)
        return [self.say(cid, "🌐 (%s) %s" % (target, out))] if out else [self.say(cid, "translation failed, try again?")]

    def auto_translate(self, msg, st, who, text):
        if not st.get("settings", {}).get("translate") or not (self.llm and self.llm.enabled) or not looks_non_english(text):
            return []
        out = self.llm.chat("Target language: english\nText: %s" % text, system=TRANSLATE_SYSTEM)
        if not out or out.strip().lower() == text.strip().lower():
            return []
        return [self.say(msg["chat_id"], "🌐 %s: %s" % (who, out))]

    # ----- study buddies (both must opt in) -----
    def cmd_study(self, msg, st, who, text, auto=False):
        cid = msg["chat_id"]
        names = [self.name_of(h) for h in msg["members"]]
        opted = [n for n in names if n in self.profiles and self.profiles[n].get("study_buddy")]
        courses = {}
        for n in opted:
            labels = [c.get("label", "") for c in self.profiles[n].get("classes", [])] + [e.get("label", "") for e in self.profiles[n].get("exams", [])]
            for lab in labels:
                for a, b in COURSE.findall(lab):
                    courses.setdefault("%s %s" % (a, b), set()).add(n)
        now = self.clock()
        out, done = [], set(st.setdefault("study_suggested", []))
        for course, people in sorted(courses.items()):
            if len(people) < 2:
                continue
            exams = []
            for n in people:
                for e in self.profiles[n].get("exams", []):
                    if course.replace(" ", "") in e.get("label", "").replace(" ", ""):
                        d = date.fromisoformat(e["date"])
                        if now.date() <= d <= now.date() + timedelta(days=10):
                            exams.append(d)
            key = "%s|%s" % (course, min(exams).isoformat() if exams else "")
            if auto and (not exams or key in done):
                continue
            agents = [PersonalAgent(self.profiles[n], self.tz) for n in sorted(people)]
            hp = HangoutPlanner(agents, [], self.tz)
            end = now + timedelta(days=3)
            if exams:
                end = min(end, datetime(min(exams).year, min(exams).month, min(exams).day, 8, 0, tzinfo=self.tz))
            opts = hp.plan(now + timedelta(hours=1), end, 120)
            opts = [o for o in opts if o["everyone"]]
            when = ""
            if opts:
                s, e = datetime.fromisoformat(opts[0]["start"]), datetime.fromisoformat(opts[0]["end"])
                when = " · free together %s @ Hayden Library?" % fmt_slot(s, e)
            ex = (" · exam %s" % (min(exams).strftime("%a %b ") + str(min(exams).day))) if exams else ""
            out.append(self.say(cid, "📚 study buddies: %s are both in %s%s%s" % (" + ".join(sorted(people)), course, ex, when)))
            st["study_suggested"].append(key)
        if not out and not auto:
            return [self.say(cid, "no matches yet. both people need to DM me \"squad study buddy on\" (that's what lets me mention a shared class)")]
        return out

    # ----- Phoenix heat / rain check for outdoor plans -----
    def forecast(self, when):
        """(temp_f, rain_pct) at `when`, from Open-Meteo (no key). Tests replace self.weather_fetch."""
        fetch = getattr(self, "weather_fetch", None)
        if fetch:
            return fetch(when)
        url = ("https://api.open-meteo.com/v1/forecast?latitude=33.4242&longitude=-111.9281&hourly=temperature_2m,"
               "precipitation_probability&temperature_unit=fahrenheit&timezone=America%2FPhoenix&forecast_days=7")
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                d = json.loads(r.read().decode())
            key = when.strftime("%Y-%m-%dT%H:00")
            i = d["hourly"]["time"].index(key)
            return d["hourly"]["temperature_2m"][i], d["hourly"]["precipitation_probability"][i]
        except Exception:  # noqa: BLE001
            return None

    def heat_check(self, cid, st, now):
        poll = st.get("poll")
        if not poll or poll.get("status") != "locked" or poll.get("weather_checked"):
            return []
        o = poll["options"][poll["winner"]]
        start = datetime.fromisoformat(o["start"])
        what = "%s %s" % (o["activity"]["what"], o["activity"].get("where", ""))
        if not OUTDOOR.search(what) or not (timedelta(0) < start - now <= timedelta(hours=30)):
            return []
        poll["weather_checked"] = True
        f = self.forecast(start)
        if not f:
            return []
        temp, rain = f
        if temp is not None and temp >= 100:
            later = None
            for h in range(1, 6):
                g = self.forecast(start + timedelta(hours=h))
                if g and g[0] is not None and g[0] < 95:
                    later = (start + timedelta(hours=h), g[0])
                    break
            tip = (" it's ~%d°F by %s, push it later?" % (later[1], later[0].strftime("%I%p").lstrip("0").lower())) if later else " maybe go indoors?"
            return [self.say(cid, "🥵 heads up: %d°F forecast for %s.%s say \"@squad plan %s evening\" to re-vote" % (
                temp, o["activity"]["what"], tip, start.strftime("%A").lower()))]
        if rain is not None and rain >= 60:
            return [self.say(cid, "🌧️ %d%% chance of rain for %s. backup plan indoors?" % (rain, o["activity"]["what"]))]
        return []

    def cmd_weather(self, msg, st, who, text):
        cid = msg["chat_id"]
        poll = st.get("poll")
        when = datetime.fromisoformat(poll["options"][poll["winner"]]["start"]) if poll and poll.get("winner") is not None else self.clock()
        f = self.forecast(when.replace(minute=0))
        if not f:
            return [self.say(cid, "couldn't get the forecast right now")]
        return [self.say(cid, "🌡️ %s: %d°F, %d%% rain chance%s" % (when.strftime("%a %I %p").replace(" 0", " "), f[0], f[1] or 0,
                                                              " 🥵 hydrate" if f[0] >= 100 else ""))]

    # ----- flake meter (only people who opt in) -----
    def cmd_flake(self, msg, st, who, text):
        cid = msg["chat_id"]
        fl = st.setdefault("flake", {"on": False, "people": {}})
        t = text.lower()
        if "on" in t.split() and "me" not in t.split():
            fl["on"] = True
            return [self.say(cid, "😈 flake meter on. only people who reply \"track me\" get counted")]
        if "off" in t.split():
            fl["on"] = False
            return [self.say(cid, "flake meter off 😇")]
        rows = sorted(fl["people"].items(), key=lambda kv: (kv[1]["shows"] / max(1, kv[1]["ins"]), -kv[1]["ins"]))
        if not rows:
            return [self.say(cid, "nobody's being tracked. reply \"track me\" if you dare")]
        lines = ["📊 flake meter:"]
        for n, s in rows:
            rate = s["shows"] / max(1, s["ins"])
            tag = "💀" if rate < 0.5 and s["ins"] >= 2 else ("🫡" if rate >= 0.9 else "")
            lines.append("- %s: %d in, %d showed %s" % (n, s["ins"], s["shows"], tag))
        return [self.say(cid, "\n".join(lines))]

    def ctx_flake(self, msg, st, who, low):
        fl = st.get("flake")
        cid = msg["chat_id"]
        if fl and fl.get("on") and re.fullmatch(r"track me[!. 😈]*", low):
            fl["people"].setdefault(who, {"ins": 0, "shows": 0})
            return [self.say(cid, "%s is now on the flake meter 😈" % who)]
        if fl and re.fullmatch(r"(untrack me|stop tracking me)[!.]*", low):
            fl["people"].pop(who, None)
            return [self.say(cid, "%s is off the flake meter" % who)]
        rc = st.get("rollcall")
        if rc and re.fullmatch(r"(here|i'?m here|made it|present|📍|🙋|🙋‍♂️|🙋‍♀️)[!.]*", low):
            if who not in rc["here"]:
                rc["here"].append(who)
            return []
        return None

    def tick_flake(self, cid, st, now):
        out = []
        poll, fl = st.get("poll"), st.get("flake")
        if poll and poll.get("status") == "locked" and not poll.get("rollcall_done"):
            start = datetime.fromisoformat(poll["options"][poll["winner"]]["start"])
            if fl and fl.get("on") and fl["people"] and start + timedelta(minutes=30) <= now < start + timedelta(hours=3) and not st.get("rollcall"):
                st["rollcall"] = {"until": (start + timedelta(hours=3)).isoformat(), "ins": poll["ins"], "here": []}
                out.append(self.say(cid, "📸 roll call! who made it? reply \"here\""))
        rc = st.get("rollcall")
        if rc and datetime.fromisoformat(rc["until"]) <= now:
            st.pop("rollcall")
            poll["rollcall_done"] = True
            res = []
            for n, s in fl["people"].items():
                if n in rc["ins"]:
                    s["ins"] += 1
                    if n in rc["here"]:
                        s["shows"] += 1
                    else:
                        res.append(n)
            if res:
                out.append(self.say(cid, "flake meter update: %s said \"in\" and... 👀" % ", ".join(res)))
        return out

    # ----- secret birthday planning (DMs everyone except the birthday person) -----
    def tick_birthdays(self, now):
        out = []
        if not (10 <= now.hour < 20):
            return out
        sent = self.state.setdefault("_bday", {})
        groups = {cid: st.get("members", []) for cid, st in self.state.items() if not cid.startswith("_")}
        for n, p in self.profiles.items():
            b = p.get("birthday")
            if not b:
                continue
            mo, d = int(b[:2]), int(b[3:5])
            nxt = date(now.year, mo, d)
            if nxt < now.date():
                nxt = date(now.year + 1, mo, d)
            days = (nxt - now.date()).days
            key = "%s-%d" % (n, nxt.year)
            if days == 7 and key not in sent:
                sent[key] = {"ideas": [], "chip": {}}
                friends = set()
                for members in groups.values():
                    names = [self.name_of(h) for h in members]
                    if n in names:
                        friends |= {(x, h) for x, h in zip(names, members) if x and x != n}
                for x, h in sorted(friends):
                    out.append({"dm": h, "text": self.prefix + "🤫 %s's birthday is next %s (%s %d). planning something? reply \"squad gift <idea>\" or \"squad chip in 10\"" % (
                        n, nxt.strftime("%A"), nxt.strftime("%b"), nxt.day)})
            if days == 2 and key in sent and not sent[key].get("summary"):
                sent[key]["summary"] = True
                ideas = sent[key]["ideas"]
                total = sum(sent[key]["chip"].values())
                for members in groups.values():
                    names = [self.name_of(h) for h in members]
                    for x, h in zip(names, members):
                        if x and x != n and n in names:
                            out.append({"dm": h, "text": self.prefix + "🎂 %s's bday in 2 days. ideas: %s · chip-ins: $%d from %d people" % (
                                n, "; ".join("%s (%s)" % (i["idea"], i["by"]) for i in ideas) or "none yet", total, len(sent[key]["chip"]))})
        return out

    def bday_dm(self, who, low, raw):
        sent = self.state.get("_bday", {})
        open_ = [k for k, v in sent.items() if not k.startswith(who + "-") and not v.get("closed")]
        if not open_:
            return None
        key = sorted(open_)[-1]
        m = re.match(r"^gift\s+(.+)", raw, re.I)
        if m:
            sent[key]["ideas"].append({"by": who, "idea": m.group(1)[:80]})
            return "noted 🎁 i'll share all the ideas 2 days before"
        m = re.match(r"^chip in\s*\$?(\d+)", low)
        if m:
            sent[key]["chip"][who] = int(m.group(1))
            return "noted 💸 $%s from you" % m.group(1)
        return None

    # ----- this or that -----
    def cmd_tot(self, msg, st, who, text):
        cid = msg["chat_id"]
        t = text.lower()
        s = st.setdefault("settings", {})
        if re.search(r"\bon\b", t):
            s["tot"] = True
            return [self.say(cid, "🎲 weekly this-or-that on (Sundays 7pm)")] + self.post_tot(cid, st)
        if re.search(r"\boff\b", t):
            s["tot"] = False
            return [self.say(cid, "this-or-that off")]
        return self.post_tot(cid, st)

    def post_tot(self, cid, st):
        a, b = None, None
        if self.llm and self.llm.enabled:
            got = self.llm.json("Give one fun 'this or that' question for ASU college students.", '{"a": short option, "b": short option} JSON only.') or {}
            a, b = got.get("a"), got.get("b")
        if not (a and b):
            used = st.setdefault("tot_used", [])
            fresh = [p for p in TOT if p[0] not in used] or TOT
            a, b = random.Random(len(used) + 3).choice(fresh)
            used.append(a)
        st["tot"] = {"a": a, "b": b, "votes": {}, "until": (self.clock() + timedelta(hours=24)).isoformat()}
        return [self.say(cid, "🎲 this or that: A) %s  or  B) %s ?\nreply A or B" % (a, b))]

    def ctx_tot(self, msg, st, who, low):
        t = st.get("tot")
        if not t or not re.fullmatch(r"[ab][!. ]*", low):
            return None
        t["votes"][who] = low[0]
        return []

    def tick_tot(self, cid, st, now):
        out = []
        t = st.get("tot")
        if t and datetime.fromisoformat(t["until"]) <= now:
            st.pop("tot")
            a = sum(1 for v in t["votes"].values() if v == "a")
            b = sum(1 for v in t["votes"].values() if v == "b")
            if a + b:
                win = t["a"] if a > b else t["b"] if b > a else None
                out.append(self.say(cid, "🎲 results: %s %d · %s %d%s" % (t["a"], a, t["b"], b, (" → %s gang wins" % win) if win else " → tie, the group is split 😭")))
        s = st.get("settings", {})
        if s.get("tot") and now.weekday() == 6 and now.hour == 19 and st.get("tot_week") != now.strftime("%G-%V") and not st.get("tot"):
            st["tot_week"] = now.strftime("%G-%V")
            out += self.post_tot(cid, st)
        return out

    # ----- roommate chores and groceries -----
    def cmd_chores(self, msg, st, who, text):
        cid = msg["chat_id"]
        names = [n for n in (self.name_of(h) for h in msg["members"]) if n]
        t = re.sub(r"(?i)^chores?\s*:?\s*", "", text).strip()
        c = st.get("chores")
        if t:
            jobs = [j.strip() for j in re.split(r",|\n", t) if j.strip()]
            c = st["chores"] = {"jobs": jobs, "people": sorted(names), "start": self.clock().strftime("%G-%V"), "done": {}}
        if not c:
            return [self.say(cid, "set them up: \"@squad chores dishes, trash, bathroom\"")]
        return [self.say(cid, self.chore_board(c, self.clock()))]

    def chore_week(self, c, now):
        y0, w0 = map(int, c["start"].split("-"))
        y1, w1 = map(int, now.strftime("%G-%V").split("-"))
        return (y1 - y0) * 52 + (w1 - w0)

    def chore_assign(self, c, now):
        k = self.chore_week(c, now)
        ppl = c["people"]
        return {job: ppl[(i + k) % len(ppl)] for i, job in enumerate(c["jobs"])} if ppl else {}

    def chore_board(self, c, now):
        wk = now.strftime("%G-%V")
        done = c["done"].get(wk, [])
        rows = ["%s %s: %s" % ("✅" if j in done else "🧽", j, p) for j, p in self.chore_assign(c, now).items()]
        return "this week's chores:\n" + "\n".join(rows) + "\nreply \"done <chore>\" when it's done"

    def ctx_chore_done(self, msg, st, who, low):
        c = st.get("chores")
        m = re.match(r"^(?:done|did|finished)\s+(?:the\s+)?(.+?)[!.]*$", low)
        if not c or not m:
            return None
        job = next((j for j in c["jobs"] if j.lower() in m.group(1) or m.group(1) in j.lower()), None)
        if not job:
            return None
        pr = st.get("project")
        if pr and any(m.group(1) in t["name"].lower() or t["name"].lower() in m.group(1) for t in pr.get("tasks", [])):
            return None  # it's a project part, let project mode handle it
        wk = self.clock().strftime("%G-%V")
        c["done"].setdefault(wk, [])
        if job not in c["done"][wk]:
            c["done"][wk].append(job)
        return [self.say(msg["chat_id"], "%s ✅ thanks %s 🫶" % (job, who))]

    def tick_chores(self, cid, st, now):
        c = st.get("chores")
        if not c or not (now.weekday() == 6 and now.hour == 18) or st.get("chore_posted") == now.strftime("%G-%V"):
            return []
        st["chore_posted"] = now.strftime("%G-%V")
        last = now - timedelta(days=7)
        missed = [j for j, p in self.chore_assign(c, last).items() if j not in c["done"].get(last.strftime("%G-%V"), [])]
        nxt = now + timedelta(days=1)
        msg = "new week 🧹\n" + self.chore_board(c, nxt)
        if missed:
            msg += "\n(last week still open: %s)" % ", ".join(missed)
        return [self.say(cid, msg)]

    def cmd_grocery(self, msg, st, who, text):
        cid = msg["chat_id"]
        g = st.setdefault("groceries", [])
        t = re.sub(r"(?i)^(add|need|grocery|groceries|buy)\s*:?\s*", "", text).strip()
        if t and not re.fullmatch(r"(?i)(list|groceries|grocery list)", t):
            for item in [x.strip() for x in re.split(r",|\band\b|\n", t) if x.strip()]:
                if item.lower() not in [i["item"].lower() for i in g]:
                    g.append({"item": item, "by": who})
        if not g:
            return [self.say(cid, "grocery list is empty 🛒 \"@squad add oat milk, paper towels\"")]
        return [self.say(cid, "🛒 list: " + ", ".join(i["item"] for i in g) + "\nreply \"got <item>\" when you buy it")]

    def ctx_got(self, msg, st, who, low):
        g = st.get("groceries")
        m = re.match(r"^(?:got|bought|picked up)\s+(?:the\s+)?(.+?)[!.]*$", low)
        if not g or not m:
            return None
        hit = [i for i in g if i["item"].lower() in m.group(1) or m.group(1) in i["item"].lower()]
        if not hit:
            return None
        for i in hit:
            g.remove(i)
        return [self.say(msg["chat_id"], "✅ %s (thanks %s)%s" % (", ".join(i["item"] for i in hit), who, (" · still need: " + ", ".join(i["item"] for i in g)) if g else " · list's empty"))]
