"""Squad Radar tests: no network, no iMessage, fake clock. Run: python3 tests/test_squad.py"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from squad.agents import PersonalAgent  # noqa: E402
from squad.bot import SquadBot  # noqa: E402
from squad.ics import make_ics  # noqa: E402
from squad.imessage import IMessage, decode_attributed_body  # noqa: E402
from squad.llm import Grok  # noqa: E402
from squad.project import allocate, parse_tasks  # noqa: E402
from squad.timeparse import duration_minutes, window  # noqa: E402

TZ = ZoneInfo("America/Phoenix")
fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  -> " + str(detail)[:400]))
    fails += 0 if cond else 1


def profiles():
    out = {}
    for f in sorted(os.listdir(os.path.join(ROOT, "profiles"))):
        p = json.load(open(os.path.join(ROOT, "profiles", f)))
        out[p["name"]] = p
    return out


def new_bot(now, llm=None, tmp=None):
    clock = {"now": now}
    events = json.load(open(os.path.join(ROOT, "data", "events.json")))
    tmp = tmp or tempfile.mkdtemp()
    b = SquadBot(profiles(), events, TZ, lambda: clock["now"], state_path=os.path.join(tmp, "s.json"), llm=llm, outdir=tmp)
    b.profile_dir = os.path.join(tmp, "profiles")
    return b, clock


MEMBERS = ["me", "+14805550101", "+14805550102", "+14805550103"]
H = {"Amrish": "me", "Priya": "+14805550101", "Jake": "+14805550102", "Sam": "+14805550103"}


def send(b, who, text, chat="g1"):
    return b.on_message({"chat_id": chat, "text": text, "sender": H[who], "members": MEMBERS, "is_group": True})


def texts(outs):
    return "\n".join(o.get("text", "") for o in outs)


now = datetime(2026, 10, 29, 18, 0, tzinfo=TZ)

# ---------- time parsing ----------
s, e, label = window("anyone free this weekend??", now)
check("weekend window starts Fri 5pm", s == datetime(2026, 10, 30, 17, 0, tzinfo=TZ) and label == "this weekend", (s, label))
s, e, label = window("dinner tomorrow night", now)
check("tomorrow night window", s.day == 30 and s.hour == 17 and label == "tomorrow night", (s, label))
s, e, label = window("saturday", now)
check("weekday window", s.day == 31 and label == "Saturday", (s, label))
check("durations", duration_minutes("movie?") == 180 and duration_minutes("for 1.5 hours") == 90 and duration_minutes("") == 120)

# ---------- agents share only what they should ----------
prof = profiles()
priya = PersonalAgent(prof["Priya"], TZ)
r = priya.check(datetime(2026, 10, 30, 9, 0, tzinfo=TZ), datetime(2026, 10, 30, 9, 30, tzinfo=TZ))
check("private agent hides class name", not r["ok"] and r["reason"] == "a commitment", r)
r = priya.check(datetime(2026, 11, 1, 21, 0, tzinfo=TZ), datetime(2026, 11, 1, 23, 0, tzinfo=TZ))
check("late night before exam is a soft cost, reason hidden", r["ok"] and r["soft"] >= 6 and "BIO" not in json.dumps(r), r)
jake = PersonalAgent(prof["Jake"], TZ)
r = jake.check(datetime(2026, 10, 31, 13, 0, tzinfo=TZ), datetime(2026, 10, 31, 15, 0, tzinfo=TZ))
check("sharing agent gives its reason", not r["ok"] and r["reason"] == "work shift", r)
sam = PersonalAgent(prof["Sam"], TZ)
check("budget shared only as yes/no", sam.budget_ok(5) is True and sam.budget_ok(10) is False)

# ---------- hangout flow ----------
b, clock = new_bot(now)
out = send(b, "Priya", "anyone free this weekend??")
t = texts(out)
check("passive trigger plans", "reply 1 or 2" in t and "Movie Night" in t, t)
check("sam's $8 budget respected (no $10 party, no $15 dinner)", "costume party" not in t and "Mill Ave" not in t, t)
check("worked-around line respects privacy", "work shift" in t and "BIO" not in t and "church" not in t and "early start" in t, t)
check("passive trigger is rate limited", send(b, "Jake", "anyone free tonight?") == [])
send(b, "Jake", "1"); send(b, "Sam", "1")
out = send(b, "Amrish", "1")
check("majority vote picks option 1", "option 1 wins" in texts(out), texts(out))
check("random chatter isn't an RSVP", send(b, "Jake", "in class rn lol") == [])
send(b, "Jake", "in"); send(b, "Sam", "count me in")
out = send(b, "Priya", "I'm in!")
files = [o["file"] for o in out if o.get("file")]
check("locks and sends invite after majority 'in'", "locked" in texts(out) and files and open(files[0]).read().startswith("BEGIN:VCALENDAR"), out)
clock["now"] = datetime(2026, 10, 31, 17, 1, tzinfo=TZ)
out = b.tick()
check("reminder 2h before", "2 hours till" in texts(out), out)
check("reminder sent once", b.tick() == [])

out = send(b, "Sam", "@squad decide tacos, sushi, pizza")
check("decide picks one of the options", any(x in texts(out) for x in ("tacos", "sushi", "pizza")), out)
out = send(b, "Amrish", "@squad why")
check("why explains what agents shared", "free block" in texts(out) and "keeps reasons private" in texts(out), out)

# ---------- project flow ----------
clock["now"] = datetime(2026, 11, 1, 20, 0, tzinfo=TZ)
send(b, "Amrish", "@squad project CSE 310 pitch due nov 6")
send(b, "Amrish", "@squad tasks research 3h, intro slides 2h, demo video 3h, final deck 3h, presenting prep 1h")
send(b, "Sam", "dibs intro slides")
out = send(b, "Amrish", "dibs intro slides")
check("second dibs told first wins", "already called" in texts(out), out)
out = send(b, "Amrish", "@squad split")
pr = b.state["g1"]["project"]
check("first dibs wins in the split (claim order, not alphabetical)", pr["assign"]["intro slides"] == "Sam", pr["assign"])
check("split note keeps Priya's exam private", "Priya's part is due a day later to fit their week" in texts(out) and "BIO" not in texts(out), texts(out))
check("jake (shares) gets the honest note", "Jake has an exam" in texts(out), texts(out))
for p in ("Priya", "Jake", "Sam"):
    send(b, p, "ok")
out = send(b, "Amrish", "ok")
check("everyone accepted", "locked in" in texts(out), out)
out = send(b, "Jake", "dibs research")
out2 = send(b, "Priya", "ok")
check("swap after split needs the owner's ok", "wants 'research'" in texts(out) and pr["assign"]["research"] == "Jake", (texts(out), texts(out2)))
clock["now"] = datetime(2026, 11, 3, 21, 0, tzinfo=TZ)
out = send(b, "Sam", "done intro slides")
check("done celebrates with progress", "✅" in texts(out) and "1/5" in texts(out), out)
clock["now"] = datetime(2026, 11, 4, 4, 0, tzinfo=TZ)
check("no check-ins at 4am", b.tick() == [])
clock["now"] = datetime(2026, 11, 4, 9, 30, tzinfo=TZ)
out = b.tick()
t = texts(out)
check("one check-in per person due soon (Amrish, Sam), none for Thursday's parts",
      t.count("friendly check-in") == 2 and "Amrish, how's" in t and "Sam, how's" in t and "Priya, how's" not in t and "Jake, how's" not in t, t)
check("check-ins don't repeat", b.tick() == [])
out = send(b, "Amrish", "@squad receipts")
check("receipts list dated work", "intro slides (Nov 3)" in texts(out), out)

# ---------- privacy sweep over every message the bot sent ----------
log = json.dumps(b.state)
check("nothing private leaked into chat state", "BIO 181" not in log and "church" not in log and "PSY 101" not in log)

# ---------- DM setup ----------
b2, _ = new_bot(now)
dm = lambda text, h="+14805559999": b2.on_message({"chat_id": "dm1", "text": text, "sender": h, "members": [h], "is_group": False})
out = dm("squad i'm Lena")
check("dm setup creates profile", "Lena" in b2.profiles and "stays between us" in texts(out), out)
out = dm("squad classes MW 10:30-11:45 CSE 230\nexam nov 5 9am CSE 230 midterm\nbudget 12\nvegetarian\nhours 5\nskills coding, slides\nshare reasons on")
p = b2.profiles["Lena"]
check("dm setup parses everything", len(p["classes"]) == 1 and p["exams"][0]["date"] == "2026-11-05" and p["prefs"]["budget"] == 12
      and p["prefs"]["diet"] == ["vegetarian"] and p["share_reasons"] is True and p["project"]["skills"] == ["coding", "slides"], p)
check("dm ignores chatter without 'squad'", dm("hey what's up") == [])

# ---------- iMessage parsing ----------
blob = b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+\x0danyone free??\x86\x84"
check("decodes attributedBody text", decode_attributed_body(blob) == "anyone free??", decode_attributed_body(blob))
long = "x" * 300
blob2 = b"...NSString\x01\x94\x84\x01+\x81" + len(long).to_bytes(2, "little") + long.encode() + b"\x86"
check("decodes long attributedBody", decode_attributed_body(blob2) == long)

tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "chat.db")
c = sqlite3.connect(db)
c.executescript("""
CREATE TABLE message (ROWID INTEGER PRIMARY KEY, text TEXT, attributedBody BLOB, is_from_me INT, handle_id INT, date INT, associated_message_type INT);
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, display_name TEXT, chat_identifier TEXT);
CREATE TABLE chat_message_join (chat_id INT, message_id INT);
CREATE TABLE chat_handle_join (chat_id INT, handle_id INT);
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
INSERT INTO handle VALUES (1, '+14805550101'), (2, '+14805550102');
INSERT INTO chat VALUES (1, 'any;+;chat111', 'Squad', 'chat111'), (2, 'any;+;chat222', 'Family', 'chat222'), (3, 'any;-;+14805550101', '', '+14805550101');
INSERT INTO chat_handle_join VALUES (1,1),(1,2),(2,1),(2,2),(3,1);
INSERT INTO message VALUES (1, 'old', NULL, 0, 1, 1, 0);
INSERT INTO chat_message_join VALUES (1,1);
""")
c.commit()
im = IMessage(["Squad"], db=db)
im.start()
c.executescript("""
INSERT INTO message VALUES (2, 'anyone free?', NULL, 0, 1, 783000000000000000, 0);
INSERT INTO message VALUES (3, 'family stuff', NULL, 0, 2, 783000000000000000, 0);
INSERT INTO message VALUES (4, NULL, X'%s', 0, 2, 783000000000000000, 0);
INSERT INTO message VALUES (5, 'Loved “anyone free?”', NULL, 0, 2, 783000000000000000, 2000);
INSERT INTO message VALUES (6, 'squad setup', NULL, 0, 1, 783000000000000000, 0);
INSERT INTO chat_message_join VALUES (1,2),(2,3),(1,4),(1,5),(3,6);
""" % blob.hex())
c.commit()
got = im.fetch()
check("reads only allowlisted group + DMs, skips tapbacks and old messages",
      [g["text"] for g in got] == ["anyone free?", "anyone free??", "squad setup"], [g["text"] for g in got])
check("group flag and members", got[0]["is_group"] and not got[2]["is_group"] and set(im.members("any;+;chat111")) == {"+14805550101", "+14805550102", "me"})
check("no new messages on second fetch", im.fetch() == [])

# ---------- Grok client against a fake xAI server ----------
calls = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        b_ = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b_)

    def do_GET(self):
        calls.append(("GET", self.path, self.headers.get("Authorization")))
        self._send({"data": [{"id": "grok-2-image"}, {"id": "grok-4-fast-non-reasoning"}, {"id": "grok-3"}]})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        calls.append(("POST", self.path, body))
        user = body["messages"][-1]["content"]
        if body.get("response_format"):
            if "Project:" in user:
                content = json.dumps({"tasks": [{"name": "survey design", "hours": 2, "skill": "research"},
                                                {"name": "poster", "hours": "x", "skill": "design"}]})
            else:
                content = json.dumps({"intent": "plan", "when": "saturday"})
        else:
            content = "sushi?? bold. respect 🍣"
        self._send({"choices": [{"message": {"content": content}}]})


srv = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
import squad.llm as L  # noqa: E402
L.API = "http://127.0.0.1:%d/v1" % srv.server_address[1]
g = Grok(key="test-key")
check("picks a fast grok model, skips image models", g.pick_model() == "grok-4-fast-non-reasoning", g.model)
check("sends bearer key", calls[0][2] == "Bearer test-key")
b3, clock3 = new_bot(now, llm=g)
out = send(b3, "Jake", "@squad yo could we do something sat", chat="g3")
check("grok intent routes free-form text to the planner", "reply 1 or 2" in texts(out), out)
out = send(b3, "Sam", "@squad decide tacos, sushi", chat="g3")
check("grok adds a quip to decide", "respect" in texts(out), out)
send(b3, "Amrish", "@squad project psych survey due 11/12", chat="g3")
out = send(b3, "Amrish", "@squad suggest", chat="g3")
check("grok suggests tasks and bad hours are cleaned", "survey design" in texts(out) and b3.state["g3"]["project"]["tasks"][1]["hours"] == 2.0, out)
no_key = Grok(key="")
check("no key: llm disabled, no network", no_key.chat("hi") is None)
L.API = "http://127.0.0.1:9/v1"
dead = Grok(key="k", model="grok-3", timeout=2)
check("unreachable API returns None instead of crashing", dead.chat("hi") is None and dead.last_error)
srv.shutdown()

# ---------- misc ----------
ics = make_ics(os.path.join(tmp, "x.ics"), "Movie, night; fun", now, now + timedelta(hours=2), "Hayden Lawn")
body = open(ics).read()
check("ics escapes commas and semicolons", "SUMMARY:Movie\\, night\\; fun" in body and body.count("BEGIN:VEVENT") == 1)
r = subprocess.run([sys.executable, os.path.join(ROOT, "run.py"), "sim"], input="Priya: anyone free saturday?\nJake: 2\nquit\n",
                   capture_output=True, text=True, timeout=60, env=dict(os.environ, XAI_API_KEY=""))
check("terminal simulator runs", "reply 1 or 2" in r.stdout, r.stdout[-500:] + r.stderr[-500:])

print("\n%d failure(s)" % fails)
sys.exit(1 if fails else 0)
