#!/usr/bin/env python3
"""Squad Radar launcher.

  python3 run.py chats      list your recent iMessage chats (to find the group's name)
  python3 run.py check      check Messages access, profiles and the Grok key
  python3 run.py imessage   run the bot in the group chats named in .env
  python3 run.py sim        chat with the bot in your terminal as any of the sample friends
  python3 run.py demo       play the scripted demo and write demo_transcript.md
  python3 run.py llm        make one live call to the language model to prove it's connected
  python3 web.py            run the web app (http://localhost:8080)
"""
import glob
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from squad.bot import SquadBot  # noqa: E402
from squad.llm import Grok  # noqa: E402


def load_env(path=os.path.join(HERE, ".env")):
    """Read .env. A filled-in value always beats an empty one, so a blank line earlier in the file can't hide your key.
    Values already set in the real environment (Railway variables, your shell) win over the file."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8-sig"):
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.split(" #")[0].strip().strip('"').strip("'")
        if v and not os.environ.get(k):
            os.environ[k] = v


def load_profiles(d):
    out = {}
    for f in sorted(glob.glob(os.path.join(d, "*.json"))):
        p = json.load(open(f, encoding="utf-8"))
        out[p["name"]] = p
    return out


def make_bot(clock, state, llm=None, prefix=""):
    tz = ZoneInfo(os.environ.get("SQUAD_TZ", "America/Phoenix"))
    prof_dir = os.path.join(HERE, os.environ.get("SQUAD_PROFILES", "profiles"))
    events = json.load(open(os.path.join(HERE, "data", "events.json"), encoding="utf-8"))
    bot = SquadBot(load_profiles(prof_dir), events, tz, clock, state_path=state, llm=llm,
                   owner=os.environ.get("SQUAD_OWNER", "me"), outdir=os.path.join(HERE, "out"), prefix=prefix)
    bot.profile_dir = prof_dir
    return bot, tz


def cmd_chats():
    from squad.imessage import IMessage
    im = IMessage([])
    ok, why = im.check_access()
    if not ok:
        print("Can't read Messages (%s). Give your Terminal app Full Disk Access: System Settings > Privacy & "
              "Security > Full Disk Access, turn on Terminal, then reopen Terminal." % why)
        return 1
    print("Recent chats (use the name, or the guid if it has no name, in SQUAD_CHATS):")
    for guid, name, ident, n, last in im.list_chats():
        print("  %-28s %-40s %d people" % ((name or "(no name)")[:28], guid, n))
    print("\nNo group? Text yourself once, then put your own number or email in SQUAD_TEST_CHAT (solo test mode).")
    return 0


def cmd_check():
    load_env()
    ok_all = True
    try:
        from squad.imessage import IMessage
        ok, why = IMessage([]).check_access()
        print(("OK   " if ok else "FAIL ") + "Messages database: " + ("readable" if ok else why + " (needs Full Disk Access)"))
        ok_all &= ok
    except Exception as e:  # noqa: BLE001
        print("FAIL Messages database: %s" % e); ok_all = False
    prof = load_profiles(os.path.join(HERE, os.environ.get("SQUAD_PROFILES", "profiles")))
    print("OK   %d profiles: %s" % (len(prof), ", ".join(prof)))
    chats = [c for c in os.environ.get("SQUAD_CHATS", "").split(",") if c.strip()]
    test = os.environ.get("SQUAD_TEST_CHAT", "")
    print(("OK   " if chats or test else "FAIL ") + "SQUAD_CHATS: %s" % (", ".join(chats) or "not set (run: python3 run.py chats)"))
    if test:
        print("OK   solo test mode in: %s" % test)
    ok_all &= bool(chats or test)
    g = Grok()
    if g.enabled:
        reply = g.chat("Say hi to the squad in five words.")
        print(("OK   " if reply else "WARN ") + "Grok: %s" % (("model %s says: %s" % (g.model, reply)) if reply else g.last_error))
    else:
        print("WARN Grok: no XAI_API_KEY in .env, so the bot uses its built-in replies only")
    return 0 if ok_all else 1


def cmd_imessage():
    load_env()
    from squad.imessage import IMessage
    chats = [c for c in os.environ.get("SQUAD_CHATS", "").split(",") if c.strip()]
    if not chats and not os.environ.get("SQUAD_TEST_CHAT"):
        print("Set SQUAD_CHATS (a group) or SQUAD_TEST_CHAT (the chat with yourself) in .env first. python3 run.py chats shows them.")
        return 1
    separate = os.environ.get("SQUAD_ACCOUNT", "own").lower() == "separate"
    prefix = "" if separate else os.environ.get("SQUAD_PREFIX", "🤖 ")
    tz = ZoneInfo(os.environ.get("SQUAD_TZ", "America/Phoenix"))
    bot, tz = make_bot(lambda: datetime.now(tz), os.path.join(HERE, "state", "state.json"), Grok(), prefix)
    im = IMessage(chats)
    ok, why = im.check_access()
    if not ok:
        print("Can't read Messages: %s. Give Terminal Full Disk Access and reopen it." % why)
        return 1
    im.start()
    test = os.environ.get("SQUAD_TEST_CHAT", "").strip()
    if test and not prefix:
        prefix = "🤖 "  # texting yourself: the prefix is how it tells its own replies apart
        bot.prefix = prefix
    cfg = {"prefix": prefix, "separate": separate, "test": test, "members": {}, "seen": {}}
    print("Squad Radar is listening in: %s (Ctrl+C to stop)" % (", ".join(chats) or "your chat with yourself"))
    if test:
        print("Solo test mode: text yourself. 'as Priya: anyone free sat?' plays a sample friend, "
              "'dm: squad budget 10' (or 'as Jake dm: ...') is a private setup DM.")
    last_tick = 0
    while True:
        try:
            for m in im.fetch():
                route(bot, im, m, cfg)
            if time.time() - last_tick > 30:
                last_tick = time.time()
                for o in bot.tick():
                    deliver(im, o, cfg)
            time.sleep(2)
        except KeyboardInterrupt:
            print("\nbye")
            return 0
        except Exception as e:  # noqa: BLE001
            print("error:", e)
            time.sleep(5)


def same_person(chat_id, handle):
    """Does this 1:1 chat id (any;-;+14805551234 / iMessage;-;me@icloud.com) belong to this phone/email?"""
    a = chat_id.split(";")[-1].strip().lower()
    b = handle.strip().lower()
    if "@" in a or "@" in b:
        return a == b
    da, db = re.sub(r"\D", "", a), re.sub(r"\D", "", b)
    return len(db) >= 7 and da[-10:] == db[-10:]


def route(bot, im, m, cfg):
    if cfg["prefix"] and m["text"].startswith(cfg["prefix"].strip()):
        return  # the bot's own messages (and their echoes)
    if m["from_me"] and cfg["separate"] and not cfg["test"]:
        return
    if cfg["test"] and not m["is_group"] and same_person(m["chat_id"], cfg["test"]):
        # texting yourself saves each message twice (sent + received); handle the first copy only
        key = m["text"] or "react:%s:%s:%s" % (m.get("target"), m.get("reaction"), m.get("removed"))
        now = time.time()
        cfg["seen"] = {k: t for k, t in cfg["seen"].items() if now - t < 20}
        if key in cfg["seen"]:
            return
        cfg["seen"][key] = now
        cfg["self_chat"], cfg["bot"] = m["chat_id"], bot
        solo_test(bot, m)
        for o in bot.on_message(m):
            deliver(im, o, cfg)
        return
    if m["is_group"]:
        if m["chat_id"] not in cfg["members"]:
            cfg["members"][m["chat_id"]] = im.members(m["chat_id"])
        m["members"] = cfg["members"][m["chat_id"]]
    else:
        m["members"] = [m["sender"]]
    for o in bot.on_message(m):
        deliver(im, o, cfg)


def solo_test(bot, m):
    """Turn the chat with yourself into a pretend group of you + the sample friends."""
    m["from_me"], m["sender"] = True, "me"
    mt = re.match(r"^\s*as\s+([A-Za-z]+)\s*(dm)?\s*:\s*(.+)$", m["text"], re.S | re.I)
    dm = re.match(r"^\s*dm\s*:\s*(.+)$", m["text"], re.S | re.I)
    if mt:
        for p in bot.profiles.values():
            if p["name"].lower() == mt.group(1).lower() and p.get("handles"):
                m["sender"], m["from_me"] = p["handles"][0], p["handles"][0] == "me"
                break
        m["text"] = mt.group(3).strip()
        dm = dm or (mt.group(2) and True)
    elif dm:
        m["text"] = dm.group(1).strip()
    if dm:
        m["is_group"], m["members"], m["chat_id"] = False, [m["sender"]], "dm:" + m["sender"]
    else:
        m["is_group"] = True
        m["members"] = ["me"] + [p["handles"][0] for p in bot.profiles.values() if p.get("handles") and p["handles"][0] != "me"]
    return m


def deliver(im, o, cfg=None):
    print(">>", ("[DM %s] " % o["dm"]) if o.get("dm") else "", o.get("text") or ("[file] " + o.get("file", "")))
    target = o.get("chat_id", "")
    if cfg and cfg.get("test") and (o.get("dm") or target.startswith("dm:")):
        # solo test mode: private DMs show up in your own chat, labeled, and never go to the sample phone numbers
        own = cfg.get("self_chat")
        if own and o.get("text"):
            who = o.get("dm") or target[3:]
            name = cfg["bot"].name_of(who) if cfg.get("bot") else who
            text = o["text"][len(cfg["prefix"]):] if o["text"].startswith(cfg["prefix"]) else o["text"]
            im.send_text(own, "%s[private DM to %s] %s" % (cfg["prefix"], name, text))
        return
    if o.get("dm"):
        im.send_dm(o["dm"], o["text"])
    elif o.get("file"):
        im.send_file(target, o["file"])
    else:
        im.send_text(target, o["text"])


# ---------- simulator ----------
def sim_members(bot):
    out = []
    for p in bot.profiles.values():
        out.append(p["handles"][0])
    return out


def cmd_sim():
    load_env()
    tz = ZoneInfo(os.environ.get("SQUAD_TZ", "America/Phoenix"))
    clock = {"now": datetime(2026, 10, 29, 18, 0, tzinfo=tz)}
    bot, _ = make_bot(lambda: clock["now"], None, Grok())
    members = sim_members(bot)
    names = {p["name"].lower(): p["handles"][0] for p in bot.profiles.values()}
    print("Group chat 'Squad' with %s. Type 'Name: message'. '+2h' moves time forward. 'quit' to stop." % ", ".join(bot.profiles))
    print("Tapbacks: 'Sam: react ‼️ landlord' (‼️ pins it). Voice notes are saved in out/.")
    seq = 0
    print("It's %s." % clock["now"].strftime("%a %b %d, %I:%M %p"))
    while True:
        try:
            line = input("> ").strip()
        except EOFError:
            break
        if line in ("quit", "exit"):
            break
        if line.startswith("+") and line[-1] in "hd":
            n = float(line[1:-1])
            clock["now"] += timedelta(hours=n) if line[-1] == "h" else timedelta(days=n)
            print("   (it's now %s)" % clock["now"].strftime("%a %b %d, %I:%M %p"))
            for o in bot.tick():
                print_out(o)
            continue
        if ":" not in line:
            print("   format: Priya: anyone free this weekend?")
            continue
        who, text = line.split(":", 1)
        h = names.get(who.strip().lower())
        if not h:
            print("   who's that? one of: %s" % ", ".join(bot.profiles))
            continue
        text = text.strip()
        rm = re.match(r"^react\s+(\S+)\s+(.+)$", text)
        if rm:  # "Sam: react ‼️ landlord" = a tapback on the latest message containing "landlord"
            hist = bot.state.get("sim-squad", {}).get("history", [])
            target = next((x for x in reversed(hist) if rm.group(2).lower() in x["text"].lower()), None)
            if not target:
                print("   no message with \"%s\"" % rm.group(2))
                continue
            kind = {"‼️": "emphasize", "!!": "emphasize", "❤️": "love", "👍": "like", "😂": "laugh", "👎": "dislike", "❓": "question"}.get(rm.group(1), "like")
            bot.on_message({"kind": "reaction", "chat_id": "sim-squad", "sender": h, "members": members, "is_group": True,
                            "target": target.get("guid"), "reaction": kind, "text": ""})
            print("   (%s tapped %s on %s's message)" % (who.strip(), rm.group(1), target["who"]))
            continue
        seq += 1
        for o in bot.on_message({"chat_id": "sim-squad", "text": text, "sender": h, "members": members, "is_group": True,
                                 "guid": "sim-%d" % seq}):
            print_out(o)


def print_out(o):
    if o.get("dm"):
        print("   🤖 (private DM to %s) %s" % (o["dm"], o["text"]))
    elif o.get("file", "").endswith((".m4a", ".wav", ".aiff")):
        print("   🤖 [voice note: %s]" % o["file"])
    elif o.get("file"):
        print("   🤖 [calendar invite: %s]" % os.path.basename(o["file"]))
    else:
        for i, l in enumerate(o["text"].split("\n")):
            print(("   🤖 " if i == 0 else "      ") + l)


DEMO = [
    ("## Plans", None, None),
    ("2026-10-29T18:00", "Sam", "@squad help"),
    ("2026-10-29T18:02", "Priya", "anyone free this weekend??"),
    ("2026-10-29T18:03", "Jake", "1"),
    ("2026-10-29T18:03", "Sam", "1"),
    ("2026-10-29T18:04", "Amrish", "1"),
    ("2026-10-29T18:05", "Jake", "in"),
    ("2026-10-29T18:05", "Sam", "in 🙋‍♂️"),
    ("2026-10-29T18:06", "Priya", "in"),
    ("2026-10-29T18:07", "Amrish", "@squad why"),
    ("2026-10-29T18:10", "Sam", "@squad decide tacos, sushi, pizza"),
    ("## Money", None, None),
    ("2026-10-30T19:40", "Amrish", "@squad bill tax 8% tip 18%\nJake: burger 14\nPriya: pasta 16, soda 3\nshared: fries 8"),
    ("2026-10-30T19:45", "Jake", "paid"),
    ("2026-10-30T21:00", "Priya", "anyone want boba"),
    ("2026-10-30T21:01", "Priya", "@squad food run kung fu tea 10"),
    ("2026-10-30T21:02", "Jake", "+ taro milk tea less ice $6.5"),
    ("2026-10-30T21:03", "Sam", "+ mango slush $5.25"),
    ("2026-10-30T21:12", None, "tick"),
    ("## Translation and catching up", None, None),
    ("2026-10-31T09:00", "Sam", "@squad translate on"),
    ("2026-10-31T09:05", "Priya", "enna plan naalaiku? sapdu porom ah?"),
    ("2026-10-31T09:06", "Jake", "@squad translate see you at 7 to spanish"),
    ("2026-10-31T13:30", "Amrish", "@squad tldr"),
    ("2026-10-31T17:00", None, "tick"),
    ("## Group project", None, None),
    ("2026-11-01T20:00", "Amrish", "@squad project CSE 310 pitch due nov 6"),
    ("2026-11-01T20:01", "Amrish", "@squad tasks research 3h, intro slides 2h, demo video 3h, final deck 3h, presenting prep 1h"),
    ("2026-11-01T20:02", "Sam", "dibs intro slides"),
    ("2026-11-01T20:02", "Amrish", "dibs intro slides"),
    ("2026-11-01T20:03", "Amrish", "@squad split"),
    ("2026-11-01T20:04", "Priya", "ok"),
    ("2026-11-01T20:04", "Jake", "ok"),
    ("2026-11-01T20:05", "Sam", "ok"),
    ("2026-11-01T20:05", "Amrish", "ok"),
    ("2026-11-03T11:00", None, "tick"),
    ("2026-11-03T11:05", "Jake", "squad gift karaoke night", "dm"),
    ("2026-11-03T21:00", "Sam", "done intro slides"),
    ("2026-11-04T09:30", None, "tick"),
    ("2026-11-04T12:00", "Priya", "done research"),
    ("2026-11-04T12:30", "Amrish", "@squad status"),
    ("2026-11-06T22:00", "Amrish", "@squad receipts"),
    ("## Game day and carpools", None, None),
    ("2026-11-25T18:00", "Sam", "@squad game day"),
    ("2026-11-25T18:01", "Sam", "got tix"),
    ("2026-11-25T18:01", "Jake", "need tix"),
    ("2026-11-25T18:02", "Amrish", "@squad ride to the game"),
    ("2026-11-25T18:03", "Jake", "driving 3"),
    ("2026-11-25T18:03", "Priya", "ride from barrett"),
    ("2026-11-25T18:04", "Sam", "ride from vista del sol"),
    ("2026-11-25T18:05", "Amrish", "@squad match rides"),
    ("2026-11-25T18:06", "Jake", "ok"),
    ("## Move-out market", None, None),
    ("2026-12-08T10:00", "Jake", "@squad sell IKEA desk $40, lamp $10, mini fridge $50 obo pickup by 12/12 @ Tooker"),
    ("2026-12-08T10:02", "Priya", "want #1"),
    ("2026-12-08T10:03", "Sam", "want #1"),
    ("2026-12-08T10:05", "Amrish", "offer #3 $35"),
    ("2026-12-08T10:10", "Jake", "accept #3 amrish"),
    ("2026-12-08T11:00", "Sam", "@squad looking for bike under $60"),
    ("2026-12-08T12:00", "Priya", "selling my road bike, 55 bucks, pickup by 12/11"),
    ("2026-12-08T12:01", "Priya", "list it"),
    ("2026-12-09T10:30", None, "tick"),
    ("2026-12-09T16:00", "Jake", "sold #1 to sam"),
    ("2026-12-10T10:00", "Amrish", "@squad market under $30"),
    ("2026-12-11T10:00", None, "tick"),
    ("2026-12-11T10:05", "Jake", "drop #2 $7"),
    ("## Breaks and roommates", None, None),
    ("2026-12-11T12:00", "Amrish", "@squad airport"),
    ("2026-12-11T12:05", "Amrish", "@squad chores dishes, trash, bathroom"),
    ("2026-12-11T12:06", "Sam", "@squad add oat milk, paper towels"),
    ("2026-12-11T12:30", "Jake", "got paper towels"),
    ("2026-12-11T12:35", "Sam", "@squad this or that"),
    ("## Group radar: what matters, who tagged you, voice notes, stats", None, None),
    ("2026-12-11T13:00", "Priya", "heads up the landlord moved the move-out inspection to mon 10am, clean your rooms"),
    ("2026-12-11T13:01", "Jake", "noooo"),
    ("2026-12-11T13:01", "Sam", "💀💀"),
    ("2026-12-11T13:02", "Sam", "‼️", "react", "landlord moved"),
    ("2026-12-11T13:02", "Jake", "❤️", "react", "landlord moved"),
    ("2026-12-11T13:05", "Priya", "@jake can you return the router before you fly out?"),
    ("2026-12-11T13:10", "Jake", "lol"),
    ("2026-12-11T13:12", "Amrish", "final electric bill is $84, i'll split it tonight"),
    ("2026-12-11T13:20", "Sam", "does anyone have a vacuum i can borrow?"),
    ("2026-12-11T16:30", None, "tick"),
    ("2026-12-11T17:00", "Jake", "@squad highlights"),
    ("2026-12-11T17:01", "Jake", "remind #1 tomorrow"),
    ("2026-12-11T17:02", "Jake", "@squad mentions"),
    ("2026-12-11T17:03", "Jake", "@squad voice highlights"),
    ("2026-12-11T17:05", "Priya", "@squad stats"),
    ("2026-12-11T17:06", "Sam", "@squad wrapped"),
]


class DemoGrok:
    """Offline stand-in for Grok in the scripted demo. Its replies are canned examples, not real model output."""
    enabled = True
    CANNED = {
        "enna plan naalaiku? sapdu porom ah?": "what's the plan tomorrow? are we going out to eat?",
        "see you at 7": "nos vemos a las 7",
    }

    def chat(self, user, system="", json_mode=False, max_tokens=300):
        if "Translate" in system:
            text = user.split("Text: ", 1)[1]
            return self.CANNED.get(text)
        if "Summarize" in system:
            return ("- movie night Sat 7pm at Hayden Lawn is locked, invite sent\n- Jake paid you back for dinner, Priya hasn't yet\n"
                    "- Priya asked what the plan is tomorrow")
        return None

    def json(self, user, system):
        return None


def cmd_demo():
    os.environ.pop("XAI_API_KEY", None)  # the demo is deterministic and offline
    tz = ZoneInfo("America/Phoenix")
    clock = {"now": datetime(2026, 10, 29, 18, 0, tzinfo=tz)}
    bot, _ = make_bot(lambda: clock["now"], None, DemoGrok())
    bot.weather_fetch = lambda when: None
    bot.profiles = json.loads(json.dumps(bot.profiles))
    bot.profile_dir = None
    members = sim_members(bot)
    seq, spoken = [0], []

    def demo_tts(text, path, lang):  # the real bot uses the Mac's voices; the transcript shows what it would say
        spoken.append(text)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").write(text)
        return path
    bot.tts = demo_tts
    handle = {p["name"]: p["handles"][0] for p in bot.profiles.values()}
    names = {v: k for k, v in handle.items()}
    md = ["# Squad Radar demo", "",
          "Group chat: Amrish, Priya, Jake, Sam. Sample people, sample events, made-up money.",
          "Runs offline: the two AI-written replies (🌐 translations and the 📝 tldr) are canned examples here; with a Grok key they come from Grok.", ""]
    for row in DEMO:
        ts, who, text = row[0], row[1], row[2]
        if ts.startswith("##"):
            md += [ts, ""]
            continue
        clock["now"] = datetime.fromisoformat(ts).replace(tzinfo=tz)
        stamp = clock["now"].strftime("%a %b %d %I:%M %p")
        if who is None:
            outs = bot.tick()
            if outs:
                md.append("*%s*" % stamp)
        elif len(row) > 3 and row[3] == "react":
            target = next((x for x in reversed(bot.state.get("demo", {}).get("history", [])) if row[4] in x["text"]), None)
            md.append("*%s tapped %s on %s's message*" % (who, text, target["who"] if target else "a"))
            kind = {"‼️": "emphasize", "❤️": "love", "👍": "like", "😂": "laugh"}.get(text, "like")
            outs = bot.on_message({"kind": "reaction", "chat_id": "demo", "sender": handle[who], "members": members, "is_group": True,
                                   "target": target.get("guid") if target else "", "reaction": kind, "text": ""})
        elif len(row) > 3 and row[3] == "dm":
            md.append("**%s** (private DM to the bot, %s): %s" % (who, stamp, text))
            outs = bot.on_message({"chat_id": "dm-" + who, "text": text, "sender": handle[who], "members": [handle[who]], "is_group": False})
        else:
            md.append("**%s** (%s): %s" % (who, stamp, text.replace("\n", "  \n")))
            seq[0] += 1
            outs = bot.on_message({"chat_id": "demo", "text": text, "sender": handle[who], "members": members, "is_group": True,
                                   "guid": "demo-%d" % seq[0]})
        for o in outs:
            if o.get("dm"):
                md.append("> 🤖 🔒 *private DM to %s:* %s" % (names.get(o["dm"], o["dm"]), o["text"]))
            elif o.get("file", "").endswith((".m4a", ".wav", ".aiff")):
                md.append("> 🤖 🔊 *voice note (audio file). It says:* \"%s\"" % spoken[-1] if spoken else "> 🤖 🔊 *voice note*")
            elif o.get("file"):
                md.append("> 🤖 📅 *calendar invite: %s*" % os.path.basename(o["file"]))
            else:
                md.append("> 🤖 " + o["text"].replace("\n", "  \n> "))
        md.append("")
    path = os.path.join(HERE, "demo_transcript.md")
    open(path, "w", encoding="utf-8").write("\n".join(md))
    print("\n".join(md))
    print("\nwrote", path)
    return 0


def cmd_llm():
    """Prove the language model is really connected: one live call, timed."""
    load_env()
    g = Grok()
    st = g.status()
    if not st["enabled"]:
        env = os.path.join(HERE, ".env")
        print("No model key found.")
        if not os.path.exists(env):
            print("- There is no .env file in %s. Run: cp .env.example .env  then fill in your key." % HERE)
            if [f for f in os.listdir(HERE) if f.startswith(".env") and f != ".env.example"]:
                print("- Found these instead: %s (it must be named exactly .env)" % ", ".join(f for f in os.listdir(HERE) if f.startswith(".env")))
        else:
            seen = {}
            for i, line in enumerate(open(env, encoding="utf-8-sig"), 1):
                m = re.match(r"^\s*(?:export\s+)?(XAI_API_KEY|LLM_API_KEY|LLM_BASE_URL|LLM_MODEL)\s*=\s*(.*)$", line)
                if m:
                    seen.setdefault(m.group(1), []).append((i, "filled" if m.group(2).strip() else "empty"))
            for k in ("XAI_API_KEY", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
                print("- %s: %s" % (k, ", ".join("line %d %s" % x for x in seen.get(k, [])) or "missing"))
            print("Put your key after LLM_API_KEY= (Gemini, Groq, OpenRouter...) or XAI_API_KEY= (Grok), save the file, and run this again.")
        return 1
    print("Provider: %s (%s)" % (st["provider"], g.base))
    model = g.pick_model()
    print("Model: %s%s (time limit %ds)" % (model, ", thinking effort " + g.effort if g.effort else "", g.timeout))
    t0 = time.time()
    out = g.chat("Say hi to the squad in under 10 words.")
    if not out:
        print("FAIL model %s did not answer: %s" % (model, g.last_error))
        if "timed out" in str(g.last_error):
            print("     It's too slow. Try a faster model in .env, for example LLM_MODEL=gemini-3.5-flash-lite")
        if "404" in str(g.last_error) or "not found" in str(g.last_error).lower():
            print("     That model name isn't available to your key. Check LLM_MODEL in .env.")
        return 1
    print("OK   answered in %.1fs: %s" % (time.time() - t0, out))
    t0 = time.time()
    j = g.json('Message: "anyone free saturday night?"', __import__("squad.llm", fromlist=["INTENT_SYSTEM"]).INTENT_SYSTEM)
    print(("OK   " if j else "FAIL ") + "JSON answer in %.1fs: %s" % (time.time() - t0, j or g.last_error))
    if j and time.time() - t0 > 8:
        print("     Works, but slow for a group chat. A lighter model will feel snappier.")
    return 0 if j else 1


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    fn = {"chats": cmd_chats, "check": cmd_check, "imessage": cmd_imessage, "sim": cmd_sim, "demo": cmd_demo, "llm": cmd_llm}.get(cmd)
    if not fn:
        print(__doc__)
        sys.exit(0)
    sys.exit(fn() or 0)
