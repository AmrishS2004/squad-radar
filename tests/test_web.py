"""Web app tests: a real HTTP server on a random port, a fake model, a fake clock. Covers sign-in, squads, invites,
messages, the board, private setup, reactions and pins, voice notes, calendar files, model labels, limits, and saving."""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from zoneinfo import ZoneInfo

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

import web  # noqa: E402

TZ = ZoneInfo("America/Phoenix")
fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  -> " + str(detail)[:600]))
    fails += 0 if cond else 1


class FakeModel:
    enabled = True

    def __init__(self):
        self.calls = self.ok = 0

    def status(self):
        return {"enabled": True, "provider": "Grok (xAI)", "model": "grok-4-fast", "calls": self.calls, "ok": self.ok, "last_error": None}

    def chat(self, user, system="", json_mode=False, max_tokens=300):
        self.calls += 1
        self.ok += 1
        if "Summarize" in system:
            return "- boba sunday 5pm is locked\n- jake owes you for dinner"
        return "honestly tacos, it's always tacos 🌮"

    def json(self, user, system):
        self.calls += 1
        return None

    def vision_json(self, path, system):
        return None


clock = {"now": datetime(2026, 10, 30, 12, 0, tzinfo=TZ)}  # a Friday
data = tempfile.mkdtemp()
model = FakeModel()
app = web.App(data, llm=model, clock=lambda: clock["now"])
srv = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(app))
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % srv.server_address[1]


def call(method, path, body=None, token=None, raw=False):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "X-Squad-Token": token or ""})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            b = r.read()
            return r.status, (b if raw else json.loads(b)), dict(r.headers)
    except urllib.error.HTTPError as e:
        b = e.read()
        return e.code, (b if raw else json.loads(b or b"{}")), dict(e.headers)


# ---------- basics ----------
code, body, _ = call("GET", "/api/health")
check("health shows the model", code == 200 and body["llm"]["model"] == "grok-4-fast", body)
code, body, hdr = call("GET", "/r/abc123", raw=True)
check("invite links serve the app", code == 200 and b"/static/app.js" in body and "text/html" in hdr.get("Content-Type", ""))
code, body, _ = call("GET", "/static/../web.py", raw=True)
check("no path tricks: server code is never served", b"ThreadingHTTPServer" not in body)
code, body, hdr = call("GET", "/static/fonts/ibm-plex-sans-latin-400-normal.woff2", raw=True)
check("fonts are bundled", code == 200 and hdr.get("Content-Type") == "font/woff2")
code, body, _ = call("GET", "/api/me")
check("no token: 401 with a clear message", code == 401 and "Enter your name" in body["error"])
code, body, _ = call("POST", "/api/join", {"name": "x"})
check("names need 2+ letters", code == 400)

_, a, _ = call("POST", "/api/join", {"name": "amrish"})
_, p, _ = call("POST", "/api/join", {"name": "Priya"})
A, P = a["token"], p["token"]
check("join gives a token and a clean name", a["me"]["name"] == "Amrish" and A)
check("a real Priya doesn't collide with sample Priya", p["me"]["name"] == "Priya 2", p)

# ---------- squads and invites ----------
_, r, _ = call("POST", "/api/rooms", {"name": "Apt 4B"}, A)
C = r["code"]
code, body, _ = call("GET", "/api/rooms/%s/feed" % C, token=P)
check("not a member yet: 403", code == 403 and "invite link" in body["error"])
code, body, _ = call("GET", "/api/rooms/%s" % C)
check("invite preview shows the squad name only", body == {"code": C, "name": "Apt 4B", "members": 1, "sample": False}, body)
call("POST", "/api/rooms/%s/join" % C, {}, P)
_, f, _ = call("GET", "/api/rooms/%s/feed" % C, token=A)
check("join shows up as an event", any(m["kind"] == "event" and "Priya 2 joined" in m["text"] for m in f["messages"]), f["messages"])
check("members listed", sorted(m["name"] for m in f["room"]["members"]) == ["Amrish", "Priya 2"])
code, body, _ = call("POST", "/api/rooms/zzzzzz/join", {}, A)
check("bad code: 404 with direction", code == 404 and "Check the link" in body["error"])

# ---------- private setup ----------
_, s, _ = call("POST", "/api/me/setup", {"text": "classes MWF 10:00-11:50 CSE 310"}, P)
check("private setup replies privately", any("saved" in x for x in s["replies"]), s)
call("POST", "/api/me/setup", {"text": "budget 12"}, P)
_, me, _ = call("GET", "/api/me", token=P)
check("profile shows what was saved", me["profile"]["classes"] == ["MWF 10:00-11:50 CSE 310"] and me["profile"]["budget"] == 12, me["profile"])
check("private messages land in your own inbox", any("saved" in x["text"] for x in me["inbox"]))
_, f, _ = call("GET", "/api/rooms/%s/feed" % C, token=A)
check("nothing private shows in the squad", "CSE 310" not in json.dumps(f) and "budget" not in json.dumps(f["messages"]))
_, meA, _ = call("GET", "/api/me", token=A)
check("someone else's inbox stays theirs", not meA["inbox"], meA["inbox"])

# ---------- plans ----------
_, m, _ = call("POST", "/api/rooms/%s/messages" % C, {"text": "anyone free this weekend??"}, A)
check("posting returns the bot's reply too", any(x["kind"] == "bot" and "reply 1 or 2" in x["text"] for x in m["messages"]), m)
_, f, _ = call("GET", "/api/rooms/%s/feed" % C, token=A)
plan = f["board"]["plan"]
check("board: the vote, with options", plan["status"] == "open" and len(plan["options"]) == 2, plan)
check("Priya's class time is avoided without saying what it is", all("CSE" not in json.dumps(o) for o in plan["options"]))
call("POST", "/api/rooms/%s/messages" % C, {"text": "1"}, A)
call("POST", "/api/rooms/%s/messages" % C, {"text": "1"}, P)
call("POST", "/api/rooms/%s/messages" % C, {"text": "in"}, A)
_, m, _ = call("POST", "/api/rooms/%s/messages" % C, {"text": "in"}, P)
locked = [x for x in m["messages"] if "locked" in (x["text"] or "")]
check("plan locks with a calendar invite attached", locked and locked[0].get("file", {}).get("kind") == "invite", m["messages"])
code, ics, hdr = call("GET", locked[0]["file"]["url"], raw=True)
check("invite downloads as a calendar file", code == 200 and b"BEGIN:VEVENT" in ics and "text/calendar" in hdr["Content-Type"])
_, f, _ = call("GET", "/api/rooms/%s/feed" % C, token=A)
check("board: locked plan with who's going and the invite", f["board"]["plan"]["status"] == "locked" and f["board"]["plan"]["invite"]
      and sorted(f["board"]["plan"]["ins"]) == ["Amrish", "Priya 2"], f["board"]["plan"])
check("board: reminder queued", f["board"]["reminders"] and "2 hours till" in f["board"]["reminders"][0]["text"])

# ---------- model labels ----------
_, m, _ = call("POST", "/api/rooms/%s/messages" % C, {"text": "@squad what should we eat tonight"}, P)
ai = [x for x in m["messages"] if x["kind"] == "bot"]
check("model-written replies are labeled with the model", ai and ai[0].get("ai") == "grok-4-fast" and "tacos" in ai[0]["text"], m)
_, m, _ = call("POST", "/api/rooms/%s/messages" % C, {"text": "@squad add oat milk"}, P)
check("rule-based replies are not labeled", not [x for x in m["messages"] if x.get("ai")], m)
_, h, _ = call("GET", "/api/health")
check("health counts answers so you can see the model is live", h["llm"]["ok"] >= 1 and h["llm"]["live"], h)

# ---------- money, market, chores on the board ----------
call("POST", "/api/rooms/%s/messages" % C, {"text": "@squad bill tax 8% tip 18%\nAmrish: pasta 16\nPriya 2: burger 14\nshared: fries 8"}, A)
call("POST", "/api/rooms/%s/messages" % C, {"text": "@squad sell desk $40, lamp $10 pickup by sunday @ Tooker"}, P)
call("POST", "/api/rooms/%s/messages" % C, {"text": "want #1"}, A)
call("POST", "/api/rooms/%s/messages" % C, {"text": "@squad chores dishes, trash"}, A)
_, f, _ = call("GET", "/api/rooms/%s/feed" % C, token=A)
b = f["board"]
check("board: who owes who", b["money"]["owes"] and b["money"]["owes"][0]["from"] in ("Priya 2", "Amrish"), b["money"])
check("board: market with the hold", [x["hold"] for x in b["market"]] == ["Amrish", None], b["market"])
check("board: chores and groceries", len(b["chores"]) == 2 and b["groceries"][0]["item"] == "oat milk", (b.get("chores"), b.get("groceries")))
check("board: who's talking", {x["name"] for x in b["stats"]} == {"Amrish", "Priya 2"}, b["stats"])

# ---------- reactions and pins ----------
_, m, _ = call("POST", "/api/rooms/%s/messages" % C, {"text": "wifi password is sundevils2026"}, P)
mid = m["messages"][0]["id"]
_, rr, _ = call("POST", "/api/rooms/%s/react" % C, {"id": mid, "kind": "emphasize"}, A)
check("reactions are saved on the message", rr["message"]["reactions"] == {"Amrish": "emphasize"}, rr)
_, f, _ = call("GET", "/api/rooms/%s/feed" % C, token=A)
check("‼️ pins it on the board", any("wifi" in x["text"] for x in f["board"]["pins"]), f["board"]["pins"])
call("POST", "/api/rooms/%s/react" % C, {"id": mid, "kind": "emphasize"}, A)
_, f, _ = call("GET", "/api/rooms/%s/feed" % C, token=A)
check("tapping it again removes it and unpins", not f["board"]["pins"] and not [x for x in f["messages"] if x["id"] == mid][0]["reactions"])
code, _, _ = call("POST", "/api/rooms/%s/react" % C, {"id": mid, "kind": "fire"}, A)
check("unknown reactions are refused", code == 400)

# ---------- voice notes and catching up ----------
_, m, _ = call("POST", "/api/rooms/%s/messages" % C, {"text": "@squad voice highlights"}, A)
v = [x for x in m["messages"] if x.get("voice")]
check("voice notes come back as speech the browser reads out", v and v[0]["voice"]["text"] and v[0]["voice"]["lang"] == "english", m)
check("voice note text is speakable (no emoji)", "📌" not in v[0]["voice"]["text"] and "⏰" not in v[0]["voice"]["text"])
_, m, _ = call("POST", "/api/rooms/%s/messages" % C, {"text": "@squad tldr"}, A)
check("tldr comes from the model and is labeled", any(x.get("ai") and "boba" in x["text"] for x in m["messages"]), m)
call("POST", "/api/rooms/%s/messages" % C, {"text": "@amrish can you bring the speaker?"}, P)
_, f, _ = call("GET", "/api/rooms/%s/feed" % C, token=A)
check("board: the question waiting on you", any("speaker" in x["text"] and not x["replied"] for x in f["board"]["mentions"]), f["board"]["mentions"])

# ---------- feed paging ----------
last = f["messages"][-1]["id"]
_, f2, _ = call("GET", "/api/rooms/%s/feed?after=%d" % (C, last), token=A)
check("feed only sends what's new", f2["messages"] == [], f2["messages"])

# ---------- reminders from the background ticker ----------
clock["now"] = datetime.fromisoformat(b["plan"]["options"][b["plan"]["winner"]]["start"]) - timedelta(hours=2, minutes=-1)
app.tick()
_, f, _ = call("GET", "/api/rooms/%s/feed?after=%d" % (C, last), token=A)
check("the ticker posts reminders into the squad", any("2 hours till" in (x["text"] or "") for x in f["messages"]), f["messages"])

# ---------- sample squad ----------
_, sr, _ = call("POST", "/api/rooms/sample", {}, A)
S = sr["code"]
_, f, _ = call("GET", "/api/rooms/%s/feed" % S, token=A)
check("sample squad comes with a conversation", len([x for x in f["messages"] if x["kind"] == "user"]) >= 10)
check("sample board has a vote, a market, pins, groceries and a question for you",
      f["board"]["plan"]["status"] == "open" and f["board"]["market"] and f["board"]["pins"] and f["board"]["groceries"]
      and any("movie night" in x["text"] for x in f["board"]["mentions"]), f["board"])
_, sr2, _ = call("POST", "/api/rooms/sample", {}, A)
check("opening it again returns the same sample squad", sr2["code"] == S)
_, m, _ = call("POST", "/api/rooms/%s/messages" % S, {"text": "1", "as": "Jake"}, A)
check("you can type as a sample friend", m["messages"][0]["who"] == "Jake", m)
_, m, _ = call("POST", "/api/rooms/%s/messages" % C, {"text": "hi", "as": "Jake"}, A)
check("but only in the sample squad", m["messages"][0]["who"] == "Amrish", m)
code, _, _ = call("POST", "/api/rooms/%s/join" % S, {}, P)
check("sample squads are private to their owner", code == 403)

# ---------- limits ----------
codes = [call("POST", "/api/rooms/%s/messages" % C, {"text": "spam %d" % i}, P)[0] for i in range(30)]
check("message rate limit kicks in", 429 in codes, codes)
code, body, _ = call("POST", "/api/rooms/%s/messages" % C, {"text": "x" * 1600}, A)
check("long messages are refused with a reason", code == 400 and "1,500" in body["error"])
capped = web.Budget(FakeModel(), 2)
outs = [capped.chat("hi") for _ in range(3)]
check("daily model budget caps calls", outs[:2] == [outs[0]] * 2 and outs[2] is None and not capped.enabled, outs)

# ---------- saving ----------
srv.shutdown()
app2 = web.App(data, llm=FakeModel(), clock=lambda: clock["now"])
check("squads, messages and people survive a restart", C in app2.db["rooms"] and app2.db["rooms"][C]["messages"]
      and "Priya 2" in app2.bot.profiles and app2.bot.state[C]["plan" if False else "poll"]["status"] == "locked")
shutil.rmtree(data, ignore_errors=True)

print("\n%s (%d failed)" % ("ALL PASSED" if not fails else "SOME FAILED", fails))
sys.exit(1 if fails else 0)
