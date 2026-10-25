"""Solo test mode: texting yourself turns that chat into a pretend group with the sample friends.
Uses a fake Messages app, so nothing is sent anywhere."""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

import run  # noqa: E402

TZ = ZoneInfo("America/Phoenix")
fails = 0
SELF = "any;-;+14805559999"


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  -> " + str(detail)[:500]))
    fails += 0 if cond else 1


class FakeMessages:
    def __init__(self):
        self.sent, self.dms, self.files = [], [], []

    def send_text(self, chat, text):
        self.sent.append((chat, text))

    def send_dm(self, handle, text):
        self.dms.append((handle, text))

    def send_file(self, chat, path):
        self.files.append((chat, path))

    def members(self, chat):
        return ["me"]


now = [datetime(2026, 10, 27, 10, 0, tzinfo=TZ)]
state = os.path.join(tempfile.mkdtemp(), "state.json")
bot, _ = run.make_bot(lambda: now[0], state, None, "🤖 ")
im = FakeMessages()
cfg = {"prefix": "🤖 ", "separate": False, "test": "(480) 555-9999", "members": {}, "seen": {}}


def text_myself(text, both_copies=True):
    """iMessage saves a message to yourself twice: once sent, once received."""
    before = len(im.sent)
    run.route(bot, im, {"chat_id": SELF, "chat_name": "", "text": text, "from_me": True, "sender": "me",
                        "is_group": False, "ts": now[0], "attachments": []}, cfg)
    if both_copies:
        run.route(bot, im, {"chat_id": SELF, "chat_name": "", "text": text, "from_me": False,
                            "sender": "+14805559999", "is_group": False, "ts": now[0], "attachments": []}, cfg)
    return [t for _, t in im.sent[before:]]


check("phone formats match", run.same_person(SELF, "(480) 555-9999") and run.same_person(SELF, "+1 480 555 9999"))
check("email chat matches", run.same_person("iMessage;-;amrish@icloud.com", "Amrish@iCloud.com"))
check("other chats don't match", not run.same_person("any;-;+14805550101", "(480) 555-9999"))

out = text_myself("@squad help")
check("help answers once even though iMessage saved it twice", len(out) == 1, out)
check("replies go to the chat with yourself", all(c == SELF for c, _ in im.sent))

out = text_myself("anyone free this weekend??")
check("a passive plan question gets a plan", out and "1" in out[0], out)
out = text_myself("as Priya: 1")
out += text_myself("as Jake: 1")
out += text_myself("as Sam: 1")
check("sample friends can vote and the plan locks at a majority", any("movie night" in t.lower() for t in out), out)
out = text_myself("in") + text_myself("as Priya: in") + text_myself("as Jake: down")
check("you and the sample friends RSVP and it locks", any("locked" in t for t in out), out)
check("the calendar invite lands in your own chat", im.files and im.files[-1][0] == SELF and im.files[-1][1].endswith(".ics"), im.files)

out = text_myself("🤖 this is the bot's own message")
check("the bot ignores its own replies", out == [], out)

out = text_myself("@squad sell IKEA desk $40, lamp $10 pickup by 11/20 @ Tooker")
check("market listing works in solo mode", any("#1" in t for t in out), out)
out = text_myself("as Sam: want #1")
check("a sample friend can claim an item", any("sam" in t.lower() for t in out), out)

before_dm = len(im.dms)
out = text_myself("as Jake dm: squad budget 12")
check("a private setup DM is answered", len(out) == 1 and "[private DM to Jake]" in out[0], out)
check("nothing is ever texted to the sample phone numbers", len(im.dms) == before_dm == 0, im.dms)

# the birthday DMs go out a week before Priya's birthday (11-10); in solo mode they must stay in your own chat
now[0] = datetime(2026, 11, 3, 10, 0, tzinfo=TZ)
before = len(im.sent)
for o in bot.tick():
    run.deliver(im, o, cfg)
private = [t for _, t in im.sent[before:] if "[private DM to" in t]
check("secret birthday DMs show up labeled in your chat", private and all("Priya]" not in t for t in private), im.sent[before:])
check("still nothing sent to the sample numbers", im.dms == [], im.dms)
check("every message went to your own chat", all(c == SELF for c, _ in im.sent))

print("\n%s (%d failed)" % ("ALL PASSED" if not fails else "SOME FAILED", fails))
sys.exit(1 if fails else 0)
