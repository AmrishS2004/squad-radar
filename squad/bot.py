"""Squad Radar: the group-chat bot. Transport-agnostic: give it messages, it returns what to send."""
import json
import os
import random
import re
from datetime import datetime, timedelta

from .agents import PersonalAgent
from .features import HELP, FeaturesMixin
from .ics import make_ics
from .llm import CHAT_SYSTEM, INTENT_SYSTEM, TASKS_SYSTEM
from .planner import FALLBACK_IDEAS, HangoutPlanner
from .project import GENERIC_TASKS, allocate, parse_tasks
from .timeparse import duration_minutes, fmt_slot, window

TRIGGER = re.compile(r"^\s*(?:@?squad(?:\s*radar)?|hey\s+squad)\b[\s,:!-]*", re.I)
PASSIVE = re.compile(r"\b(anyone free|any(?:one|body) down|when (?:is|are) (?:every(?:one|body)|y'?all|we|you guys) free|"
                     r"let'?s hang|wanna hang|we should hang)\b", re.I)
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

CELEBRATE = ["let's gooo", "W", "absolute machine", "the group carry fr", "clean"]


def bar(done, total, width=10):
    n = 0 if not total else round(width * done / total)
    return "▓" * n + "░" * (width - n)


class SquadBot(FeaturesMixin):
    def __init__(self, profiles, events, tz, clock, state_path=None, llm=None, owner="me", outdir="out", prefix=""):
        self.tz = tz
        self.clock = clock
        self.events = events
        self.llm = llm
        self.owner = owner
        self.outdir = outdir
        self.prefix = prefix
        self.profiles = profiles  # name -> profile dict
        self.state_path = state_path
        self.state = {}
        if state_path and os.path.exists(state_path):
            with open(state_path, encoding="utf-8") as fh:
                self.state = json.load(fh)
        self.last_passive = {}

    # ---------- plumbing ----------
    def save(self):
        if self.state_path:
            os.makedirs(os.path.dirname(os.path.abspath(self.state_path)), exist_ok=True)
            with open(self.state_path + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(self.state, fh, indent=1)
            os.replace(self.state_path + ".tmp", self.state_path)

    def chat_state(self, chat_id):
        return self.state.setdefault(chat_id, {"poll": None, "project": None, "reminders": [], "why": []})

    def say(self, chat_id, text):
        return {"chat_id": chat_id, "text": self.prefix + text}

    def name_of(self, handle):
        h = (handle or "").lower()
        if h == "me":
            for n, p in self.profiles.items():
                if "me" in [x.lower() for x in p.get("handles", [])]:
                    return n
            return self.owner
        for n, p in self.profiles.items():
            if h in [x.lower() for x in p.get("handles", [])]:
                return n
        return None

    def agents_for(self, members):
        agents, unknown = [], []
        for h in members:
            n = self.name_of(h)
            if n and n in self.profiles:
                agents.append(PersonalAgent(self.profiles[n], self.tz))
            else:
                unknown.append(h)
        return agents, unknown

    # ---------- entry points ----------
    def on_message(self, msg):
        """msg: chat_id, text, sender ('me' or a handle), members (handles), is_group."""
        if msg.get("kind") == "reaction":
            return self.on_reaction(msg) if msg.get("is_group") else []
        text = (msg.get("text") or "").strip()
        if not text:
            return []
        if self.prefix and text.startswith(self.prefix.strip()):
            return []  # our own message echoed back
        cid = msg["chat_id"]
        st = self.chat_state(cid)
        who = self.name_of(msg["sender"]) or msg["sender"]
        m = TRIGGER.match(text)
        body = text[m.end():].strip() if m else text
        low = body.lower()
        out = []

        if not msg.get("is_group"):
            if m:
                reply = self.bday_dm(who, low, body)
                out = [self.say(cid, reply)] if reply else self.setup_dm(msg, body)
            self.save()
            return out

        st["members"] = msg.get("members", [])
        extra = [] if m else self.welcome_back(msg, st, who)
        if not m:
            self.remember(st, who, text, msg.get("guid"))
        extra += [] if m else self.auto_translate(msg, st, who, text)
        ctx = None if m else self.contextual(msg, st, who, text, low)
        if ctx is not None:
            self.save()
            return ctx + extra

        # contextual replies that don't need @squad
        if st.get("poll") and st["poll"]["status"] == "open" and re.fullmatch(r"(?:option\s*)?([12])|neither|none", low):
            out = self.vote(msg, st, who, low)
        elif st.get("poll") and st["poll"]["status"] == "locking" and re.fullmatch(r"(?:i'?m\s+)?(?:in|out|down)[\s!.👍🙋‍♂️🙋‍♀️]*|count me (?:in|out)[!.]*|can'?t make it[!.]*", low):
            out = self.rsvp(msg, st, who, low)
        elif re.match(r"^(dibs|i call dibs)( on)?\s+", low) and st.get("project"):
            out = self.dibs(msg, st, who, re.sub(r"^(dibs|i call dibs)( on)?\s+", "", low))
        elif re.match(r"^(done|finished|i finished)\s+", low) and st.get("project"):
            out = self.done(msg, st, who, re.sub(r"^(done|finished|i finished)( with)?\s+", "", low))
        elif re.fullmatch(r"(ok|okay|👍|bet|sounds good|works for me)[.!]*", low) and st.get("project") and st["project"].get("assign"):
            out = self.accept(msg, st, who)
        elif m:
            out = self.command(msg, st, who, body)
        elif not m and (pas := self.passive(msg, st, who, text)):
            out = pas
        elif PASSIVE.search(text):
            last = self.last_passive.get(cid)
            now = self.clock()
            if not last or now - last > timedelta(hours=1):
                self.last_passive[cid] = now
                out = self.plan(msg, st, text)
        self.save()
        return out + extra

    def command(self, msg, st, who, body):
        low = body.lower()
        cid = msg["chat_id"]
        if low in ("", "help", "what can you do", "?", "commands"):
            return [self.say(cid, HELP)]
        if re.fullmatch(r"(hi+|hey+|hello+|yo+|sup|what'?s up|gm|good (morning|afternoon|evening)|hola|namaste|vanakkam)[\s!.👋]*", low):
            return [self.say(cid, "hey %s 👋 what do you need? ask me anything about the chat, or \"@squad help\" for everything i can do" % (who or "there").split()[0])]
        if re.fullmatch(r"(thanks|thank you|thx|ty|tysm|appreciate it)[\s!.🙏]*", low):
            return [self.say(cid, "anytime 🫡")]
        r = self.feature_command(msg, st, who, body)
        if r is not None:
            return r
        if re.match(r"^(plan|free|hang|when|find a time|schedule)\b", low) or PASSIVE.search(low) or "free" in low:
            return self.plan(msg, st, body)
        if low.startswith("decide") or "where should we eat" in low or "what should we eat" in low or low.startswith("pick"):
            return self.decide(msg, st, re.sub(r"^(decide|pick)( between| for us)?:?\s*", "", body, flags=re.I))
        if low.startswith("why"):
            return [self.say(cid, "what each agent shared last time:\n" + "\n".join("- " + l for l in st.get("why") or ["nothing yet"]))]
        if low.startswith("project"):
            return self.new_project(msg, st, body)
        if low.startswith("tasks") or low.startswith("parts"):
            return self.set_tasks(msg, st, re.sub(r"^(tasks|parts):?\s*", "", body, flags=re.I))
        if low.startswith("suggest"):
            return self.suggest(msg, st)
        if low.startswith("split") or low.startswith("assign"):
            return self.split(msg, st)
        if low.startswith("status") or low.startswith("progress"):
            return self.status(msg, st)
        if low.startswith("receipts"):
            return self.receipts(msg, st)
        if low.startswith("dibs"):
            return self.dibs(msg, st, who, re.sub(r"^dibs( on)?\s*", "", low))
        if low.startswith("done"):
            return self.done(msg, st, who, re.sub(r"^done( with)?\s*", "", low))
        # free-form: let Grok classify, else small talk, else help
        if self.llm and self.llm.enabled:
            intent = self.llm.json("Message: " + body, INTENT_SYSTEM) or {}
            it = intent.get("intent")
            if it == "plan":
                return self.plan(msg, st, intent.get("when") or body)
            if it == "decide":
                return self.decide(msg, st, ", ".join(intent.get("options") or []))
            if it in ("split", "status", "receipts", "suggest", "why", "help"):
                return self.command(msg, st, who, it)
            mapped = {"market": "market", "owes": "owes", "tldr": "tldr", "highlights": "highlights", "mentions": "mentions",
                      "stats": "stats", "pins": "pins", "groceries": "list"}.get(it)
            if mapped:
                r = self.feature_command(msg, st, who, mapped)
                if r is not None:
                    return r
            reply = self.llm.chat("%s\n\n%s asks: %s" % (self.context_for(st, who), who, body), system=CHAT_SYSTEM, max_tokens=220)
            if reply:
                return [self.say(cid, reply)]
        return [self.say(cid, "not sure what you mean 😅 try \"@squad help\"")]

    # ---------- hangouts ----------
    def plan(self, msg, st, text):
        cid = msg["chat_id"]
        agents, unknown = self.agents_for(msg["members"])
        if len(agents) < 2:
            return [self.say(cid, "i only know %d person's schedule here. everyone DM me \"squad setup\" first 🙏" % len(agents))]
        now = self.clock()
        s, e, label = window(text, now)
        mins = duration_minutes(text)
        hp = HangoutPlanner(agents, self.events, self.tz)
        opts = hp.plan(s, e, mins, hint=text)
        st["why"] = hp.log
        if not opts:
            return [self.say(cid, "couldn't find any open time %s 😭 try another day?" % label)]
        st["poll"] = {"options": opts, "votes": {}, "status": "open", "ins": [], "outs": [],
                      "members": [a.name for a in agents], "created": now.isoformat()}
        lines = ["ok i checked everyone's classes for %s 👀" % label]
        for i, o in enumerate(opts, 1):
            so, eo = datetime.fromisoformat(o["start"]), datetime.fromisoformat(o["end"])
            act = o["activity"]
            price = "free" if not act["price"] else "~$%d" % act["price"]
            extra = " 🍕 free food" if act.get("free_food") else ""
            where = (" @ " + act["where"]) if act.get("where") else ""
            lines.append("%d. %s: %s%s (%s)%s" % (i, fmt_slot(so, eo), act["what"], where, price, extra))
            if o["missing"]:
                lines.append("   ⚠️ can't make it: " + ", ".join("%s (%s)" % m for m in o["missing"]))
            for n in o["notes"]:
                lines.append("   · " + n)
        if getattr(hp, "worked_around", None):
            lines.append("worked around: " + " · ".join(w for _, w in hp.worked_around[:4]))
        if unknown:
            lines.append("(don't have schedules for %d people yet, DM me \"squad setup\")" % len(unknown))
        lines.append("reply 1 or 2 🗳️")
        return [self.say(cid, "\n".join(lines))]

    def vote(self, msg, st, who, low):
        cid, poll = msg["chat_id"], st["poll"]
        m = re.search(r"[12]", low)
        poll["votes"][who] = int(m.group(0)) - 1 if m else -1
        need = len(poll["members"]) // 2 + 1
        counts = [sum(1 for v in poll["votes"].values() if v == i) for i in range(len(poll["options"]))]
        best = max(range(len(counts)), key=lambda i: counts[i]) if counts else 0
        all_voted = len(poll["votes"]) >= len(poll["members"])
        if counts and (counts[best] >= need or all_voted):
            if counts[best] == 0:
                poll["status"] = "closed"
                return [self.say(cid, "nobody liked either 😭 say \"@squad plan\" with another day")]
            poll["status"] = "locking"
            poll["winner"] = best
            o = poll["options"][best]
            so, eo = datetime.fromisoformat(o["start"]), datetime.fromisoformat(o["end"])
            return [self.say(cid, "option %d wins 🎉 %s, %s\nreply \"in\" to lock it and i'll send the invite" % (
                best + 1, fmt_slot(so, eo), o["activity"]["what"]))]
        return []

    def rsvp(self, msg, st, who, low):
        cid, poll = msg["chat_id"], st["poll"]
        going = bool(re.match(r"(?:i'?m\s+)?(?:in|down)\b|count me in", low))
        ins, outs = set(poll["ins"]), set(poll["outs"])
        if going:
            ins.add(who); outs.discard(who)
        else:
            outs.add(who); ins.discard(who)
        poll["ins"], poll["outs"] = sorted(ins), sorted(outs)
        need = len(poll["members"]) // 2 + 1
        if len(set(poll["ins"])) >= need:
            poll["status"] = "locked"
            self.log_event(st, "plan")
            o = poll["options"][poll["winner"]]
            so, eo = datetime.fromisoformat(o["start"]), datetime.fromisoformat(o["end"])
            act = o["activity"]
            path = make_ics(os.path.join(self.outdir, "squad-%s.ics" % so.strftime("%Y%m%d-%H%M")),
                            act["what"], so, eo, act.get("where", ""), "Planned by Squad Radar")
            st["reminders"].append({"at": (so - timedelta(hours=2)).isoformat(), "chat_id": cid, "sent": False,
                                    "text": "2 hours till %s%s 🔔 %s" % (act["what"], (" @ " + act["where"]) if act.get("where") else "",
                                                                      ", ".join(sorted(set(poll["ins"]))))})
            return [self.say(cid, "locked ✅ %s. tap the invite to add it to your calendar 👇 i'll remind you 2h before" % ", ".join(sorted(set(poll["ins"])))),
                    {"chat_id": cid, "file": path}]
        if not going:
            return [self.say(cid, "no worries %s 🫡" % who)]
        return []

    def decide(self, msg, st, text):
        cid = msg["chat_id"]
        opts = [o.strip(" .!?") for o in re.split(r",|\bor\b|/", text) if o.strip(" .!?")]
        agents, _ = self.agents_for(msg["members"])
        if len(opts) < 2:
            hp = HangoutPlanner(agents, [], self.tz)
            h = self.clock().hour
            opts = [i[2] for i in FALLBACK_IDEAS if i[0] <= h < i[1] and hp.group_budget_ok(i[3]) and hp.group_diet_ok(i[4])] \
                or [i[2] for i in FALLBACK_IDEAS]
        pick = random.Random(self.clock().toordinal() * 7 + len(text)).choice(opts)
        quip = None
        if self.llm and self.llm.enabled:
            quip = self.llm.chat("The group is choosing between: %s. You picked %s. Hype the pick in one short funny line." % (", ".join(opts), pick))
        quip = quip or random.Random(len(opts) + len(pick)).choice(["no further questions", "it's decided, no take-backs",
                                                                       "the dice don't lie", "case closed"])
        return [self.say(cid, "the squad has spoken: %s 🎲 %s" % (pick, quip))]

    # ---------- group projects ----------
    def parse_due(self, text):
        now = self.clock()
        t = text.lower()
        m = re.search(r"(\d{1,2})/(\d{1,2})", t)
        if m:
            mo, d = int(m.group(1)), int(m.group(2))
        else:
            m = re.search(r"\b(%s)[a-z]*\.?\s+(\d{1,2})" % "|".join(MONTHS), t)
            if m:
                mo, d = MONTHS[m.group(1)], int(m.group(2))
            else:
                s, e, label = window(t, now)
                if label in ("the next few days",):
                    return now + timedelta(days=7)
                return e.replace(hour=23, minute=59)
        y = now.year + (1 if mo < now.month - 1 else 0)
        return datetime(y, mo, d, 23, 59, tzinfo=self.tz)

    def new_project(self, msg, st, body):
        cid = msg["chat_id"]
        rest = re.sub(r"^project:?\s*", "", body, flags=re.I)
        parts = re.split(r"\bdue\b", rest, flags=re.I)
        name = parts[0].strip(" ,-") or "group project"
        due = self.parse_due(parts[1]) if len(parts) > 1 else self.clock() + timedelta(days=7)
        st["project"] = {"name": name, "due": due.isoformat(), "tasks": [], "assign": {}, "deadlines": {},
                         "accepted": [], "done": {}, "dibs": {}, "nudged": [], "created": self.clock().isoformat()}
        return [self.say(cid, "group project mode on 📚 %s, due %s\nsend the parts: \"@squad tasks intro 2h, research 3h, ...\" or \"@squad suggest\"" % (
            name, due.strftime("%a %b ") + str(due.day)))]

    def set_tasks(self, msg, st, text):
        cid = msg["chat_id"]
        if not st.get("project"):
            return [self.say(cid, "start one first: \"@squad project <name> due <date>\"")]
        tasks = parse_tasks(text)
        if not tasks:
            return [self.say(cid, "send them like: intro 2h, research 3h, slides 3h")]
        st["project"]["tasks"] = tasks
        total = sum(t["hours"] for t in tasks)
        return [self.say(cid, "got %d parts (~%.0fh total). call dibs now (\"dibs research\") or say \"@squad split\"" % (len(tasks), total))]

    def suggest(self, msg, st):
        cid = msg["chat_id"]
        if not st.get("project"):
            return [self.say(cid, "start one first: \"@squad project <name> due <date>\"")]
        tasks = None
        if self.llm and self.llm.enabled:
            got = self.llm.json("Project: %s" % st["project"]["name"], TASKS_SYSTEM) or {}
            tasks = [t for t in got.get("tasks", []) if isinstance(t, dict) and t.get("name")]
            for t in tasks:
                try:
                    t["hours"] = max(0.5, min(12, float(t.get("hours", 2))))
                except (TypeError, ValueError):
                    t["hours"] = 2.0
        tasks = tasks or [dict(t) for t in GENERIC_TASKS]
        st["project"]["tasks"] = tasks
        lines = ["here's a split for %s:" % st["project"]["name"]] + ["- %s (~%sh)" % (t["name"], ("%g" % t["hours"])) for t in tasks]
        lines.append("edit with \"@squad tasks ...\", call dibs, or say \"@squad split\"")
        return [self.say(cid, "\n".join(lines))]

    def dibs(self, msg, st, who, task_text):
        cid, pr = msg["chat_id"], st.get("project")
        if not pr or not pr.get("tasks"):
            return [self.say(cid, "no parts yet, send \"@squad tasks ...\" first")]
        t = self.match_task(pr, task_text)
        if not t:
            return [self.say(cid, "which part? " + ", ".join(x["name"] for x in pr["tasks"]))]
        if pr.get("assign"):
            owner = pr["assign"].get(t["name"])
            if owner == who:
                return [self.say(cid, "it's already yours 😄")]
            pr["swap"] = {"task": t["name"], "from": owner, "to": who}
            return [self.say(cid, "%s wants '%s' 👀 %s, ok to swap? reply \"ok\" and it's theirs" % (who, t["name"], owner))]
        claims = pr["dibs"].setdefault(t["name"].lower(), [])
        if who in [c[1] for c in claims]:
            return []
        claims.append([self.clock().isoformat(), who])
        if len(claims) > 1:
            return [self.say(cid, "%s already called '%s' 👀 first dibs wins, i'll find you something else %s" % (claims[0][1], t["name"], who))]
        return [self.say(cid, "%s → %s ✍️" % (who, t["name"]))]

    def split(self, msg, st):
        cid, pr = msg["chat_id"], st.get("project")
        if not pr or not pr.get("tasks"):
            return [self.say(cid, "need parts first: \"@squad tasks ...\" or \"@squad suggest\"")]
        agents, unknown = self.agents_for(msg["members"])
        if not agents:
            return [self.say(cid, "nobody's set up yet, DM me \"squad setup\"")]
        now = self.clock()
        due = datetime.fromisoformat(pr["due"])
        dibs = {k: [(c[0], c[1]) for c in v] for k, v in pr["dibs"].items()}
        assign, deadlines, notes, overload = allocate(pr["tasks"], agents, due, now, dibs)
        pr["assign"], pr["deadlines"], pr["accepted"] = assign, deadlines, []
        st["why"] = ["%s's agent shared: ~%.0fh free this week, skills %s%s" % (
            a.name, a.hours, ", ".join(a.skills) or "none listed", "" if a.share else " (reasons private)") for a in agents] + notes
        lines = ["here's the split 📋 (internal deadlines leave a buffer before %s)" % (due.strftime("%a %b ") + str(due.day))]
        by_person = {}
        for task, person in assign.items():
            by_person.setdefault(person, []).append(task)
        for person, ts in by_person.items():
            dl = min(datetime.fromisoformat(deadlines[t]) for t in ts)
            lines.append("- %s: %s (by %s)" % (person, ", ".join(ts), dl.strftime("%a %b ") + str(dl.day)))
        lines += ["· " + n for n in notes]
        lines.append("everyone reply \"ok\" to accept your part 👍 (or \"dibs <part>\" to ask for a swap)")
        return [self.say(cid, "\n".join(lines))]

    def accept(self, msg, st, who):
        cid, pr = msg["chat_id"], st["project"]
        sw = pr.get("swap")
        if sw and who == sw["from"]:
            pr["assign"][sw["task"]] = sw["to"]
            pr["swap"] = None
            return [self.say(cid, "swapped 🔁 '%s' is %s's now" % (sw["task"], sw["to"]))]
        if who not in pr["accepted"] and who in pr["assign"].values():
            pr["accepted"].append(who)
        people = sorted(set(pr["assign"].values()))
        if set(pr["accepted"]) >= set(people):
            return [self.say(cid, "everyone's locked in 🔒 i'll check in before each internal deadline")]
        return []

    def match_task(self, pr, text):
        text = text.lower().strip(" .!")
        best, score = None, 0
        for t in pr["tasks"]:
            n = t["name"].lower()
            if text == n or text in n or n in text:
                s = 10 + len(n)
            else:
                s = len(set(text.split()) & set(n.split()))
            if s > score:
                best, score = t, s
        return best

    def done(self, msg, st, who, text):
        cid, pr = msg["chat_id"], st.get("project")
        t = self.match_task(pr, text) if pr and pr.get("tasks") else None
        if not t:
            return [self.say(cid, "which part? " + ", ".join(x["name"] for x in (pr or {}).get("tasks", [])))]
        if t["name"] in pr["done"]:
            return [self.say(cid, "'%s' was already done ✅" % t["name"])]
        pr["done"][t["name"]] = {"by": who, "at": self.clock().isoformat()}
        n, total = len(pr["done"]), len(pr["tasks"])
        dl = pr["deadlines"].get(t["name"])
        early = ""
        if dl:
            days = (datetime.fromisoformat(dl).date() - self.clock().date()).days
            if days >= 1:
                early = ", %d day%s early" % (days, "" if days == 1 else "s")
        msg_ = "%s ✅ by %s%s. %s\n%s %d/%d" % (t["name"], who, early, random.Random(n).choice(CELEBRATE), bar(n, total), n, total)
        if n == total:
            msg_ += "\nthat's everything 🎉 \"@squad receipts\" for the peer eval record"
        return [self.say(cid, msg_)]

    def status(self, msg, st):
        cid, pr = msg["chat_id"], st.get("project")
        if not pr:
            return [self.say(cid, "no project in this chat. \"@squad project <name> due <date>\"")]
        lines = ["%s %s %d/%d" % (pr["name"], bar(len(pr["done"]), len(pr["tasks"])), len(pr["done"]), len(pr["tasks"]))]
        for t in pr["tasks"]:
            who = pr["assign"].get(t["name"], "unassigned")
            mark = "✅" if t["name"] in pr["done"] else "⏳"
            lines.append("%s %s (%s)" % (mark, t["name"], who))
        return [self.say(cid, "\n".join(lines))]

    def receipts(self, msg, st):
        cid, pr = msg["chat_id"], st.get("project")
        if not pr:
            return [self.say(cid, "no project here yet")]
        lines = ["🧾 receipts for %s" % pr["name"]]
        people = sorted(set(pr["assign"].values()) | {d["by"] for d in pr["done"].values()})
        for p in people:
            mine = [t for t, d in pr["done"].items() if d["by"] == p]
            hours = sum(t["hours"] for t in pr["tasks"] if t["name"] in mine)
            open_ = [t for t, w in pr["assign"].items() if w == p and t not in pr["done"]]
            when = ", ".join("%s (%s)" % (t, datetime.fromisoformat(pr["done"][t]["at"]).strftime("%b ") + str(datetime.fromisoformat(pr["done"][t]["at"]).day)) for t in mine)
            lines.append("- %s: %s%s%s" % (p, when or "nothing marked done yet", " · ~%gh" % hours if hours else "",
                                            (" · open: " + ", ".join(open_)) if open_ else ""))
        lines.append("(only what people marked done in this chat, with dates)")
        return [self.say(cid, "\n".join(lines))]

    # ---------- private setup by DM ----------
    def setup_dm(self, msg, body):
        cid = msg["chat_id"]
        handle = msg["sender"].lower()
        name = self.name_of(handle)
        low = body.lower()
        m = re.match(r"^(?:setup|i'?m|i am|my name is)\s+([a-z][a-z'\- ]{0,30})$", low)
        if low == "setup" or (m and not name):
            if m and low != "setup":
                name = m.group(1).strip().title()
                self.profiles[name] = {"name": name, "handles": [handle], "share_reasons": False, "classes": [], "busy": [],
                                       "exams": [], "prefs": {}, "project": {}}
                self.save_profile(name)
                return [self.say(cid, "hey %s 👋 this stays between us. send:\n- classes MW 10:30-11:45 CSE 310\n- exam nov 2 9am BIO midterm\n- budget 15\n- vegetarian\n- hours 6 (free for projects this week)\n- skills research, writing\n- share reasons on/off (off = friends only see \"busy\")" % name)]
            return [self.say(cid, "what should i call you? \"squad i'm <name>\"")]
        if not name:
            return [self.say(cid, "first tell me your name: \"squad i'm <name>\"")]
        p = self.profiles[name]
        changed = []
        for line in re.split(r"[\n;]+", body):
            l = line.strip()
            ll = l.lower()
            extra_lab = self.setup_extra(p, l, ll)
            if extra_lab:
                changed.append(extra_lab)
                continue
            mc = re.match(r"^(?:classes?\s+)?([mtwrfsu]{1,5}|[mtwfsu]+th?|tth|mwf|mw)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*-\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*(.*)$", l, re.I)
            if ll.startswith("class") or mc:
                if mc:
                    p.setdefault("classes", []).append({"days": mc.group(1), "start": mc.group(2), "end": mc.group(3), "label": mc.group(4) or "class"})
                    changed.append("class")
                continue
            me = re.match(r"^exam\s+([a-z]{3})[a-z]*\.?\s+(\d{1,2})\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*(.*)$", ll)
            if me:
                y = self.clock().year
                d = "%d-%02d-%02d" % (y, MONTHS[me.group(1)], int(me.group(2)))
                p.setdefault("exams", []).append({"date": d, "time": me.group(3), "label": me.group(4) or "exam"})
                changed.append("exam")
                continue
            mb = re.match(r"^budget\s*\$?(\d+)", ll)
            if mb:
                p.setdefault("prefs", {})["budget"] = int(mb.group(1)); changed.append("budget"); continue
            if ll in ("vegetarian", "vegan", "halal", "kosher"):
                p.setdefault("prefs", {}).setdefault("diet", []).append(ll); changed.append("diet"); continue
            mh = re.match(r"^hours\s+(\d+(?:\.\d+)?)", ll)
            if mh:
                p.setdefault("project", {})["hours_this_week"] = float(mh.group(1)); changed.append("hours"); continue
            if ll.startswith("skills"):
                p.setdefault("project", {})["skills"] = [s.strip() for s in ll[6:].strip(" :").split(",") if s.strip()]; changed.append("skills"); continue
            ms = re.match(r"^share reasons\s+(on|off|yes|no)", ll)
            if ms:
                p["share_reasons"] = ms.group(1) in ("on", "yes"); changed.append("privacy"); continue
        if not changed:
            return [self.say(cid, "didn't catch that 😅 try \"classes MW 10:30-11:45 CSE 310\" or \"budget 15\"")]
        self.save_profile(name)
        return [self.say(cid, "saved (%s) 🔒 only free/busy ever leaves this chat" % ", ".join(sorted(set(changed))))]

    def save_profile(self, name):
        d = getattr(self, "profile_dir", None)
        if d:
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, re.sub(r"[^a-z0-9]+", "-", name.lower()) + ".json"), "w", encoding="utf-8") as fh:
                json.dump(self.profiles[name], fh, indent=2)

    # ---------- time-based ----------
    def tick(self):
        now = self.clock()
        out = []
        for cid, st in self.state.items():
            if cid.startswith("_"):
                continue
            for r in st.get("reminders", []):
                if not r["sent"] and datetime.fromisoformat(r["at"]) <= now:
                    r["sent"] = True
                    out.append(self.say(cid, r["text"]))
            pr = st.get("project")
            if pr and pr.get("assign") and 9 <= now.hour < 22 and now <= datetime.fromisoformat(pr["due"]):  # quiet hours, and stop after the deadline
                due_soon = {}
                for t in pr["tasks"]:
                    name = t["name"]
                    if name in pr["done"] or name in pr["nudged"]:
                        continue
                    dl = datetime.fromisoformat(pr["deadlines"][name])
                    if now >= dl - timedelta(hours=20):
                        due_soon.setdefault(pr["assign"][name], []).append((name, dl))
                for who, items in due_soon.items():
                    for name, _ in items:
                        pr["nudged"].append(name)
                    free = [p for p in sorted(set(pr["assign"].values())) if p != who and
                            all(x in pr["done"] for x, w in pr["assign"].items() if w == p)]
                    help_ = (" %s is all done, maybe they can help?" % free[0]) if free else ""
                    names = " + ".join("'%s'" % n for n, _ in items)
                    when = min(d for _, d in items).strftime("%a")
                    out.append(self.say(cid, "friendly check-in 👋 %s, how's %s going? due %s.%s reply \"done <part>\" when it's in" % (
                        who, names, when, help_)))
        out += self.feature_tick(now)
        if out:
            self.save()
        return out
