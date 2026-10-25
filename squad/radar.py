"""Group radar: what actually matters in the chat.

highlights (importance scoring, Grok picks when available) · summary of messages that tag you · voice notes ·
participation stats · pins (‼️ tapback or 📌) · remind #N · unanswered-question bumps · welcome-back catch-up DMs ·
decision log · weekly Squad Wrapped."""
import os
import re
import statistics
from collections import Counter
from datetime import datetime, timedelta

from . import voice
from .llm import HIGHLIGHT_SYSTEM, MENTIONS_SYSTEM, TRANSLATE_SYSTEM
from .timeparse import window

TIME_RX = re.compile(r"\b(today|tonight|tmrw|tomorrow|tmr|this weekend|mon(?:day)?|tues?(?:day)?|wed(?:nesday)?|thu(?:rs?)?(?:day)?|"
                     r"fri(?:day)?|sat(?:urday)?|sun(?:day)?|\d{1,2}(?::\d{2})?\s?(?:am|pm)|\d{1,2}/\d{1,2}|noon|midnight)\b", re.I)
MONEY_RX = re.compile(r"\$\s?\d|\b\d+\s?(?:bucks|dollars)\b|\b(?:venmo|zelle|owe[sd]?|pay (?:me|you|back)|rent|deposit|split)\b", re.I)
URGENT_RX = re.compile(r"\b(due|deadline|asap|urgent|important|exam|midterm|final|quiz|lease|landlord|move[- ]?out|cancel+ed|cancel|"
                       r"changed?|moved to|postponed|rescheduled|reminder|don'?t forget|heads up|psa|fyi)\b", re.I)
DECIDE_RX = re.compile(r"\b(let'?s (?:do|go|meet|just)|decided|final(?:ly)?|confirmed|locked|booked|reserved|we'?re going|"
                       r"plan is|it'?s (?:set|decided)|sold)\b", re.I)
LINK_RX = re.compile(r"https?://\S+")
ALL_RX = re.compile(r"@(?:all|everyone|here)\b|\beveryone\b|\by'?all\b|\bguys\b|\bany(?:one|body)\b|\bsomeone\b|\bwho\b", re.I)
FILLER_RX = re.compile(r"^(?:lo+l+|lmf?ao+|ha(?:ha)+|ok+|okay|k+|ya+|yea+h?|yep|nah|bet|ty|thx|same|fr|true|real|w|l|nice|omg|wow|"
                       r"[\U0001F000-\U0001FAFF☀-➿]+)[\s!.?]*$", re.I)
EMOJI_RX = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
REACT_WEIGHT = {"love": 1.5, "like": 1, "laugh": 0.5, "emphasize": 3, "question": 1, "dislike": 0.5, "emoji": 1}
REACT_ICON = {"love": "❤️", "like": "👍", "laugh": "😂", "emphasize": "‼️", "question": "❓", "dislike": "👎", "emoji": "✨"}
FILTERS = [(r"\b(?:money|bills?|venmo|owes?)\b", "💸"), (r"\b(?:plans?|dates?|times?|when)\b", "📅"),
           (r"\b(?:deadlines?|due|urgent)\b", "⏰"), (r"\b(?:decisions?|decided)\b", "✅"), (r"\bquestions?\b", "❓"),
           (r"\blinks?\b", "🔗"), (r"\bpins?\b", "📌")]


def clock(dt):
    return dt.strftime("%I").lstrip("0") + ("" if dt.minute == 0 else dt.strftime(":%M")) + dt.strftime("%p").lower()


def bar(n, total, width=10):
    k = 0 if not total else max(1 if n else 0, round(width * n / total))
    return "▓" * k + "░" * (width - k)


TAG_ORDER = "🙋📌⏰💸✅❓📅🔗"
BOTTY = re.compile(r"^(?:remind|pin|unpin|want|offer|pass|sold|drop|claim|accept|dibs|done|got|\+|📌|[12]$|in$|out$)", re.I)


class RadarMixin:
    # ---------- memory ----------
    def log_event(self, st, kind, cents=0):
        lg = st.setdefault("log", [])
        lg.append({"k": kind, "at": self.clock().isoformat(), "c": cents})
        del lg[:-200]

    def _hist(self, st):
        return st.setdefault("history", [])

    def _find(self, st, mid):
        return next((x for x in self._hist(st) if x.get("id") == mid), None)

    def names_for(self, who):
        p = self.profiles.get(who, {})
        out = [who] + list(p.get("prefs", {}).get("nicknames", []))
        return [n for n in out if n and len(n) >= 3 and not n.startswith("+")]

    def tags_who(self, text, who):
        return any(re.search(r"(?<![\w])@?%s\b" % re.escape(n), text, re.I) for n in self.names_for(who))

    def _window(self, text, default_hours):
        now = self.clock()
        t = (text or "").lower()
        m = re.search(r"(\d+)\s*(h|hr|hrs|hours?|d|days?)\b", t)
        if m:
            n = int(m.group(1))
            return now - (timedelta(days=n) if m.group(2).startswith("d") else timedelta(hours=n)), "last %s" % m.group(0).replace(" ", "")
        if "today" in t:
            return now.replace(hour=0, minute=0, second=0, microsecond=0), "today"
        if "week" in t:
            return now - timedelta(days=7), "this week"
        if re.search(r"\ball\b", t):
            return datetime(2000, 1, 1, tzinfo=now.tzinfo), "all time"
        return now - timedelta(hours=default_hours), "last %dh" % default_hours if default_hours < 48 else "last %d days" % (default_hours // 24)

    def _since(self, st, cutoff):
        return [x for x in self._hist(st) if datetime.fromisoformat(x["at"]) >= cutoff]

    def _when(self, x):
        dt = datetime.fromisoformat(x["at"])
        now = self.clock()
        if dt.date() == now.date():
            return clock(dt)
        if dt.date() == (now - timedelta(days=1)).date():
            return "yesterday " + clock(dt)
        return dt.strftime("%a ") + clock(dt)

    # ---------- scoring ----------
    def answered(self, st, x):
        """Did someone else say anything within 3h after this message?"""
        h = self._hist(st)
        t0 = datetime.fromisoformat(x["at"])
        try:
            i = h.index(x)
        except ValueError:
            return True
        for y in h[i + 1:]:
            if datetime.fromisoformat(y["at"]) - t0 > timedelta(hours=3):
                break
            if y["who"] != x["who"]:
                return True
        return False

    def score(self, st, x, reader=None):
        t = x["text"]
        tags = []
        if FILLER_RX.match(t.strip()) or len(t.strip()) < 3:
            return -3, tags
        s = 0.0
        if x.get("pinned"):
            s += 6; tags.append("📌")
        if URGENT_RX.search(t):
            s += 3; tags.append("⏰")
        if TIME_RX.search(t):
            s += 2; tags.append("📅")
        if MONEY_RX.search(t):
            s += 2; tags.append("💸")
        if DECIDE_RX.search(t):
            s += 2; tags.append("✅")
        if "?" in t and len(t.split()) >= 3:
            s += 1.5 + (1 if not self.answered(st, x) else 0); tags.append("❓")
        if LINK_RX.search(t):
            s += 1; tags.append("🔗")
        if re.search(r"@(?:all|everyone|here)\b", t, re.I):
            s += 1.5
        if re.search(r"!!|\b[A-Z]{4,}\b", t):
            s += 0.5
        if len(t) > 140:
            s += 0.5
        s += sum(REACT_WEIGHT.get(k, 1) for k in x.get("reactions", {}).values())
        if reader and x["who"] != reader and self.tags_who(t, reader):
            s += 4; tags.append("🙋")
        tags.sort(key=TAG_ORDER.index)
        return s, tags

    def _react_str(self, x):
        c = Counter(x.get("reactions", {}).values())
        return "".join(" %s%s" % (REACT_ICON.get(k, "✨"), n if n > 1 else "") for k, n in c.most_common(3))

    def _line(self, n, x, tags, why=""):
        snippet = x["text"].replace("\n", " ")
        snippet = snippet if len(snippet) <= 110 else snippet[:107] + "..."
        return "%d. %s %s · %s: \"%s\"%s%s" % (n, "".join(tags[:2]) or "💬", x["who"], self._when(x), snippet,
                                              self._react_str(x), (" ← " + why) if why else "")

    # ---------- highlights ----------
    def highlights(self, st, who, text="", limit=5):
        cutoff, label = self._window(text, 24)
        if re.search(r"\bdecisions?\b", text or "", re.I) and not re.search(r"\d|today", text or ""):
            cutoff, label = self._window("week", 0)
        chunk = self._since(st, cutoff)
        want = next((v for pat, v in FILTERS if re.search(pat, text or "", re.I)), None)
        scored = []
        for x in chunk:
            s, tags = self.score(st, x, who)
            if want and want not in tags:
                continue
            if s >= 2.5 or (want and s > 0):
                scored.append((s, x, tags))
        scored.sort(key=lambda r: -r[0])
        picks = [(x, tags, "") for _, x, tags in scored[:limit]]
        if self.llm and getattr(self.llm, "enabled", False) and len(scored) >= 2 and not want:
            cands = scored[:15]
            listing = "\n".join("%d. %s (%s) %s: %s" % (i + 1, x["who"], self._when(x), "".join(tags), x["text"][:300])
                                for i, (_, x, tags) in enumerate(cands))
            res = self.llm.json("Reader: %s\n\n%s" % (who, listing), HIGHLIGHT_SYSTEM)
            try:
                chosen = [(cands[int(p["n"]) - 1][1], cands[int(p["n"]) - 1][2], str(p.get("why", ""))[:60])
                          for p in (res or {}).get("picks", []) if 1 <= int(p["n"]) <= len(cands)]
            except (TypeError, ValueError, KeyError):
                chosen = []
            if chosen:
                seen = {id(x) for x, _, _ in chosen}
                fill = [p for p in picks if id(p[0]) not in seen]
                picks = (chosen + fill[: max(0, min(3, len(picks)) - len(chosen))])[:limit]
        return picks, chunk, label

    def cmd_highlights(self, msg, st, who, text):
        cid = msg["chat_id"]
        picks, chunk, label = self.highlights(st, who, text)
        if not chunk:
            return [self.say(cid, "quiet in here (%s) 😌" % label)]
        if not picks:
            return [self.say(cid, "nothing big in %d msgs (%s), mostly vibes ✨" % (len(chunk), label))]
        st["last_hl"] = [x.get("id") for x, _, _ in picks]
        title = "✅ decisions" if re.search(r"decision", text or "", re.I) else "✨ highlights"
        lines = ["%s (%s · %d msgs → %d that matter):" % (title, label, len(chunk), len(picks))]
        lines += [self._line(i + 1, x, tags, why) for i, (x, tags, why) in enumerate(picks)]
        lines.append("reply \"remind #1\" · \"pin #2\" · \"@squad voice highlights\" to listen")
        return self._maybe_voice(msg, st, who, [self.say(cid, "\n".join(lines))])

    # ---------- tagged / mentions ----------
    def mentions(self, st, who, text=""):
        cutoff, label = self._window(text, 72)
        out = []
        for x in self._since(st, cutoff):
            if x["who"] == who:
                continue
            direct = self.tags_who(x["text"], who)
            everyone = bool(re.search(r"@(?:all|everyone|here)\b", x["text"], re.I))
            if not (direct or everyone):
                continue
            later = [y for y in self._hist(st) if y["who"] == who and y["at"] > x["at"] and not BOTTY.match(y["text"].strip())]
            kind = "❓ asked you" if direct and "?" in x["text"] else ("📣 @everyone" if not direct else "💬 tagged you")
            out.append({"x": x, "kind": kind, "replied": bool(later)})
        return out, label

    def cmd_mentions(self, msg, st, who, text):
        cid = msg["chat_id"]
        items, label = self.mentions(st, who, text)
        if not items:
            return [self.say(cid, "nobody tagged you (%s) %s 🫶" % (label, who))]
        waiting = [i for i in items if not i["replied"] and i["kind"].startswith("❓")]
        lines = ["🙋 %s, you got tagged %d time%s (%s)%s:" % (who, len(items), "" if len(items) == 1 else "s", label,
                                                            ", %d still waiting on you" % len(waiting) if waiting else "")]
        for i in items[-8:]:
            x = i["x"]
            flag = " ⚠️ no reply yet" if not i["replied"] and i["kind"].startswith("❓") else ""
            lines.append("- %s · %s %s: \"%s\"%s" % (i["kind"], x["who"], self._when(x), x["text"][:100].replace("\n", " "), flag))
        if self.llm and getattr(self.llm, "enabled", False):
            convo = "\n".join("%s: %s" % (i["x"]["who"], i["x"]["text"][:300]) for i in items[-12:])
            todo = self.llm.chat("Person: %s\n\nMessages that tag them:\n%s" % (who, convo), system=MENTIONS_SYSTEM, max_tokens=160)
            if todo:
                lines.append("your to-do from this:\n" + todo.strip())
        return self._maybe_voice(msg, st, who, [self.say(cid, "\n".join(lines))])

    # ---------- context for free-form answers ----------
    def context_for(self, st, who, lines=25):
        """A compact picture of this squad for the model: recent messages plus what the bot is tracking."""
        out = ["Recent chat (oldest first):"]
        for x in self._hist(st)[-lines:]:
            out.append("%s: %s" % (x["who"], x["text"][:200].replace("\n", " ")))
        items = [r for r in ((st.get("market") or {}).get("items") or {}).values() if r.get("status") != "sold"]
        if items:
            out.append("For sale: " + "; ".join("#%s %s $%s from %s%s" % (r["id"], r["title"], r["price"], r["seller"],
                                                                      " (on hold for %s)" % r["hold"]["by"] if r.get("hold") else "")
                                                for r in items[:12]))
        poll = st.get("poll")
        if poll and poll.get("options"):
            if poll.get("winner") is not None:
                o = poll["options"][poll["winner"]]
                out.append("Plan %s: %s at %s, going: %s" % (poll["status"], o["activity"]["what"], o["start"], ", ".join(poll.get("ins", []))))
            else:
                out.append("Plan vote open: " + " | ".join("%d) %s at %s" % (i + 1, o["activity"]["what"], o["start"]) for i, o in enumerate(poll["options"])))
        owes = [r for r in st.get("owes", []) if not r.get("paid")]
        if owes:
            out.append("Owed: " + "; ".join("%s owes %s $%.2f (%s)" % (r["from"], r["to"], r["cents"] / 100, r["note"]) for r in owes[:10]))
        if st.get("groceries"):
            out.append("Grocery list: " + ", ".join(g["item"] for g in st["groceries"]))
        if st.get("chores"):
            out.append("Chores this week: " + ", ".join("%s: %s" % kv for kv in self.chore_assign(st["chores"], self.clock()).items()))
        pr = st.get("project")
        if pr:
            out.append("Project %s due %s: " % (pr.get("name"), pr.get("due")) + ", ".join(
                "%s (%s%s)" % (t["name"], (pr.get("assign") or {}).get(t["name"], "unclaimed"), ", done" if t["name"] in pr.get("done", {}) else "")
                for t in pr.get("tasks", [])))
        pins = [x for x in self._hist(st) if x.get("pinned")]
        if pins:
            out.append("Pinned: " + " | ".join("%s: %s" % (x["who"], x["text"][:100]) for x in pins[-4:]))
        rem = [r for r in st.get("reminders", []) if not r.get("sent")]
        if rem:
            out.append("Upcoming reminders: " + " | ".join("%s at %s" % (r["text"][:80], r["at"]) for r in rem[:4]))
        out.append("Now: %s. The person asking is %s." % (self.clock().strftime("%a %b %d %I:%M %p"), who))
        return "\n".join(out)

    # ---------- pins & reminders ----------
    def on_reaction(self, msg):
        """An iMessage tapback. ‼️ (Emphasize) pins the message; every reaction counts toward importance."""
        st = self.chat_state(msg["chat_id"])
        who = self.name_of(msg["sender"]) or msg["sender"]
        x = next((y for y in reversed(self._hist(st)) if y.get("guid") and y["guid"] == msg.get("target")), None)
        if not x:
            return []
        r = x.setdefault("reactions", {})
        if msg.get("removed"):
            r.pop(who, None)
            if msg.get("reaction") == "emphasize" and x.get("pinned_by") == who:
                x["pinned"] = False
        else:
            r[who] = msg.get("reaction", "like")
            if msg.get("reaction") == "emphasize":
                x["pinned"], x["pinned_by"] = True, who
        self.save()
        return []

    def ctx_radar(self, msg, st, who, low):
        cid = msg["chat_id"]
        m = re.match(r"^(un)?pin\s+#?(\d+)$", low)
        if m and st.get("last_hl"):
            n = int(m.group(2))
            if 1 <= n <= len(st["last_hl"]):
                x = self._find(st, st["last_hl"][n - 1])
                if x:
                    x["pinned"], x["pinned_by"] = not m.group(1), who
                    return [self.say(cid, ("📌 pinned: %s: \"%s\"" if not m.group(1) else "unpinned: %s: \"%s\"") % (x["who"], x["text"][:80]))]
        if re.fullmatch(r"📌|pin (?:that|this|it)|pinned", low):
            prev = [x for x in self._hist(st)[:-1] if x["who"] != who] or self._hist(st)[:-1]
            if prev:
                x = prev[-1]
                x["pinned"], x["pinned_by"] = True, who
                return [self.say(cid, "📌 pinned %s's message. \"@squad pins\" shows them all" % x["who"])]
        m = re.match(r"^remind(?: me| us)?\s+#?(\d+)\s*(.*)$", low)
        if m and st.get("last_hl"):
            n = int(m.group(1))
            if not 1 <= n <= len(st["last_hl"]):
                return None
            x = self._find(st, st["last_hl"][n - 1])
            if not x:
                return None
            at, how = self._remind_time(m.group(2), x["text"])
            st.setdefault("reminders", []).append({"at": at.isoformat(), "chat_id": cid, "sent": False,
                                                   "text": "🔔 reminder for %s: %s said \"%s\"" % (who, x["who"], x["text"][:120])})
            return [self.say(cid, "🔔 got it, I'll remind you %s" % how)]
        return None

    def _remind_time(self, spec, text):
        now = self.clock()
        m = re.search(r"in\s+(\d+)\s*(m|min|mins|minutes?|h|hrs?|hours?)\b", spec)
        if m:
            d = timedelta(minutes=int(m.group(1))) if m.group(2).startswith("m") else timedelta(hours=int(m.group(1)))
            return now + d, "in %s" % m.group(0)[3:]
        if "tonight" in spec:
            t = now.replace(hour=20, minute=0, second=0, microsecond=0)
            return (t if t > now else now + timedelta(hours=1)), "tonight at 8pm"
        if "tomorrow" in spec:
            t = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
            return t, "tomorrow at 9am"
        if TIME_RX.search(text):
            s, _, _ = window(text, now)
            if s and s - timedelta(hours=2) > now:
                return s - timedelta(hours=2), "2h before (%s %s)" % (s.strftime("%a"), clock(s))
        t = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        return t, "tomorrow at 9am"

    def cmd_pins(self, msg, st, who, text):
        cid = msg["chat_id"]
        pins = [x for x in self._hist(st) if x.get("pinned")]
        if not pins:
            return [self.say(cid, "no pins yet. ‼️ (Emphasize) a message or reply 📌 right after it")]
        st["last_hl"] = [x.get("id") for x in pins[-8:]]
        lines = ["📌 pinned:"] + [self._line(i + 1, x, []) for i, x in enumerate(pins[-8:])]
        return [self.say(cid, "\n".join(lines))]

    # ---------- voice ----------
    def _maybe_voice(self, msg, st, who, out):
        if st.get("settings", {}).get("voice") and out and out[0].get("text"):
            out += self._voice_note(msg, who, out[0]["text"], "summary")[1:]
        return out

    def _voice_note(self, msg, who, text, label, lang=None):
        cid = msg["chat_id"]
        if self.prefix and text.startswith(self.prefix):
            text = text[len(self.prefix):]
        text = voice.speakable(re.sub(r"\n?reply \".*$", "", text))  # skip the "reply ..." footer out loud
        lang = (lang or self.profiles.get(who, {}).get("prefs", {}).get("language") or "english").lower()
        if lang not in ("english", "en") and self.llm and getattr(self.llm, "enabled", False):
            tr = self.llm.chat("Target language: %s\nText: %s" % (lang, text), system=TRANSLATE_SYSTEM, max_tokens=600)
            if tr:
                text = tr
            else:
                lang = "english"
        elif lang not in ("english", "en"):
            lang = "english"
        tts = getattr(self, "tts", None) or voice.speak
        path = os.path.join(self.outdir, "voice-%s-%s.m4a" % (label.replace(" ", "-"), self.clock().strftime("%Y%m%d-%H%M%S")))
        got = tts(text, path, lang)
        if not got:
            return [self.say(cid, "🔇 voice notes need the bot running on a Mac (it uses the Mac's built-in voices)")]
        secs = max(3, round(len(voice.speakable(text).split()) / 2.6))
        return [self.say(cid, "🔊 %s for %s (~%ds%s)" % (label, who, secs, ", " + lang if lang != "english" else "")),
                {"chat_id": cid, "file": got}]

    def cmd_voice(self, msg, st, who, text):
        cid = msg["chat_id"]
        arg = re.sub(r"(?i)^(voice|listen|read(?: it)?(?: out)?(?: loud)?|say it|speak)\s*(?:note|to me)?\s*", "", text).strip()
        lang = None
        m = re.search(r"\s*\bin\s+([a-z]+)\s*$", arg, re.I)
        if m and m.group(1).lower() in voice.LOCALES:
            lang, arg = m.group(1).lower(), arg[: m.start()].strip()
        low = arg.lower()
        if low in ("on", "off"):
            st.setdefault("settings", {})["voice"] = low == "on"
            return [self.say(cid, "🔊 voice notes %s for highlights, tldr and mentions" % ("on" if low == "on" else "off"))]
        if low in ("", "tldr", "summary", "catch me up"):
            src, label = self.cmd_tldr(msg, st, who, "tldr"), "tldr"
        elif low.startswith(("highlight", "important")):
            src, label = self._plain(self.cmd_highlights, msg, st, who, low), "highlights"
        elif low in ("me", "mentions", "tags", "tagged", "my tags"):
            src, label = self._plain(self.cmd_mentions, msg, st, who, ""), "your tags"
        elif low.startswith("wrapped"):
            src, label = self.cmd_wrapped(msg, st, who, ""), "squad wrapped"
        elif low in ("that", "last", "it", "this"):
            prev = [x for x in self._hist(st) if x["who"] != who] or self._hist(st)
            if not prev:
                return [self.say(cid, "nothing to read yet")]
            src, label = [self.say(cid, "%s said: %s" % (prev[-1]["who"], prev[-1]["text"]))], "read aloud"
        else:
            src, label = [self.say(cid, arg)], "voice note"
        body = src[0]["text"] if src and src[0].get("text") else ""
        return self._voice_note(msg, who, body, label, lang)

    def _plain(self, fn, msg, st, who, text):
        saved = st.get("settings", {}).get("voice")
        st.setdefault("settings", {})["voice"] = False
        try:
            return fn(msg, st, who, text)
        finally:
            st["settings"]["voice"] = saved

    # ---------- participation ----------
    def _hidden(self, name):
        return bool(self.profiles.get(name, {}).get("prefs", {}).get("hide_stats"))

    def participation(self, st, chunk):
        counts = Counter(x["who"] for x in chunk)
        starts, reacts, asks, owls, gaps = Counter(), Counter(), Counter(), Counter(), {}
        prev = None
        for x in chunk:
            t = datetime.fromisoformat(x["at"])
            if prev is None or t - datetime.fromisoformat(prev["at"]) > timedelta(hours=2):
                starts[x["who"]] += 1
            elif prev["who"] != x["who"]:
                gap = (t - datetime.fromisoformat(prev["at"])).total_seconds() / 60
                if gap <= 60:
                    gaps.setdefault(x["who"], []).append(gap)
            if "?" in x["text"] and len(x["text"].split()) >= 3:
                asks[x["who"]] += 1
            if t.hour < 4:
                owls[x["who"]] += 1
            reacts[x["who"]] += len(x.get("reactions", {}))
            prev = x
        fast = {w: statistics.median(g) for w, g in gaps.items() if len(g) >= 3}
        return counts, starts, reacts, asks, owls, fast

    def cmd_stats(self, msg, st, who, text):
        cid = msg["chat_id"]
        cutoff, label = self._window(text if re.search(r"\d|today|all|week", text or "") else "week", 0)
        chunk = self._since(st, cutoff)
        if not chunk:
            return [self.say(cid, "no messages yet (%s)" % label)]
        counts, starts, reacts, asks, owls, fast = self.participation(st, chunk)
        hidden = {n for n in counts if self._hidden(n)}
        total = sum(counts.values())
        lines = ["📊 who's talking (%s, %d msgs):" % (label, total)]
        width = max([len(n) for n in counts if n not in hidden] + [4])
        for n, c in counts.most_common():
            if n in hidden:
                continue
            lines.append("%s %s %d%% · %d" % (n.ljust(width), bar(c, total), round(100 * c / total), c))
        if hidden:
            lines.append("(%d keep their stats private)" % len(hidden))

        def top(counter, fmt):
            vis = [(n, v) for n, v in counter.most_common() if n not in hidden and v]
            return fmt % vis[0] if vis else None
        for line in (top(starts, "🗣️ starts the most convos: %s (%d)"),
                     top(reacts, "❤️ most reactions: %s (%d)"),
                     top(asks, "❓ asks the most questions: %s (%d)"),
                     top(owls, "🦉 night owl: %s (%d msgs after midnight)")):
            if line:
                lines.append(line)
        if fast:
            n, g = min(((n, g) for n, g in fast.items() if n not in hidden), key=lambda r: r[1], default=(None, None))
            if n:
                lines.append("⚡ fastest replier: %s (~%s)" % (n, "under a min" if g < 1 else "%d min" % round(g)))
        members = [self.name_of(h) or h for h in st.get("members", [])]
        quiet = [n for n in members if n and n not in counts and not n.startswith("+") and not self._hidden(n)]
        if quiet:
            lines.append("👋 haven't heard from %s %s, someone check in?" % (" & ".join(quiet[:3]), label))
        pr = st.get("project")
        if pr and pr.get("assign"):
            lines.append("📚 %s:" % pr["name"])
            for p in sorted(set(pr["assign"].values())):
                mine = [t for t, w in pr["assign"].items() if w == p]
                done = [t for t in mine if t in pr.get("done", {})]
                lines.append("- %s %s %d/%d parts done" % (p, bar(len(done), len(mine), 6), len(done), len(mine)))
        lines.append("(counts only this chat. DM me \"squad hide my stats\" to opt out)")
        return [self.say(cid, "\n".join(lines))]

    # ---------- squad wrapped ----------
    def cmd_wrapped(self, msg, st, who, text):
        cid = msg["chat_id"]
        low = (text or "").lower().strip()
        if low in ("on", "off"):
            st.setdefault("settings", {})["wrapped"] = low == "on"
            return [self.say(cid, "🎁 Squad Wrapped %s (Sundays 8pm)" % ("on" if low == "on" else "off"))]
        now = self.clock()
        start = now - timedelta(days=7)
        chunk = self._since(st, start)
        if len(chunk) < 5:
            return [self.say(cid, "not enough chat this week for a Wrapped yet 👀")]
        counts, starts, reacts, asks, owls, fast = self.participation(st, chunk)
        vis = [(n, c) for n, c in counts.most_common() if not self._hidden(n)]
        lines = ["🎁 Squad Wrapped · %s %d – %s %d" % (start.strftime("%b"), start.day, now.strftime("%b"), now.day)]
        hours = Counter(datetime.fromisoformat(x["at"]).strftime("%a ") + clock(datetime.fromisoformat(x["at"]).replace(minute=0)) for x in chunk)
        lines.append("💬 %d messages · busiest hour: %s" % (len(chunk), hours.most_common(1)[0][0]))
        if vis:
            lines.append("🏆 top yapper: %s (%d)%s" % (vis[0][0], vis[0][1], (" · 🤫 lowkey: %s (%d)" % vis[-1]) if len(vis) > 2 else ""))
        best = max(chunk, key=lambda x: (sum(REACT_WEIGHT.get(k, 1) for k in x.get("reactions", {}).values()), self.score(st, x)[0]))
        lines.append("🔥 message of the week%s: %s: \"%s\"" % (self._react_str(best) and " (" + self._react_str(best).strip() + ")", best["who"], best["text"][:100]))
        emo = Counter(e for x in chunk for e in EMOJI_RX.findall(x["text"]))
        if emo:
            e, n = emo.most_common(1)[0]
            lines.append("😂 emoji of the week: %s (%d)" % (e, n))
        lg = [e for e in st.get("log", []) if datetime.fromisoformat(e["at"]) >= start]
        k = Counter(e["k"] for e in lg)
        money = sum(e["c"] for e in lg if e["k"] == "bill")
        bits = [s for s in ("📅 plans locked: %d" % k["plan"] if k["plan"] else "",
                            "💸 bills split: %d ($%.2f)" % (k["bill"], money / 100) if k["bill"] else "",
                            "🛒 items sold: %d" % k["sold"] if k["sold"] else "",
                            "🚗 carpools: %d" % k["rides"] if k["rides"] else "") if s]
        if bits:
            lines.append(" · ".join(bits))
        pins = [x for x in chunk if x.get("pinned")]
        if pins:
            lines.append("📌 pinned this week: %d" % len(pins))
        return [self.say(cid, "\n".join(lines))]

    # ---------- welcome back ----------
    def welcome_back(self, msg, st, who):
        if not st.get("settings", {}).get("welcome") or msg["sender"] == "me":
            return []
        h = self._hist(st)
        mine = [i for i, x in enumerate(h) if x["who"] == who]
        if not mine:
            return []
        last = h[mine[-1]]
        since = h[mine[-1] + 1:]
        if self.clock() - datetime.fromisoformat(last["at"]) < timedelta(hours=12) or len(since) < 25:
            return []
        cutoff = datetime.fromisoformat(last["at"])
        hrs = max(1, int((self.clock() - cutoff).total_seconds() // 3600))
        picks, _, _ = self.highlights(st, who, "%dh" % hrs, limit=3)
        tags, _ = self.mentions(st, who, "%dh" % hrs)
        lines = ["👋 welcome back %s, you missed %d msgs. what matters:" % (who, len(since))]
        lines += ["- %s %s: \"%s\"" % ("".join(t[:1]) or "💬", x["who"], x["text"][:90]) for x, t, _ in picks] or ["- nothing big"]
        if tags:
            lines.append("🙋 you got tagged %d time%s. \"@squad mentions\" in the group for those" % (len(tags), "" if len(tags) == 1 else "s"))
        return [{"dm": msg["sender"], "text": self.prefix + "\n".join(lines)}]

    # ---------- unanswered questions ----------
    def tick_radar(self, cid, st, now):
        out = []
        sets = st.get("settings", {})
        if sets.get("bumps", True) and 9 <= now.hour < 22:
            day = now.date().isoformat()
            done_today = st.get("bump_day") == day and st.get("bumps_today", 0) >= 2
            if not done_today:
                for x in reversed(self._hist(st)[-80:]):
                    age = now - datetime.fromisoformat(x["at"])
                    if age > timedelta(hours=20):
                        break
                    if x.get("bumped") or age < timedelta(hours=3) or "?" not in x["text"]:
                        continue
                    words = x["text"].split()
                    if len(words) < 4 or not ALL_RX.search(x["text"]) or self.answered(st, x):
                        continue
                    x["bumped"] = True
                    if st.get("bump_day") != day:
                        st["bump_day"], st["bumps_today"] = day, 0
                    st["bumps_today"] += 1
                    out.append(self.say(cid, "👀 bumping %s's question from %s, nobody answered: \"%s\"" % (x["who"], self._when(x), x["text"][:140])))
                    break
        if sets.get("wrapped") and now.weekday() == 6 and now.hour == 20 and st.get("wrapped_day") != now.date().isoformat():
            st["wrapped_day"] = now.date().isoformat()
            w = self.cmd_wrapped({"chat_id": cid, "members": st.get("members", [])}, st, None, "")
            if w and "not enough" not in w[0]["text"]:
                out += w
        return out
