"""Squad Radar web app: group chats in the browser with the bot built in, plus a live board of everything it
organizes (plans, invites, money, market, rides, projects, chores, pins, highlights, stats).

Standard library only, so it runs anywhere Python runs (Railway, Render, Fly, your laptop).
    python3 web.py                      -> http://localhost:8080
Settings come from the environment or .env: PORT, SQUAD_DATA (where chats are saved), SQUAD_TZ, and the model:
XAI_API_KEY for Grok, or LLM_BASE_URL + LLM_API_KEY + LLM_MODEL for any OpenAI-compatible provider."""
import json
import mimetypes
import os
import re
import secrets
import shutil
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import run  # noqa: E402  (load_env, load_profiles)
from squad.bot import SquadBot  # noqa: E402
from squad.llm import Grok  # noqa: E402

STATIC = os.path.join(HERE, "webapp")
CODE_CHARS = "abcdefghjkmnpqrstuvwxyz23456789"
REACTIONS = {"love", "like", "laugh", "emphasize", "question", "dislike"}
SAMPLE_FRIENDS = ["Priya", "Jake", "Sam"]


def public_url():
    """Where people open the website: SQUAD_PUBLIC_URL, Railway's domain, or this machine."""
    if os.environ.get("SQUAD_PUBLIC_URL"):
        return os.environ["SQUAD_PUBLIC_URL"].rstrip("/")
    if os.environ.get("RAILWAY_PUBLIC_DOMAIN"):
        return "https://" + os.environ["RAILWAY_PUBLIC_DOMAIN"]
    if os.environ.get("RENDER_EXTERNAL_URL"):
        return os.environ["RENDER_EXTERNAL_URL"].rstrip("/")
    return "http://localhost:%s" % os.environ.get("PORT", "8080")


def iso(dt):
    return dt.isoformat(timespec="seconds")


class Budget:
    """Wraps the model client with a daily call cap, so a public deployment can't run up a bill."""

    def __init__(self, inner, per_day):
        self.inner, self.per_day, self.day, self.used = inner, per_day, None, 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    @property
    def enabled(self):
        return self.inner.enabled and self._room()

    def _room(self):
        today = time.strftime("%Y-%m-%d")
        if today != self.day:
            self.day, self.used = today, 0
        return self.used < self.per_day

    def _spend(self, fn, *a, **k):
        if not self.enabled:
            return None
        self.used += 1
        return fn(*a, **k)

    def chat(self, *a, **k):
        return self._spend(self.inner.chat, *a, **k)

    def json(self, *a, **k):
        return self._spend(self.inner.json, *a, **k)

    def vision_json(self, *a, **k):
        return self._spend(self.inner.vision_json, *a, **k)


class App:
    def __init__(self, data_dir, llm=None, clock=None, tz=None, samples=True):
        self.data = os.path.abspath(data_dir)
        os.makedirs(self.data, exist_ok=True)
        self.tz = tz or ZoneInfo(os.environ.get("SQUAD_TZ", "America/Phoenix"))
        self._clock = clock or (lambda: datetime.now(self.tz))
        self.offset = timedelta(0)
        self.lock = threading.RLock()      # the bot's work, which may wait on the model
        self.db_lock = threading.RLock()   # quick reads and writes of chats, so the feed never waits on the model
        self.boards = {}                   # (room, handle) -> board snapshot
        self.senders = []                  # other chat apps: fn(output, ai) -> True if it delivered it
        prof_dir = os.path.join(self.data, "profiles")
        if not os.path.isdir(prof_dir):  # only the sample friends; real people create their own profile when they join
            os.makedirs(prof_dir)
            for n in (SAMPLE_FRIENDS if samples else []):
                shutil.copy(os.path.join(HERE, "profiles", n.lower() + ".json"), prof_dir)
        self.files = os.path.join(self.data, "files")
        os.makedirs(self.files, exist_ok=True)
        events = json.load(open(os.path.join(HERE, "data", "events.json"), encoding="utf-8"))
        self.model = llm if llm is not None else Grok()
        budget = int(os.environ.get("SQUAD_LLM_DAILY_LIMIT", "800"))
        self.bot = SquadBot(run.load_profiles(prof_dir), events, self.tz, self.now, state_path=os.path.join(self.data, "state.json"),
                            llm=Budget(self.model, budget), outdir=self.files)
        self.bot.profile_dir = prof_dir
        self.bot.tts = self.tts
        self.db_path = os.path.join(self.data, "app.json")
        self.db = json.load(open(self.db_path, encoding="utf-8")) if os.path.exists(self.db_path) else {"users": {}, "rooms": {}, "inbox": {}}
        self.rate = {}
        self.sample_handles = {p["handles"][0]: n for n, p in self.bot.profiles.items() if n in SAMPLE_FRIENDS and p.get("handles")}

    # ---------- basics ----------
    def now(self):
        return self._clock() + self.offset

    def save(self):
        with self.db_lock:
            tmp = self.db_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.db, fh)
            os.replace(tmp, self.db_path)

    def tts(self, text, path, lang):
        """Voice notes in the browser: save what to say; the page reads it out with the device's own voice."""
        out = os.path.splitext(path)[0] + ".speech.json"
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"text": text, "lang": lang}, fh, ensure_ascii=False)
        return out

    def allow(self, key, limit, per=60):
        t = time.time()
        hits = [x for x in self.rate.get(key, []) if t - x < per]
        if len(hits) >= limit:
            self.rate[key] = hits
            return False
        hits.append(t)
        self.rate[key] = hits
        return True

    def user(self, token):
        u = self.db["users"].get(token or "")
        if not u:
            raise ApiError(401, "Your session expired. Enter your name again to continue.")
        return u

    def room(self, code, u=None):
        r = self.db["rooms"].get((code or "").lower())
        if not r:
            raise ApiError(404, "No squad with that code. Check the link or ask for a new one.")
        if u and u["handle"] not in r["members"] and not r.get("demo"):
            raise ApiError(403, "You're not in this squad yet. Open the invite link to join.")
        return r

    def model_mark(self):
        return len(getattr(self.model, "recent", []) or []), getattr(self.model, "ok", 0)

    def model_written(self, mark):
        """What the model wrote since `mark`: a list of its answers, or None if this client can't tell (then any use counts)."""
        recent = getattr(self.model, "recent", None)
        if recent is None:
            return None if getattr(self.model, "ok", 0) > mark[1] else []
        return list(recent[mark[0]:]) if len(recent) >= mark[0] else list(recent)

    def model_name(self):
        st = self.model.status() if hasattr(self.model, "status") else {}
        return st.get("model") or st.get("provider") or "model"

    def llm_status(self):
        st = self.model.status() if hasattr(self.model, "status") else {"enabled": bool(getattr(self.model, "enabled", False))}
        st["live"] = bool(st.get("ok"))
        if not st.get("enabled"):
            st["fix"] = ("No model key on this server. Set LLM_API_KEY (and LLM_BASE_URL, LLM_MODEL) in the host's "
                         "environment variables, not in .env, then redeploy.")
        elif st.get("resting"):
            st["fix"] = ("The model's free quota is used up for now. The bot keeps working on built-in replies and tries again in "
                         "about %d min. Add LLM_FALLBACK_BASE_URL / LLM_FALLBACK_API_KEY / LLM_FALLBACK_MODEL for a backup model."
                         % max(1, st["resting"] // 60))
        elif st.get("last_error") and not st.get("ok"):
            st["fix"] = "The key is set but the model call failed. Check the key is correct and has no spaces or quotes."
        return st

    # ---------- people ----------
    def join(self, name):
        name = re.sub(r"[^A-Za-z' -]", "", (name or "")).strip().title()[:24]
        if len(name) < 2:
            raise ApiError(400, "Enter a name with at least 2 letters.")
        with self.lock:
            uid = secrets.token_hex(5)
            handle = "web:" + uid
            name = self.ensure_profile(handle, name)
            token = secrets.token_urlsafe(24)
            self.db["users"][token] = {"id": uid, "name": name, "handle": handle, "created": iso(self.now()), "rooms": []}
            self.save()
        return token, self.db["users"][token]

    def ensure_profile(self, handle, name):
        """Give a person (web or Discord) a profile the bot knows them by. Returns their name in the bot."""
        with self.lock:
            known = self.bot.name_of(handle)
            if known:
                return known
            name = re.sub(r"[^A-Za-z' -]", "", (name or "")).strip().title()[:24] or "Friend"
            base, n = name, 2
            while name in self.bot.profiles:
                name, n = "%s %d" % (base, n), n + 1
            nick = [base] if name != base else []
            self.bot.profiles[name] = {"name": name, "handles": [handle.lower()], "share_reasons": False, "classes": [], "busy": [],
                                       "exams": [], "prefs": {"nicknames": nick} if nick else {}, "project": {}}
            self.bot.save_profile(name)
            return name

    # ---------- other chat apps (Discord) plug in here ----------
    def mirror_room(self, cid, title, members, source="discord", demo=False):
        """A chat from another app (a Discord channel) shown on the website with the same chat and Board."""
        with self.db_lock:
            r = self.db["rooms"].get(cid)
            if not r:
                r = {"code": cid, "name": title or "Discord channel", "created": iso(self.now()), "owner": None, "members": [],
                     "messages": [], "seq": 0, "sample": False, "source": source}
                self.db["rooms"][cid] = r
            if demo:
                r["demo"] = True  # demo channels are open to anyone signed in to the website
            if title and r["name"] != title:
                r["name"] = title
            for h in members:
                if h not in r["members"]:
                    r["members"].append(h)
            return r

    def make_link(self, handle, room=None, minutes=30):
        """A one-time sign-in link for someone from another app (sent to them privately)."""
        code = secrets.token_urlsafe(18)
        with self.db_lock:
            links = self.db.setdefault("links", {})
            now = time.time()
            for k in [k for k, v in links.items() if v["exp"] < now]:
                links.pop(k)
            links[code] = {"handle": handle, "room": room, "exp": now + minutes * 60}
        self.save()
        return "%s/l/%s" % (public_url(), code)

    def use_link(self, code):
        with self.db_lock:
            link = self.db.setdefault("links", {}).pop(code or "", None)
        if not link or link["exp"] < time.time():
            raise ApiError(410, "That sign-in link has expired or was already used. Type @squad board in Discord for a new one.")
        with self.lock:
            name = self.bot.name_of(link["handle"]) or "Friend"
            with self.db_lock:
                token = next((t for t, u in self.db["users"].items() if u["handle"] == link["handle"]), None)
                if not token:
                    token = secrets.token_urlsafe(24)
                    self.db["users"][token] = {"id": secrets.token_hex(5), "name": name, "handle": link["handle"],
                                               "created": iso(self.now()), "rooms": []}
                u = self.db["users"][token]
                u["name"] = name
                for code_, r in self.db["rooms"].items():
                    if link["handle"] in r["members"] and code_ not in u["rooms"]:
                        u["rooms"].append(code_)
                if link.get("room") in u["rooms"]:
                    u["rooms"].remove(link["room"])
                    u["rooms"].insert(0, link["room"])
            self.refresh_boards(link.get("room")) if link.get("room") in self.db["rooms"] else None
            self.save()
        return token, u, link.get("room")

    def import_history(self, cid, title, members, items):
        """Put recent Discord messages back on the website and in the bot's memory after a restart (no replies are sent).
        items: dicts with kind ('user' or 'bot'), handle, who, text, guid, at (ISO)."""
        with self.lock:
            r = self.mirror_room(cid, title, members, demo=True)
            st = self.bot.chat_state(cid)
            st["members"] = list(r["members"])
            have = {m.get("guid") for m in r["messages"]} | {x.get("guid") for x in st.get("history", [])}
            added = 0
            for it in items:
                if not it.get("text") or it.get("guid") in have:
                    continue
                m = self.add_msg(r, it["kind"], it["who"], it["handle"], it["text"], guid=it.get("guid"))
                m["at"] = it.get("at") or m["at"]
                if it["kind"] == "user":
                    self.bot.remember(st, it["who"], it["text"], it.get("guid"))
                    st["history"][-1]["at"] = m["at"]
                added += 1
            self.refresh_boards(cid)
            self.save()
            self.bot.save()
            return added

    def forget_room(self, cid):
        with self.lock:
            with self.db_lock:
                self.db["rooms"].pop(cid, None)
                for u in self.db["users"].values():
                    if cid in u["rooms"]:
                        u["rooms"].remove(cid)
                self.boards = {k: v for k, v in self.boards.items() if k[0] != cid}
            self.bot.state.pop(cid, None)
            self.bot.save()
            self.save()

    def external_message(self, cid, handle, text, members, guid=None, attachments=None, private=False, title=None, demo=False):
        """A message from another chat app. The bot's replies go out through self.senders and show on the website."""
        with self.lock:
            mark = self.model_mark()
            if not private:
                r = self.mirror_room(cid, title, members, demo=demo)
                self.add_msg(r, "user", self.bot.name_of(handle) or handle, handle, text, guid=guid)
            if private:
                body = text if re.match(r"(?i)^@?squad\b", text) else "squad " + text
                outs = self.bot.on_message({"chat_id": "dm:" + handle, "text": body, "sender": handle, "members": [handle], "is_group": False})
            else:
                self.bot.chat_state(cid)["members"] = members
                outs = self.bot.on_message({"chat_id": cid, "text": text, "sender": handle, "members": members, "is_group": True,
                                            "guid": guid, "attachments": attachments or []})
            self.route(outs, ai=self.model_name(), ai_texts=self.model_written(mark))
            if not private:
                self.refresh_boards(cid)
            self.save()
            return outs

    def external_reaction(self, cid, handle, members, target, kind, removed):
        with self.lock:
            self.bot.on_message({"kind": "reaction", "chat_id": cid, "sender": handle, "is_group": True, "members": members,
                                 "target": target, "reaction": kind, "removed": removed, "text": ""})
            r = self.db["rooms"].get(cid)
            m = next((x for x in reversed(r["messages"]) if x.get("guid") == target), None) if r else None
            if m is not None:
                name = self.bot.name_of(handle) or handle
                with self.db_lock:
                    if removed:
                        m.setdefault("reactions", {}).pop(name, None)
                    else:
                        m.setdefault("reactions", {})[name] = kind
                self.refresh_boards(cid)

    def me(self, u):
        rooms = []
        demo = [c for c, r in self.db["rooms"].items() if r.get("demo") and c not in u["rooms"]]
        for code in list(u["rooms"]) + demo:
            r = self.db["rooms"].get(code)
            if r:
                last = r["messages"][-1] if r["messages"] else None
                rooms.append({"code": code, "name": r["name"], "sample": r.get("sample", False), "members": len(r["members"]), "source": r.get("source"),
                              "demo": bool(r.get("demo")),
                              "last": {"who": last["who"], "text": (last["text"] or "")[:80], "at": last["at"]} if last else None})
        p = self.bot.profiles.get(u["name"], {})
        prefs = p.get("prefs", {})
        profile = {
            "classes": [" ".join(("%s %s-%s %s" % (c["days"], c["start"].strip(), c["end"].strip(), c.get("label", ""))).split()) for c in p.get("classes", [])],
            "exams": [" ".join(("%s %s %s" % (e["date"], e.get("time", ""), e.get("label", ""))).split()) for e in p.get("exams", [])],
            "busy": len(p.get("busy", [])), "budget": prefs.get("budget"), "diet": prefs.get("diet", []),
            "language": prefs.get("language"), "nicknames": prefs.get("nicknames", []), "venmo": prefs.get("venmo"),
            "dorm": prefs.get("dorm"), "birthday": p.get("birthday"), "hide_stats": bool(prefs.get("hide_stats")),
            "share_reasons": bool(p.get("share_reasons")), "hours": p.get("project", {}).get("hours_this_week"),
            "skills": p.get("project", {}).get("skills", []), "flights": len(p.get("flights", [])),
        }
        return {"me": {"id": u["id"], "name": u["name"]}, "rooms": rooms, "profile": profile,
                "inbox": self.db["inbox"].get(u["handle"], [])[-40:], "llm": self.llm_status()}

    # ---------- rooms ----------
    def new_code(self):
        while True:
            c = "".join(secrets.choice(CODE_CHARS) for _ in range(6))
            if c not in self.db["rooms"]:
                return c

    def create_room(self, u, name, sample=False):
        name = re.sub(r"\s+", " ", (name or "").strip())[:40] or "Squad"
        with self.lock:
            code = self.new_code()
            members = [u["handle"]] + (list(self.sample_handles) if sample else [])
            r = {"code": code, "name": name, "created": iso(self.now()), "owner": u["handle"], "members": members,
                 "messages": [], "seq": 0, "sample": sample}
            self.db["rooms"][code] = r
            u["rooms"].insert(0, code)
            self.event(r, "%s started %s" % (u["name"], name))
            self.bot.chat_state(code)["members"] = members
            self.save()
        return r

    def join_room(self, u, code):
        with self.lock:
            r = self.room(code)
            if r.get("sample") and r["owner"] != u["handle"]:
                raise ApiError(403, "Sample squads are private to whoever opened them. Open your own from the sidebar.")
            if r.get("source") and u["handle"] not in r["members"]:
                raise ApiError(403, "This squad lives in Discord. Type @squad board in that channel to open it here.")
            if u["handle"] not in r["members"]:
                r["members"].append(u["handle"])
                self.bot.chat_state(r["code"])["members"] = r["members"]
                self.event(r, "%s joined" % u["name"])
            if r["code"] not in u["rooms"]:
                u["rooms"].insert(0, r["code"])
            self.refresh_boards(r["code"])
            self.save()
        return r

    def sample_room(self, u):
        with self.lock:
            for code in u["rooms"]:
                r = self.db["rooms"].get(code)
                if r and r.get("sample"):
                    return r
            r = self.create_room(u, "Apartment 4B (sample)", sample=True)
            self.seed(r, u)
            self.refresh_boards(r["code"])
            self.save()
        return r

    def seed(self, r, u):
        """A believable afternoon in an apartment group chat, so the board has something real on it."""
        me = u["name"].split()[0]
        script = [
            (-300, "Priya", "ok real talk rent is due on the 1st, venmo me $640 by friday"),
            (-298, "Jake", "bet"),
            (-240, "Sam", "who ate my leftovers 💀"),
            (-238, "Jake", "not me lol"),
            (-200, "Priya", "heads up the landlord moved the inspection to monday 10am, clean your rooms"),
            (-199, "Sam", ("react", "emphasize", "landlord")),
            (-198, "Jake", ("react", "love", "landlord")),
            (-150, "Jake", "@squad sell IKEA desk $40, mini fridge $50 obo, desk lamp $10 pickup by sunday @ Tooker"),
            (-148, "Sam", "want #2"),
            (-120, "Priya", "@%s are you coming to movie night?" % me),
            (-90, "Jake", "@squad add oat milk, paper towels, dish soap"),
            (-60, "Sam", "does anyone have a vacuum i can borrow?"),
            (-35, "Jake", "anyone free this weekend??"),
            (-30, "Priya", "1"),
            (-28, "Sam", "1"),
        ]
        handle = {n: h for h, n in self.sample_handles.items()}
        for mins, who, text in script:
            self.offset = timedelta(minutes=mins)
            if isinstance(text, tuple):
                target = next((m for m in reversed(r["messages"]) if text[2] in (m["text"] or "")), None)
                if target:
                    self._react(r, handle[who], who, target, text[1])
                continue
            self._post(r, handle[who], text, model_label=False)
        self.offset = timedelta(0)

    def event(self, r, text):
        with self.db_lock:
            r["seq"] += 1
            r["messages"].append({"id": r["seq"], "kind": "event", "who": "", "text": text, "at": iso(self.now())})

    def add_msg(self, r, kind, who, handle, text, **extra):
        with self.db_lock:
            r["seq"] += 1
            m = {"id": r["seq"], "kind": kind, "who": who, "handle": handle, "text": text, "at": iso(self.now()), "reactions": {}}
            m.update(extra)
            r["messages"].append(m)
            del r["messages"][:-2000]
            return m

    def refresh_boards(self, code=None):
        """Snapshot each member's board after the bot changes something (called while holding the bot lock)."""
        for r in ([self.db["rooms"][code]] if code else list(self.db["rooms"].values())):
            for h in r["members"]:
                if h.startswith("web:"):
                    name = self.bot.name_of(h) or h
                    b = self.board(r, name)
                    with self.db_lock:
                        self.boards[(r["code"], h)] = b

    # ---------- messages ----------
    def post(self, u, code, text, as_name=None):
        text = (text or "").strip()
        if not text:
            raise ApiError(400, "Type a message first.")
        if len(text) > 1500:
            raise ApiError(400, "That message is too long. Keep it under 1,500 characters.")
        if not self.allow("msg:" + u["id"], 25):
            raise ApiError(429, "You're sending messages too fast. Wait a few seconds and try again.")
        with self.lock:
            r = self.room(code, u)
            handle = u["handle"]
            if r.get("demo") and handle not in r["members"]:
                with self.db_lock:
                    r["members"].append(handle)
                    if code not in u["rooms"]:
                        u["rooms"].insert(0, code)
            if as_name and r.get("sample"):
                handle = next((h for h, n in self.sample_handles.items() if n == as_name), handle)
            try:
                with self.db_lock:
                    r["thinking"] = True
                new = self._post(r, handle, text)
            finally:
                with self.db_lock:
                    r["thinking"] = False
            self.refresh_boards(r["code"])
            self.save()
        return new

    def _post(self, r, handle, text, model_label=True):
        name = self.bot.name_of(handle) or handle
        start = r["seq"]
        m = self.add_msg(r, "user", name, handle, text)
        m["guid"] = "%s-%d" % (r["code"], m["id"])
        if r.get("source"):  # say it in the Discord channel too
            for send in self.senders:
                send({"chat_id": r["code"], "text": "**%s** (on the web): %s" % (name, text), "relay": True}, None)
        mark = self.model_mark()
        outs = self.bot.on_message({"chat_id": r["code"], "text": text, "sender": handle, "members": r["members"],
                                    "is_group": True, "guid": m["guid"], "attachments": []})
        self.route(outs, ai=self.model_name() if model_label else None, ai_texts=self.model_written(mark))
        return [x for x in r["messages"] if x["id"] > start]

    def react(self, u, code, msg_id, kind, as_name=None):
        if kind not in REACTIONS:
            raise ApiError(400, "Unknown reaction.")
        with self.lock:
            r = self.room(code, u)
            m = next((x for x in r["messages"] if x["id"] == msg_id), None)
            if not m:
                raise ApiError(404, "That message is gone.")
            handle = u["handle"]
            if as_name and r.get("sample"):
                handle = next((h for h, n in self.sample_handles.items() if n == as_name), handle)
            self._react(r, handle, self.bot.name_of(handle) or handle, m, kind)
            self.refresh_boards(r["code"])
            self.save()
            return m

    def _react(self, r, handle, name, m, kind):
        with self.db_lock:
            removed = m.setdefault("reactions", {}).get(name) == kind
            if removed:
                m["reactions"].pop(name)
            else:
                m["reactions"][name] = kind
        if m.get("guid"):
            self.bot.on_message({"kind": "reaction", "chat_id": r["code"], "sender": handle, "is_group": True, "members": r["members"],
                                 "target": m["guid"], "reaction": kind, "removed": removed, "text": ""})

    def route(self, outs, code=None, ai=None, ai_texts=None):
        """ai is the model's name; ai_texts is what it wrote this turn (None = unknown, label everything when it was used)."""
        last_bot, last_cid = None, None
        written = [t.strip()[:60] for t in (ai_texts or []) if t and t.strip()]
        base_ai = ai
        for o in outs:
            ai = base_ai if base_ai and (ai_texts is None or any(w in (o.get("text") or "") for w in written)) else None
            is_dm = bool(o.get("dm")) or o.get("chat_id", "").startswith("dm:")
            if any(send(o, ai) for send in self.senders) and (is_dm or o.get("chat_id") not in self.db["rooms"]):
                continue  # delivered by Discord; chats mirrored on the website still get a copy below
            if o.get("dm"):
                self.deliver_private(o["dm"], o.get("text", ""), ai)
                continue
            cid = o.get("chat_id", "")
            if cid.startswith("dm:"):
                if o.get("text"):
                    self.deliver_private(cid[3:], o["text"], ai)
                continue
            r = self.db["rooms"].get(cid)
            if not r:
                continue
            attach_to = last_bot if last_cid == cid else None
            if o.get("file"):
                path = o["file"]
                name = os.path.basename(path)
                if name.endswith(".speech.json"):
                    try:
                        att = {"voice": json.load(open(path, encoding="utf-8"))}
                    except (OSError, ValueError):
                        continue
                elif name.endswith(".ics"):
                    att = {"file": {"url": "/files/" + name, "kind": "invite", "name": name}}
                else:
                    continue
                if attach_to is not None:
                    with self.db_lock:
                        attach_to.update(att)
                else:
                    self.add_msg(r, "bot", "Squad Radar", "bot", "", **att)
                continue
            last_bot = self.add_msg(r, "bot", "Squad Radar", "bot", o.get("text", ""), **({"ai": ai} if ai else {}))
            last_cid = cid

    def deliver_private(self, handle, text, ai=None):
        """Private messages from the bot. Sample friends' DMs go to whoever owns that sample squad, labeled."""
        targets = []
        if handle in self.sample_handles:
            owners = {r["owner"] for r in self.db["rooms"].values() if r.get("sample") and handle in r["members"]}
            targets = [(o, self.sample_handles[handle]) for o in owners]
        else:
            targets = [(handle, None)]
        for h, to in targets:
            with self.db_lock:
                box = self.db["inbox"].setdefault(h, [])
                box.append({"id": (box[-1]["id"] + 1) if box else 1, "text": text, "at": iso(self.now()), "to": to, "ai": ai})
                del box[:-100]

    def setup(self, u, text):
        text = (text or "").strip()
        if not text:
            raise ApiError(400, "Type a setting, like: classes MW 10:30-11:45 CSE 310")
        if not self.allow("setup:" + u["id"], 30):
            raise ApiError(429, "Too many changes at once. Wait a moment and try again.")
        with self.lock:
            body = text if re.match(r"(?i)^@?squad\b", text) else "squad " + text
            self.deliver_private(u["handle"], "You: " + text)
            outs = self.bot.on_message({"chat_id": "dm:" + u["handle"], "text": body, "sender": u["handle"], "members": [u["handle"]],
                                        "is_group": False})
            self.route(outs)
            for c in u["rooms"]:
                if c in self.db["rooms"]:
                    self.refresh_boards(c)
            self.save()
            return [o.get("text", "") for o in outs]

    def tick(self):
        with self.lock:
            mark = self.model_mark()
            outs = self.bot.tick()
            if outs:
                self.route(outs, ai=self.model_name(), ai_texts=self.model_written(mark))
                self.save()
            self.refresh_boards()
            return outs

    # ---------- the website assistant: private to whoever asks, never posted in the chat ----------
    def assist(self, u, code, tool, text="", lang="", at=""):
        if not self.allow("assist:" + u["id"], 20):
            raise ApiError(429, "Too many requests at once. Wait a few seconds.")
        r = self.room(code, u)
        name = u["name"]
        msg = {"chat_id": r["code"], "members": r["members"], "sender": u["handle"], "is_group": True}
        with self.lock:
            st = self.bot.chat_state(r["code"])
            before = getattr(self.model, "ok", 0)
            saved = st.setdefault("settings", {}).get("voice")
            st["settings"]["voice"] = False  # the website reads results aloud itself
            try:
                if tool == "summary":
                    out = self.bot.cmd_tldr(msg, st, name, "tldr 24h")
                elif tool == "highlights":
                    out = self.bot.cmd_highlights(msg, st, name, text or "")
                elif tool == "mentions":
                    out = self.bot.cmd_mentions(msg, st, name, "")
                elif tool == "stats":
                    out = self.bot.cmd_stats(msg, st, name, "")
                elif tool == "translate":
                    if not getattr(self.bot.llm, "enabled", False):
                        raise ApiError(400, "Translation needs a language model key on the server (LLM_API_KEY in .env).")
                    from squad.llm import TRANSLATE_SYSTEM
                    lang = re.sub(r"[^A-Za-z ]", "", lang or "spanish")[:20] or "spanish"
                    res = self.bot.llm.chat("Target language: %s\nText: %s" % (lang, (text or "")[:2000]), system=TRANSLATE_SYSTEM, max_tokens=700)
                    if not res:
                        raise ApiError(502, "The model didn't answer. Try again in a moment.")
                    out = [{"text": res}]
                elif tool == "remind":
                    when = datetime.fromisoformat(at).replace(tzinfo=self.tz) if at else None
                    if not when or not (text or "").strip():
                        raise ApiError(400, "Add what to remind about and when.")
                    if when <= self.now():
                        raise ApiError(400, "Pick a time in the future.")
                    st.setdefault("reminders", []).append({"at": when.isoformat(), "chat_id": r["code"], "sent": False,
                                                           "text": "🔔 reminder from %s: %s" % (name, text.strip()[:200])})
                    self.bot.save()
                    self.refresh_boards(r["code"])
                    out = [{"text": "Reminder set for %s. It will post in the chat%s." % (
                        when.strftime("%a %b %d, %I:%M %p"), " and in Discord" if r.get("source") else "")}]
                else:
                    raise ApiError(400, "Unknown tool.")
            finally:
                st["settings"]["voice"] = saved
            used = getattr(self.model, "ok", 0) > before
        text_out = "\n".join(o.get("text", "") for o in out if o.get("text"))
        if self.bot.prefix and text_out.startswith(self.bot.prefix):
            text_out = text_out[len(self.bot.prefix):]
        text_out = re.sub(r"\n?reply \".*$", "", text_out).strip()
        from squad import voice as _voice
        return {"text": text_out, "speech": _voice.speakable(text_out), "ai": self.model_name() if used else None}

    # ---------- the board: everything the bot has organized in this squad ----------
    def board(self, r, me_name):
        st = self.bot.state.get(r["code"], {})
        now = self.now()
        b = {}
        poll = st.get("poll")
        if poll and poll.get("options"):
            opts = []
            for i, o in enumerate(poll["options"]):
                act = o.get("activity", {})
                opts.append({"start": o["start"], "end": o["end"], "what": act.get("what", ""), "where": act.get("where", ""),
                             "price": act.get("price", 0), "free_food": bool(act.get("free_food")),
                             "votes": sorted(n for n, v in poll["votes"].items() if v == i),
                             "missing": [m[0] for m in o.get("missing", [])]})
            plan = {"status": poll["status"], "options": opts, "winner": poll.get("winner"), "ins": poll.get("ins", []),
                    "outs": poll.get("outs", []), "members": poll.get("members", [])}
            if poll.get("winner") is not None:
                so = datetime.fromisoformat(opts[poll["winner"]]["start"])
                ics = "squad-%s.ics" % so.strftime("%Y%m%d-%H%M")
                if os.path.exists(os.path.join(self.files, ics)):
                    plan["invite"] = "/files/" + ics
                plan["past"] = so < now
            b["plan"] = plan
        b["reminders"] = sorted(({"at": x["at"], "text": x["text"]} for x in st.get("reminders", []) if not x.get("sent")),
                                key=lambda x: x["at"])[:4]
        owes = [x for x in st.get("owes", []) if not x.get("paid")]
        b["money"] = {"owes": [{"from": x["from"], "to": x["to"], "cents": x["cents"], "note": x.get("note", "")} for x in owes],
                      "settled": sum(1 for x in st.get("owes", []) if x.get("paid"))}
        if st.get("bill"):
            bl = st["bill"]
            b["money"]["bill"] = {"payer": bl.get("payer"), "items": [{"name": i.get("name"), "cents": i.get("cents"), "by": i.get("by", [])}
                                                                     for i in bl.get("items", [])]}
        if st.get("food"):
            f = st["food"]
            b["money"]["food"] = {"place": f.get("place"), "runner": f.get("runner"), "closes": f.get("closes"),
                                  "orders": [{"who": o["who"], "what": o["what"], "cents": o.get("cents", 0)} for o in f.get("orders", [])]}
        items = (st.get("market") or {}).get("items", {})
        b["market"] = [{"id": it["id"], "title": it["title"], "price": it["price"], "seller": it["seller"], "status": it["status"],
                        "hold": (it.get("hold") or {}).get("by"), "line": len(it.get("queue", [])), "pickup_by": it.get("pickup_by"),
                        "where": it.get("where"), "sold_to": it.get("sold_to")}
                       for it in sorted(items.values(), key=lambda x: int(x["id"]))]
        rd = st.get("rides")
        if rd and rd.get("status") != "done" or (rd and rd.get("assign")):
            b["rides"] = {"to": rd["to"], "status": rd["status"], "drivers": {d: v["seats"] for d, v in rd.get("drivers", {}).items()},
                          "riders": sorted(rd.get("riders", {})), "assign": rd.get("assign")}
        pr = st.get("project")
        if pr:
            b["project"] = {"name": pr.get("name"), "due": pr.get("due"),
                            "tasks": [{"name": t["name"], "hours": t.get("hours"), "owner": (pr.get("assign") or {}).get(t["name"]),
                                       "done": t["name"] in pr.get("done", {}), "deadline": (pr.get("deadlines") or {}).get(t["name"])}
                                      for t in pr.get("tasks", [])]}
        c = st.get("chores")
        if c:
            wk = now.strftime("%G-%V")
            done = c.get("done", {}).get(wk, [])
            b["chores"] = [{"job": j, "who": p, "done": j in done} for j, p in self.bot.chore_assign(c, now).items()]
        if st.get("groceries"):
            b["groceries"] = [{"item": g["item"], "by": g.get("by")} for g in st["groceries"]]
        hist = st.get("history", [])
        b["pins"] = [{"who": x["who"], "text": x["text"], "at": x["at"]} for x in hist if x.get("pinned")][-6:]
        saved, self.bot.llm = self.bot.llm, None  # the board ranks with rules; the model is saved for when people ask
        try:
            picks, _, _ = self.bot.highlights(st, me_name, "24h")
            mentions, _ = self.bot.mentions(st, me_name, "72h")
        finally:
            self.bot.llm = saved
        b["highlights"] = [{"who": x["who"], "text": x["text"], "at": x["at"], "tags": tags} for x, tags, _ in picks]
        b["mentions"] = [{"who": i["x"]["who"], "text": i["x"]["text"], "at": i["x"]["at"], "kind": i["kind"], "replied": i["replied"]}
                         for i in mentions][-6:]
        week = [x for x in hist if datetime.fromisoformat(x["at"]) >= now - timedelta(days=7)]
        counts = {}
        for x in week:
            if not self.bot._hidden(x["who"]):
                counts[x["who"]] = counts.get(x["who"], 0) + 1
        b["stats"] = sorted(({"name": n, "count": k} for n, k in counts.items()), key=lambda x: -x["count"])
        b["settings"] = {k: v for k, v in st.get("settings", {}).items()}
        return b

    def feed(self, u, code, after):
        r = self.room(code, u)
        board = self.boards.get((r["code"], u["handle"]))
        if board is None and self.lock.acquire(blocking=False):  # first look at a squad: build the board if the bot is free
            try:
                board = self.board(r, u["name"])
                self.boards[(r["code"], u["handle"])] = board
            finally:
                self.lock.release()
        with self.db_lock:
            msgs = [dict(m, reactions=dict(m.get("reactions") or {})) for m in r["messages"] if m["id"] > after][-300:]
            members = [{"name": self.bot.name_of(h) or h, "you": h == u["handle"], "sample": h in self.sample_handles}
                       for h in r["members"]]
            box = self.db["inbox"].get(u["handle"]) or [{"id": 0}]
            return {"room": {"code": r["code"], "name": r["name"], "sample": r.get("sample", False), "members": members,
                             "thinking": bool(r.get("thinking")), "source": r.get("source"), "demo": bool(r.get("demo"))},
                    "messages": msgs, "board": board or {}, "llm": self.llm_status(), "inbox_last": box[-1]["id"]}


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SquadRadar/1.0"

        def log_message(self, fmt, *args):
            if os.environ.get("SQUAD_LOG"):
                sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

        def _send(self, status, body, ctype="application/json; charset=utf-8", extra=None):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _json(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 20000:
                raise ApiError(413, "Request too large.")
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                raise ApiError(400, "Malformed request.")

        def _token(self):
            return self.headers.get("X-Squad-Token", "")

        def _static(self, rel):
            rel = rel.strip("/") or "index.html"
            path = os.path.normpath(os.path.join(STATIC, rel))
            if not path.startswith(STATIC) or not os.path.isfile(path):
                path = os.path.join(STATIC, "index.html")
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            cache = "no-cache" if path.endswith(".html") else "public, max-age=300"
            self._send(200, open(path, "rb").read(), ctype, {"Cache-Control": cache})

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path == "/api/health":
                    return self._send(200, {"ok": True, "llm": app.llm_status(), "discord": getattr(app, "discord_status", None),
                                            "squads": len(app.db["rooms"]), "demo_channels": sum(1 for r in app.db["rooms"].values() if r.get("demo")),
                                            "time": iso(app.now())})
                if u.path == "/api/me":
                    with app.db_lock:
                        return self._send(200, json.loads(json.dumps(app.me(app.user(self._token())))))
                m = re.match(r"^/api/rooms/([a-z0-9]{1,32})/feed$", u.path)
                if m:
                    after = int((q.get("after") or ["0"])[0] or 0)
                    return self._send(200, app.feed(app.user(self._token()), m.group(1), after))
                m = re.match(r"^/api/rooms/([a-z0-9]{1,32})$", u.path)
                if m:
                    r = app.room(m.group(1))
                    return self._send(200, {"code": r["code"], "name": r["name"], "members": len(r["members"]), "sample": r.get("sample", False)})
                m = re.match(r"^/files/([\w.-]+\.ics)$", u.path)
                if m:
                    path = os.path.join(app.files, m.group(1))
                    if os.path.isfile(path):
                        return self._send(200, open(path, "rb").read(), "text/calendar; charset=utf-8",
                                          {"Content-Disposition": 'attachment; filename="%s"' % m.group(1)})
                    raise ApiError(404, "That invite is gone.")
                if u.path.startswith("/api/"):
                    raise ApiError(404, "Unknown endpoint.")
                if u.path.startswith("/static/"):
                    return self._static(u.path[len("/static/"):])
                return self._static("index.html")
            except ApiError as e:
                self._send(e.status, {"error": e.message})

        def do_POST(self):
            u = urlparse(self.path)
            try:
                body = self._json()
                if u.path == "/api/join":
                    if not app.allow("join:" + self.client_address[0], 15):
                        raise ApiError(429, "Too many sign-ins from this network. Try again in a minute.")
                    token, user = app.join(body.get("name"))
                    return self._send(200, {"token": token, "me": {"id": user["id"], "name": user["name"]}})
                if u.path == "/api/link":
                    if not app.allow("link:" + self.client_address[0], 20):
                        raise ApiError(429, "Too many tries. Wait a minute and try again.")
                    token, user, room = app.use_link(body.get("code"))
                    return self._send(200, {"token": token, "me": {"id": user["id"], "name": user["name"]}, "room": room})
                user = app.user(self._token())
                if u.path == "/api/rooms":
                    r = app.create_room(user, body.get("name"))
                    return self._send(200, {"code": r["code"], "name": r["name"]})
                if u.path == "/api/rooms/sample":
                    r = app.sample_room(user)
                    return self._send(200, {"code": r["code"], "name": r["name"]})
                if u.path == "/api/me/setup":
                    replies = app.setup(user, body.get("text"))
                    return self._send(200, {"replies": replies})
                m = re.match(r"^/api/rooms/([a-z0-9]{1,32})/assist$", u.path)
                if m:
                    return self._send(200, app.assist(user, m.group(1), body.get("tool"), body.get("text", ""), body.get("lang", ""), body.get("at", "")))
                m = re.match(r"^/api/rooms/([a-z0-9]{1,32})/(join|messages|react)$", u.path)
                if m:
                    code, action = m.groups()
                    if action == "join":
                        r = app.join_room(user, code)
                        return self._send(200, {"code": r["code"], "name": r["name"]})
                    if action == "messages":
                        new = app.post(user, code, body.get("text"), body.get("as"))
                        return self._send(200, {"messages": new})
                    msg = app.react(user, code, int(body.get("id") or 0), body.get("kind"), body.get("as"))
                    return self._send(200, {"message": msg})
                raise ApiError(404, "Unknown endpoint.")
            except ApiError as e:
                self._send(e.status, {"error": e.message})
            except Exception as e:  # noqa: BLE001
                sys.stderr.write("error: %r\n" % e)
                self._send(500, {"error": "Something broke on the server. Try again; if it keeps happening, reload the page."})

    return Handler


def keep_awake(every=600):
    """Free hosts (Render) put a web service to sleep after ~15 idle minutes, which would also take the Discord bot
    offline. Visiting our own health page every 10 minutes counts as traffic and keeps it up."""
    import urllib.request
    url = public_url() + "/api/health"
    while True:
        time.sleep(every)
        try:
            urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "squad-radar-keepalive"}), timeout=20).read()
        except Exception as e:  # noqa: BLE001
            sys.stderr.write("keep-awake ping failed: %r\n" % e)


def ticker(app, every=20):
    while True:
        time.sleep(every)
        try:
            app.tick()
        except Exception as e:  # noqa: BLE001
            sys.stderr.write("tick error: %r\n" % e)


def main():
    run.load_env()
    data = os.environ.get("SQUAD_DATA") or os.path.join(HERE, "data-web")
    app = App(data)
    if app.model.enabled:
        threading.Thread(target=app.model.pick_model, daemon=True).start()
    threading.Thread(target=ticker, args=(app,), daemon=True).start()
    if os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("SQUAD_KEEPALIVE"):
        threading.Thread(target=keep_awake, daemon=True).start()
        print("Keep-awake on: pinging %s/api/health every 10 minutes" % public_url())
    if os.environ.get("DISCORD_TOKEN"):
        try:
            from discord_bridge import DiscordBridge
            DiscordBridge(app, os.environ["DISCORD_TOKEN"], os.environ.get("DISCORD_CHANNELS", "")).start()
        except ImportError:
            print("DISCORD_TOKEN is set but the Discord library isn't installed. Run: pip3 install -r requirements.txt")
    port = int(os.environ.get("PORT", "8080"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(app))
    st = app.llm_status()
    print("Squad Radar web app on http://localhost:%d  (data: %s, model: %s)" % (
        port, data, ("%s %s" % (st.get("provider"), st.get("model") or "(picking...)")) if st.get("enabled") else "off, add a key to .env (see .env.example)"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
