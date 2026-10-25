#!/usr/bin/env python3
"""Squad Radar launcher.

  python3 run.py chats      list your recent iMessage chats (to find the group's name)
  python3 run.py check      check Messages access, profiles and the Grok key
  python3 run.py imessage   run the bot in the group chats named in .env
  python3 run.py sim        chat with the bot in your terminal as any of the sample friends
  python3 run.py demo       play the scripted demo and write demo_transcript.md
"""
import glob
import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from squad.bot import SquadBot  # noqa: E402
from squad.llm import Grok  # noqa: E402


def load_env(path=os.path.join(HERE, ".env")):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


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
    print(("OK   " if chats else "FAIL ") + "SQUAD_CHATS: %s" % (", ".join(chats) or "not set (run: python3 run.py chats)"))
    ok_all &= bool(chats)
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
    if not chats:
        print("Set SQUAD_CHATS in .env first (python3 run.py chats shows the names).")
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
    print("Squad Radar is listening in: %s (Ctrl+C to stop)" % ", ".join(chats))
    members_cache = {}
    last_tick = 0
    while True:
        try:
            for m in im.fetch():
                if m["from_me"] and (separate or (prefix and m["text"].startswith(prefix.strip()))):
                    continue  # the bot's own messages
                if m["is_group"]:
                    if m["chat_id"] not in members_cache:
                        members_cache[m["chat_id"]] = im.members(m["chat_id"])
                    m["members"] = members_cache[m["chat_id"]]
                else:
                    m["members"] = [m["sender"]]
                for o in bot.on_message(m):
                    deliver(im, o)
            if time.time() - last_tick > 30:
                last_tick = time.time()
                for o in bot.tick():
                    deliver(im, o)
            time.sleep(2)
        except KeyboardInterrupt:
            print("\nbye")
            return 0
        except Exception as e:  # noqa: BLE001
            print("error:", e)
            time.sleep(5)


def deliver(im, o):
    print(">>", o.get("text") or ("[file] " + o.get("file", "")))
    if o.get("file"):
        im.send_file(o["chat_id"], o["file"])
    else:
        im.send_text(o["chat_id"], o["text"])


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
        for o in bot.on_message({"chat_id": "sim-squad", "text": text.strip(), "sender": h, "members": members, "is_group": True}):
            print_out(o)


def print_out(o):
    if o.get("file"):
        print("   🤖 [calendar invite: %s]" % os.path.basename(o["file"]))
    else:
        for i, l in enumerate(o["text"].split("\n")):
            print(("   🤖 " if i == 0 else "      ") + l)


DEMO = [
    ("2026-10-29T18:02", "Priya", "anyone free this weekend??"),
    ("2026-10-29T18:03", "Jake", "1"),
    ("2026-10-29T18:03", "Sam", "1"),
    ("2026-10-29T18:04", "Amrish", "1"),
    ("2026-10-29T18:05", "Jake", "in"),
    ("2026-10-29T18:05", "Sam", "in 🙋‍♂️"),
    ("2026-10-29T18:06", "Priya", "in"),
    ("2026-10-29T18:07", "Amrish", "@squad why"),
    ("2026-10-29T18:10", "Sam", "@squad decide tacos, sushi, pizza"),
    ("2026-10-31T17:00", None, "tick"),
    ("2026-11-01T20:00", "Amrish", "@squad project CSE 310 pitch due nov 6"),
    ("2026-11-01T20:01", "Amrish", "@squad tasks research 3h, intro slides 2h, demo video 3h, final deck 3h, presenting prep 1h"),
    ("2026-11-01T20:02", "Sam", "dibs intro slides"),
    ("2026-11-01T20:02", "Amrish", "dibs intro slides"),
    ("2026-11-01T20:03", "Amrish", "@squad split"),
    ("2026-11-01T20:04", "Priya", "ok"),
    ("2026-11-01T20:04", "Jake", "ok"),
    ("2026-11-01T20:05", "Sam", "ok"),
    ("2026-11-01T20:05", "Amrish", "ok"),
    ("2026-11-03T21:00", "Sam", "done intro slides"),
    ("2026-11-04T09:30", None, "tick"),
    ("2026-11-04T12:00", "Priya", "done research"),
    ("2026-11-04T12:30", "Amrish", "@squad status"),
    ("2026-11-06T22:00", "Amrish", "@squad receipts"),
]


def cmd_demo():
    os.environ.pop("XAI_API_KEY", None)  # the demo is deterministic and offline
    tz = ZoneInfo("America/Phoenix")
    clock = {"now": datetime(2026, 10, 29, 18, 0, tzinfo=tz)}
    bot, _ = make_bot(lambda: clock["now"], None, None)
    members = sim_members(bot)
    handle = {p["name"]: p["handles"][0] for p in bot.profiles.values()}
    md = ["# Squad Radar demo", "", "Group chat: Amrish, Priya, Jake, Sam (sample people and sample events).", ""]
    for ts, who, text in DEMO:
        clock["now"] = datetime.fromisoformat(ts).replace(tzinfo=tz)
        stamp = clock["now"].strftime("%a %b %d %I:%M %p")
        if who is None:
            outs = bot.tick()
            if outs:
                md.append("*%s*" % stamp)
        else:
            md.append("**%s** (%s): %s" % (who, stamp, text))
            outs = bot.on_message({"chat_id": "demo", "text": text, "sender": handle[who], "members": members, "is_group": True})
        for o in outs:
            if o.get("file"):
                md.append("> 🤖 📅 *calendar invite: %s*" % os.path.basename(o["file"]))
            else:
                md.append("> 🤖 " + o["text"].replace("\n", "  \n> "))
        md.append("")
    path = os.path.join(HERE, "demo_transcript.md")
    open(path, "w", encoding="utf-8").write("\n".join(md))
    print("\n".join(md))
    print("\nwrote", path)
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    fn = {"chats": cmd_chats, "check": cmd_check, "imessage": cmd_imessage, "sim": cmd_sim, "demo": cmd_demo}.get(cmd)
    if not fn:
        print(__doc__)
        sys.exit(0)
    sys.exit(fn() or 0)
