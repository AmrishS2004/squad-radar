"""Group radar tests: highlights, tagged summaries, pins/tapbacks, remind, voice notes, participation stats,
Squad Wrapped, unanswered-question bumps, welcome-back DMs. Fake clock, fake Grok, fake voice."""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from squad import voice  # noqa: E402
from squad.bot import SquadBot  # noqa: E402

TZ = ZoneInfo("America/Phoenix")
fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  -> " + str(detail)[:600]))
    fails += 0 if cond else 1


class FakeGrok:
    enabled = True

    def __init__(self, picks=None):
        self.picks = picks
        self.calls = []

    def chat(self, user, system="", json_mode=False, max_tokens=300):
        self.calls.append(system[:30])
        if "Translate" in system or "translat" in system.lower():
            return "[tamil] " + user.split("Text: ", 1)[1][:40]
        if "tagged" in system:
            return "- tell Priya if you're bringing the speaker"
        if "Summarize" in system:
            return "- rent due nov 1\n- movie sat 7pm"
        return "ok"

    def json(self, user, system):
        self.calls.append("json:" + system[:20])
        if "what matters" in system:
            return self.picks
        return None


def profiles():
    out = {}
    for f in sorted(os.listdir(os.path.join(ROOT, "profiles"))):
        p = json.load(open(os.path.join(ROOT, "profiles", f)))
        out[p["name"]] = p
    return out


def new_bot(now, llm=None):
    clock = {"now": now}
    tmp = tempfile.mkdtemp()
    b = SquadBot(profiles(), json.load(open(os.path.join(ROOT, "data", "events.json"))), TZ, lambda: clock["now"],
                 state_path=os.path.join(tmp, "s.json"), llm=llm, outdir=tmp)
    b.profile_dir = os.path.join(tmp, "profiles")
    spoken = []

    def fake_tts(text, path, lang):
        spoken.append((text, lang))
        open(path, "w").write("audio")
        return path
    b.tts = fake_tts
    return b, clock, spoken


MEMBERS = ["me", "+14805550101", "+14805550102", "+14805550103"]
H = {"Amrish": "me", "Priya": "+14805550101", "Jake": "+14805550102", "Sam": "+14805550103"}
n_guid = [0]


def send(b, clock, who, text, mins=0, chat="g"):
    clock["now"] += timedelta(minutes=mins)
    n_guid[0] += 1
    return b.on_message({"chat_id": chat, "text": text, "sender": H[who], "members": MEMBERS, "is_group": True,
                         "attachments": [], "guid": "G%d" % n_guid[0]})


def react(b, who, guid, kind, removed=False, chat="g"):
    return b.on_message({"kind": "reaction", "chat_id": chat, "sender": H[who], "is_group": True, "members": MEMBERS,
                         "target": guid, "reaction": kind, "removed": removed, "text": ""})


def dm(b, who, text):
    return b.on_message({"chat_id": "dm-" + who, "text": text, "sender": H[who], "members": [H[who]], "is_group": False})


def T(outs):
    return "\n".join(o.get("text", "") for o in outs)


def busy_day(b, clock):
    """A realistic Friday in an apartment group chat."""
    script = [
        ("Jake", "lmaooo", 0), ("Sam", "💀💀", 1), ("Priya", "ok real talk rent is due nov 1, venmo me $640 by friday", 3),
        ("Jake", "bet", 1), ("Sam", "who ate my leftovers", 20), ("Jake", "not me lol", 2),
        ("Priya", "heads up the landlord moved the inspection to tuesday 10am, clean your rooms", 40),
        ("Jake", "noooo", 1), ("Sam", "haha", 1),
        ("Priya", "Amrish are you bringing the speaker saturday?", 30),
        ("Jake", "https://docs.google.com/spreadsheets/d/abc grocery sheet", 10),
        ("Sam", "ok", 5), ("Jake", "let's do tacos at 7pm tonight, i booked a table for 4", 30),
        ("Sam", "yes", 1), ("Priya", "lol", 1),
    ]
    guids = {}
    for who, text, mins in script:
        send(b, clock, who, text, mins)
        guids[text] = "G%d" % n_guid[0]
    return guids


# ---------- voice helpers ----------
s = voice.speakable("✨ highlights:\n1. ⏰ Priya · 2:14pm: \"rent $640\" ❤️2\n- see https://x.com #3 at 7pm")
check("speakable strips emoji, reads #3 and 7pm out loud, drops links",
      "✨" not in s and "❤" not in s and "number 3" in s and "7 P M" in s and "a link" in s and "2 14 P M" in s, s)
fake_voices = [("Samantha", "en_US"), ("Ava (Premium)", "en_US"), ("Vani", "ta_IN"), ("Lekha", "hi_IN"), ("Paulina", "es_MX")]
check("voice picker: best English voice, Tamil, Spanish, none for missing language",
      voice.pick_voice("english", fake_voices) == "Ava (Premium)" and voice.pick_voice("tamil", fake_voices) == "Vani"
      and voice.pick_voice("spanish", fake_voices) == "Paulina" and voice.pick_voice("korean", fake_voices) is None)

# ---------- highlights ----------
start = datetime(2026, 10, 30, 9, 0, tzinfo=TZ)  # a Friday
b, clock, spoken = new_bot(start)
guids = busy_day(b, clock)
out = T(send(b, clock, "Amrish", "@squad highlights", 5))
check("highlights finds rent, the inspection change, the tacos decision and the question for you", all(k in out for k in (
    "rent is due", "inspection", "tacos at 7pm", "speaker")), out)
check("highlights skip banter", not any(k in out for k in ("lmaooo", "💀💀", "\"bet\"", "who ate", "noooo")), out)
check("highlight lines carry category tags", "⏰" in out and "💸" in out and "🙋" in out, out)
check("highlights offer follow ups", "remind #1" in out and "pin #2" in out, out)

out = T(send(b, clock, "Amrish", "@squad highlights money", 1))
check("filter: money only", "rent" in out and "inspection" not in out and "tacos" not in out, out)
out = T(send(b, clock, "Amrish", "@squad what did we decide", 1))
check("decision log", "decisions" in out and "tacos" in out and "rent" not in out, out)

# ---------- remind #N ----------
send(b, clock, "Amrish", "@squad highlights", 1)
first = b.state["g"]["last_hl"][0]
out = T(send(b, clock, "Amrish", "remind #1 tomorrow", 1))
check("remind #N sets a reminder", "tomorrow at 9am" in out and b.state["g"]["reminders"], out)
clock["now"] = clock["now"].replace(hour=9, minute=1) + timedelta(days=1)
fired = T(b.tick())
check("the reminder fires with the original message", "🔔 reminder for Amrish" in fired, fired)
clock["now"] = start + timedelta(hours=4)

# ---------- tapbacks and pins ----------
react(b, "Jake", guids["heads up the landlord moved the inspection to tuesday 10am, clean your rooms"], "love")
react(b, "Sam", guids["heads up the landlord moved the inspection to tuesday 10am, clean your rooms"], "emphasize")
react(b, "Jake", "nope", "love")  # unknown message: ignored quietly
x = [m for m in b.state["g"]["history"] if "inspection" in m["text"]][0]
check("tapbacks are recorded and ‼️ pins", x["reactions"] == {"Jake": "love", "Sam": "emphasize"} and x["pinned"], x)
check("tapbacks never post anything in the chat", react(b, "Priya", guids["lmaooo"], "laugh") == [])
out = T(send(b, clock, "Amrish", "@squad pins", 1))
check("pins list shows the ‼️ message with its reactions", "inspection" in out and "❤️" in out and "‼️" in out, out)
react(b, "Sam", x["guid"], "emphasize", removed=True)
check("removing ‼️ unpins", not x["pinned"] and "Sam" not in x["reactions"], x)
send(b, clock, "Jake", "the wifi password is sundevils2026", 1)
out = T(send(b, clock, "Priya", "📌", 1))
check("replying 📌 pins the message before it", "pinned Jake's" in out and [m for m in b.state["g"]["history"] if "wifi" in m["text"]][0]["pinned"], out)
out = T(send(b, clock, "Amrish", "@squad pins", 1))
send(b, clock, "Amrish", "unpin #1", 0)
check("unpin #N from the pins list", not [m for m in b.state["g"]["history"] if "wifi" in m["text"]][0]["pinned"])

# ---------- mentions ----------
out = T(send(b, clock, "Amrish", "@squad mentions", 1))
check("mentions: the direct question to you, flagged as waiting", "asked you" in out and "speaker" in out and "no reply yet" in out, out)
check("mentions: nothing that doesn't tag you", "rent" not in out, out)
dm(b, "Jake", "squad nickname jakey, j-man")
send(b, clock, "Priya", "@jakey can you grab paper towels", 1)
send(b, clock, "Sam", "@everyone house meeting sunday 6pm", 1)
out = T(send(b, clock, "Jake", "@squad who tagged me", 1))
check("nicknames count as tags, @everyone counts too", "paper towels" in out and "@everyone" in out and "house meeting" in out, out)
out = T(send(b, clock, "Sam", "@squad mentions", 1))
check("Sam's own @everyone is not Sam's tag", "house meeting" not in out, out)
send(b, clock, "Amrish", "yeah i'll bring it", 1)
out = T(send(b, clock, "Amrish", "@squad mentions", 1))
check("once you reply, it's no longer waiting", "no reply yet" not in out and "speaker" in out, out)

# ---------- voice ----------
out = send(b, clock, "Amrish", "@squad voice highlights", 1)
check("voice note: text + audio file", any(o.get("file", "").endswith(".m4a") for o in out) and "🔊 highlights" in T(out), out)
check("voice note reads the highlights without emoji or the reply footer",
      spoken and "rent" in spoken[-1][0] and "remind" not in spoken[-1][0].lower() and "⏰" not in spoken[-1][0], spoken[-1:])
out = send(b, clock, "Amrish", "@squad read that", 1)
check("read that: reads the last message from someone else", "Amrish" not in spoken[-1][0].split(" said")[0] and "said" in spoken[-1][0], spoken[-1:])
out = send(b, clock, "Amrish", "@squad voice gm squad, leaving in 10", 1)
check("voice any text", "leaving in 10" in spoken[-1][0], spoken[-1:])
nogrok = len(spoken)
out = T(send(b, clock, "Amrish", "@squad voice tldr in tamil", 1))
check("no Grok key: a Tamil request falls back to English instead of failing", spoken[-1][1] == "english" and len(spoken) == nogrok + 1, spoken[-1:])
send(b, clock, "Amrish", "@squad voice on", 1)
out = send(b, clock, "Amrish", "@squad mentions", 1)
check("voice on: summaries come with a voice note too", any(o.get("file") for o in out), out)
send(b, clock, "Amrish", "@squad voice off", 1)
b.tts = lambda text, path, lang: None
out = T(send(b, clock, "Amrish", "@squad voice tldr", 1))
check("without a Mac voice it says so", "need the bot running on a Mac" in out, out)

# with Grok: translated voice note in the listener's language, Grok-picked highlights
g = FakeGrok()
b2, clock2, spoken2 = new_bot(start, llm=g)
guids2 = busy_day(b2, clock2)
dm(b2, "Priya", "squad language tamil")
send(b2, clock2, "Priya", "@squad voice tldr", 5)
check("voice tldr uses your language: translated first, Tamil voice", spoken2 and spoken2[-1][1] == "tamil" and spoken2[-1][0].startswith("[tamil]"), spoken2[-1:])
out = T(send(b2, clock2, "Amrish", "@squad tldr voice", 1))
check("\"tldr voice\" also works", "🔊 tldr" in out, out)
g.picks = {"picks": [{"n": 99, "why": "bad"}, {"n": "x"}]}
out = T(send(b2, clock2, "Amrish", "@squad highlights", 1))
check("bad Grok picks fall back to the rule ranking", "inspection" in out and "rent" in out and "←" not in out, out)
g.picks = {"picks": [{"n": 2, "why": "your rent money"}, {"n": 1, "why": "inspection moved"}]}
out = T(send(b2, clock2, "Amrish", "@squad highlights", 1))
check("Grok picks the order and says why", "← your rent money" in out or "← inspection moved" in out, out)
out = T(send(b2, clock2, "Amrish", "@squad mentions", 1))
check("mentions with Grok add a to-do", "your to-do" in out and "speaker" in out, out)

# ---------- participation ----------
b3, clock3, _ = new_bot(start)
busy_day(b3, clock3)
for i in range(4):  # quick back-and-forth so there's a fastest replier
    send(b3, clock3, "Jake", "wait what time", 30)
    send(b3, clock3, "Sam", "like 8", 1)
out = T(send(b3, clock3, "Priya", "@squad stats", 1))
check("stats: bars and percentages for everyone who talked", "Jake" in out and "▓" in out and "%" in out, out)
check("stats: fastest replier and convo starter", "fastest replier: Sam" in out and "starts the most convos" in out, out)
check("stats: Amrish hasn't talked here, gentle check-in", "haven't heard from Amrish" in out, out)
dm(b3, "Sam", "squad hide my stats")
out = T(send(b3, clock3, "Priya", "@squad stats", 1))
check("opt out: Sam disappears from stats", "Sam" not in out and "keep their stats private" in out, out)
dm(b3, "Sam", "squad show my stats")
check("opt back in", "Sam" in T(send(b3, clock3, "Priya", "@squad stats week", 1)))

# ---------- wrapped ----------
send(b3, clock3, "Amrish", "anyone free this weekend??", 2)
for p in ("Priya", "Jake", "Sam"):
    send(b3, clock3, p, "1", 1)
for p in ("Amrish", "Priya", "Jake"):
    send(b3, clock3, p, "in", 1)
send(b3, clock3, "Amrish", "@squad sell desk $40", 1)
send(b3, clock3, "Jake", "want #1", 1)
send(b3, clock3, "Amrish", "sold #1 to jake", 1)
hist = b3.state["g"]["history"]
react(b3, "Priya", [m for m in hist if m["text"].startswith("let's do tacos")][0]["guid"], "love")
react(b3, "Sam", [m for m in hist if m["text"].startswith("let's do tacos")][0]["guid"], "emphasize")
out = T(send(b3, clock3, "Sam", "@squad wrapped", 1))
check("wrapped: totals, top yapper, message of the week by reactions", "Squad Wrapped" in out and "top yapper" in out
      and "message of the week" in out and "tacos" in out.split("message of the week")[1].split("\n")[0], out)
check("wrapped: counts plans and sales from the week", "plans locked: 1" in out and "items sold: 1" in out, out)
check("wrapped: emoji of the week", "💀" in out, out)
send(b3, clock3, "Sam", "@squad wrapped on", 1)
clock3["now"] = datetime(2026, 11, 1, 20, 5, tzinfo=TZ)  # Sunday 8pm
check("wrapped posts itself Sunday 8pm when on", "Squad Wrapped" in T(b3.tick()))
check("only once", "Squad Wrapped" not in T(b3.tick()))

# ---------- unanswered question bumps ----------
b4, clock4, _ = new_bot(datetime(2026, 10, 30, 10, 0, tzinfo=TZ))
send(b4, clock4, "Sam", "does anyone have an hdmi cable i can borrow?", 0)
clock4["now"] += timedelta(hours=1)
check("no bump before 3h", "bumping" not in T(b4.tick()))
clock4["now"] += timedelta(hours=2, minutes=5)
out = T(b4.tick())
check("bump after 3h of silence", "bumping Sam's question" in out and "hdmi" in out, out)
check("only bumped once", "bumping" not in T(b4.tick()))
send(b4, clock4, "Jake", "lol why is this so quiet", 1)
send(b4, clock4, "Jake", "can someone send me the lease pdf?", 1)
send(b4, clock4, "Priya", "sent!", 2)
clock4["now"] += timedelta(hours=4)
check("answered questions are never bumped", "lease" not in T(b4.tick()))
send(b4, clock4, "Sam", "who's coming to the game friday?", 1)
clock4["now"] = clock4["now"].replace(hour=23)
check("no bumps late at night", "bumping" not in T(b4.tick()))
send(b4, clock4, "Sam", "@squad bumps off", 0)
clock4["now"] = clock4["now"] + timedelta(hours=10)
check("bumps can be turned off", "bumping" not in T(b4.tick()))

# ---------- welcome back ----------
b5, clock5, _ = new_bot(datetime(2026, 10, 30, 9, 0, tzinfo=TZ))
send(b5, clock5, "Amrish", "@squad welcome back on", 0)
send(b5, clock5, "Sam", "morning", 0)
clock5["now"] += timedelta(hours=1)
send(b5, clock5, "Priya", "ok real talk rent is due nov 1, venmo me $640 by friday", 3)
send(b5, clock5, "Jake", "heads up the landlord moved the inspection to tuesday 10am", 20)
for i in range(24):
    send(b5, clock5, "Jake" if i % 2 else "Priya", "random chat %d" % i, 10)
send(b5, clock5, "Priya", "@sam you still selling the bike?", 5)
check("no catch-up before 12h away", not [o for o in send(b5, clock5, "Jake", "yo", 1) if o.get("dm")])
clock5["now"] += timedelta(hours=10)
out = send(b5, clock5, "Sam", "wait what did i miss", 1)
dms = [o for o in out if o.get("dm")]
check("welcome back DM to the person who was away", dms and dms[0]["dm"] == H["Sam"] and "you missed" in dms[0]["text"], out)
check("the DM has what matters and the tag count", "rent" in dms[0]["text"] and "tagged 1 time" in dms[0]["text"], dms)
check("welcome back stays out of the group chat", not [o for o in out if not o.get("dm") and "welcome back" in o.get("text", "")])

# ---------- help ----------
out = T(send(b5, clock5, "Sam", "@squad help radar", 1))
check("help radar", "highlights" in out and "mentions" in out and "pins" in out, out)
check("help voice / stats", "voice tldr" in T(send(b5, clock5, "Sam", "@squad help voice", 1)) and "wrapped" in T(send(b5, clock5, "Sam", "@squad help stats", 1)))

print("\n%s (%d failed)" % ("ALL PASSED" if not fails else "SOME FAILED", fails))
sys.exit(1 if fails else 0)
