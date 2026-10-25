"""Discord bridge tests with fake Discord objects (no network): mentions, long messages, channel squads, private
setup by DM, reactions and pins, receipts of model-written replies, reminders posted by the ticker, voice notes."""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

try:
    import discord  # noqa: F401
except ImportError:
    print("SKIP discord.py isn't installed (pip3 install -r requirements.txt)")
    sys.exit(0)

import web  # noqa: E402
from discord_bridge import DiscordBridge, chunks, normalize, render  # noqa: E402

os.environ.setdefault("SQUAD_QUIET", "1")
TZ = ZoneInfo("America/Phoenix")
fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  -> " + str(detail)[:600]))
    fails += 0 if cond else 1


# ---------- helpers ----------
check("mentioning the bot means @squad", normalize("<@999> plan this weekend", 999, {}) == "@squad plan this weekend")
check("user mentions become names the bot can match", normalize("<@!5> can you bring the speaker?", 999, {5: "Priya Ray"}) == "@Priya can you bring the speaker?")
check("custom emoji and channel links are tidied", normalize("<:pog:123> see <#77>", 999, {}) == ":pog: see #channel")
parts = chunks("line\n" * 1000)
check("long replies are split under Discord's limit", len(parts) > 1 and all(len(p) <= 1900 for p in parts) and "".join(parts).count("line") == 1000)
check("model-written replies get a small label", render({"text": "tacos"}, "gemini-3.6-flash")[0] == "tacos\n-# written by gemini-3.6-flash")
tmp = tempfile.mkdtemp()
sp = os.path.join(tmp, "v.speech.json")
json.dump({"text": "rent is due friday", "lang": "english"}, open(sp, "w"))
check("voice notes become Discord text-to-speech", render({"file": sp}) == ("rent is due friday", None, True))


# ---------- fakes ----------
class FakeModel:
    enabled = True
    calls = ok = 0

    def status(self):
        return {"enabled": True, "provider": "Gemini", "model": "gemini-3.6-flash", "ok": self.ok}

    def chat(self, user, system="", json_mode=False, max_tokens=300):
        self.ok += 1
        return "tacos, obviously 🌮"

    def json(self, user, system=""):
        if "realistic Discord messages" in system:
            names = [l[2:].split(":")[0] for l in user.split("Members:\n", 1)[1].split("\n\n")[0].split("\n")]
            return {"messages": [{"who": names[0], "text": "anyone free this weekend??"}, {"who": "Nobody", "text": "ignored"},
                                 {"who": names[-1], "text": "@squad tldr"}]}
        return None

    def vision_json(self, *a, **k):
        return None


class Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class Channel:
    def __init__(self, cid, name, category=None, guild=None):
        self.id, self.name, self.sent, self.category, self.guild = cid, name, [], category, guild
        self.mention = "<#%d>" % cid
        self.hooks = []
        self.deleted = False

    async def webhooks(self):
        return self.hooks

    async def history(self, limit=100, oldest_first=None):
        """Newest first, like Discord. Persona posts come from the webhook; the rest are the bot's own messages."""
        for x in reversed(self.sent[-limit:]):
            if not x.get("text"):
                continue
            if x.get("as"):
                author = type("A", (), {"id": 777, "name": x["as"], "display_name": x["as"], "bot": True})()
                yield type("M", (), {"id": x["msg"].id, "author": author, "content": x["text"], "webhook_id": 1, "created_at": None})()
            else:
                author = type("A", (), {"id": 4242, "name": "Square Radar", "display_name": "Square Radar", "bot": True})()
                yield type("M", (), {"id": id(x), "author": author, "content": x["text"], "webhook_id": None, "created_at": None})()

    async def create_webhook(self, name):
        h = Hook(name, self)
        self.hooks.append(h)
        return h

    async def delete(self, reason=None):
        self.deleted = True
        if self.category:
            self.category.text_channels.remove(self)

    def typing(self):
        return Typing()

    async def send(self, content=None, tts=False, file=None, allowed_mentions=None):
        self.sent.append({"text": content, "tts": tts, "file": getattr(file, "filename", None)})


class SentMsg:
    _n = 50000

    def __init__(self, channel, content, username):
        SentMsg._n += 1
        self.id, self.channel, self.content, self.username, self.reactions = SentMsg._n, channel, content, username, []

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)


class Hook:
    def __init__(self, name, channel):
        self.name, self.channel = name, channel

    async def send(self, content, username=None, wait=False, allowed_mentions=None):
        m = SentMsg(self.channel, content, username)
        self.channel.sent.append({"text": content, "tts": False, "file": None, "as": username, "msg": m})
        return m


class Category:
    def __init__(self, name):
        self.name, self.text_channels = name, []

    @property
    def channels(self):
        return self.text_channels

    async def delete(self, reason=None):
        guild.categories.remove(self)


class Perms:
    manage_channels = manage_webhooks = True


class Guild:
    def __init__(self, name):
        self.name, self.categories, self.me, self.n = name, [], type("Me", (), {"guild_permissions": Perms()})(), 900

    async def create_category(self, name, reason=None):
        c = Category(name)
        self.categories.append(c)
        return c

    async def create_text_channel(self, name, category=None, topic=None, reason=None):
        self.n += 1
        ch = Channel(self.n, name, category, self)
        category.text_channels.append(ch)
        all_channels[ch.id] = ch
        return ch


class User:
    def __init__(self, uid, name):
        self.id, self.display_name, self.bot, self.sent = uid, name, False, []

    async def send(self, content=None, tts=False, file=None, allowed_mentions=None):
        self.sent.append({"text": content, "tts": tts, "file": getattr(file, "filename", None)})


class Msg:
    _n = 1000

    def __init__(self, author, channel, content):
        Msg._n += 1
        self.id, self.author, self.channel, self.content, self.attachments = Msg._n, author, channel, content, []
        self.webhook_id = None
        self.guild = getattr(channel, "guild", None)


class Reaction:
    def __init__(self, user_id, emoji, channel_id, message_id):
        self.user_id, self.emoji, self.channel_id, self.message_id, self.guild_id = user_id, emoji, channel_id, message_id, 1


clock = {"now": datetime(2026, 10, 30, 12, 0, tzinfo=TZ)}
app = web.App(tempfile.mkdtemp(), llm=FakeModel(), clock=lambda: clock["now"])
bridge = DiscordBridge(app, "fake-token", "")
general = Channel(111, "general")
other = Channel(222, "memes")
amrish, priya, jake = User(1, "Amrish"), User(2, "Priya R"), User(3, "Jake")
users = {1: amrish, 2: priya, 3: jake}

loop = asyncio.new_event_loop()
bridge.loop = loop


async def fetch_user(i):
    return users[i]

guild = Guild("ASU Squad")
all_channels = {111: general, 222: other}
general.guild = guild
bridge.client.get_channel = lambda i: all_channels.get(i)
bridge.client._connection.user = None
bridge.client.fetch_user = fetch_user


def run_loop():
    asyncio.set_event_loop(loop)
    bridge.queue = asyncio.Queue()
    loop.create_task(bridge._sender())
    loop.run_forever()


threading.Thread(target=run_loop, daemon=True).start()
while bridge.queue is None:
    time.sleep(0.01)


def say(user, channel, text):
    before = len(channel.sent)
    asyncio.run_coroutine_threadsafe(bridge.on_message(Msg(user, channel, text)), loop).result(10)
    time.sleep(0.15)
    return [x["text"] or x["file"] for x in channel.sent[before:]]


def react(user, emoji, channel, msg_id, removed=False):
    asyncio.run_coroutine_threadsafe(bridge.on_reaction(Reaction(user.id, emoji, channel.id, msg_id), removed), loop).result(10)


# ---------- a channel is a squad ----------
out = say(jake, other, "hi")
check("the first message in a new channel gets a one-time intro", out and "I'm Squad Radar" in out[0] and "@squad simulate" in out[0], out)
check("after that, plain chat stays quiet", say(jake, other, "hi again") == [])
out = say(amrish, general, "@squad help")
check("@squad help answers in the channel", out and "Squad Radar" in out[0], out)
check("people get a profile from their Discord name", "Priya R" not in app.bot.profiles and app.bot.name_of("dc:1") == "Amrish")
say(priya, general, "hey")
say(jake, general, "yo")
check("Discord display names become bot names", app.bot.name_of("dc:2") == "Priya R", app.bot.profiles.keys())
out = say(amrish, general, "anyone free this weekend??")
check("plans work in a Discord channel", out and "reply 1 or 2" in out[0], out)
say(priya, general, "1")
out = say(jake, general, "1")
check("votes are counted", out and "wins" in out[0], out)
out = say(amrish, general, "in") + say(priya, general, "in") + say(jake, general, "in")
check("locking sends the calendar invite as a file", any(str(x).endswith(".ics") for x in out), out)
room = app.db["rooms"].get("dc111")
check("each Discord channel shows up on the website as a squad", room and room["source"] == "discord" and room["name"] == "#general (ASU Squad)", room and room["name"])
check("the website copy has the people's messages and the bot's replies",
      any(m["kind"] == "user" and m["who"] == "Amrish" and "anyone free" in m["text"] for m in room["messages"])
      and any(m["kind"] == "bot" and "reply 1 or 2" in m["text"] for m in room["messages"]))
check("the invite file is attached on the website too", any((m.get("file") or {}).get("kind") == "invite" for m in room["messages"]))

# ---------- model replies are labeled ----------
out = say(priya, general, "@squad what should we eat tonight")
check("model replies arrive with the small label", out and "tacos" in out[0] and "-# written by gemini-3.6-flash" in out[0], out)

# ---------- reactions and pins ----------
m = Msg(priya, general, "wifi password is sundevils2026")
asyncio.run_coroutine_threadsafe(bridge.on_message(m), loop).result(10)
react(amrish, "📌", general, m.id)
h = [x for x in app.bot.state["dc111"]["history"] if "wifi" in x["text"]][0]
check("📌 pins a message", h.get("pinned"), h)
react(jake, "❤️", general, m.id)
check("❤️ counts as a reaction", h["reactions"].get(app.bot.name_of("dc:3")) == "love", h)
react(amrish, "📌", general, m.id, removed=True)
check("removing 📌 unpins", not h.get("pinned"), h)
react(amrish, "🍕", general, m.id)
check("other emoji are ignored", "Amrish" not in h["reactions"], h)
out = say(amrish, general, "@squad pins")
check("@squad pins in Discord", out and "no pins" in out[0], out)

# ---------- mentions ----------
say(priya, general, "<@!1> can you bring the speaker?")
out = say(amrish, general, "@squad mentions")
check("Discord @mentions count as tags", out and "speaker" in out[0] and "asked you" in out[0], out)

# ---------- private setup by DM ----------
before = len(amrish.sent)
app.external_message(None, "dc:1", "budget 15", ["dc:1"], private=True)
time.sleep(0.15)
check("DMs to the bot are private setup, answered by DM", any("saved" in (x["text"] or "") for x in amrish.sent[before:]), amrish.sent)
check("private setup never posts in the channel", not any("saved (budget)" in (x["text"] or "") for x in general.sent))
check("the budget is saved", app.bot.profiles["Amrish"]["prefs"].get("budget") == 15)

# ---------- voice notes ----------
before = len(general.sent)
say(amrish, general, "@squad voice highlights")
v = [x for x in general.sent[before:] if x["tts"]]
check("voice notes post as text-to-speech messages", v and v[0]["text"].startswith("🔊"), general.sent[before:])

# ---------- reminders come from the ticker ----------
poll = app.bot.state["dc111"]["poll"]
start = datetime.fromisoformat(poll["options"][poll["winner"]]["start"])
clock["now"] = start - timedelta(hours=2) + timedelta(minutes=1)
before = len(general.sent)
app.tick()
time.sleep(0.2)
check("reminders post into the Discord channel on their own", any("2 hours till" in (x["text"] or "") for x in general.sent[before:]), general.sent[before:])

# ---------- @squad board: a private sign-in link to the website ----------
before = len(amrish.sent)
out = say(amrish, general, "@squad board")
link = [x["text"] for x in amrish.sent[before:] if "/l/" in (x["text"] or "")]
check("@squad board DMs a private link, and says so in the channel", link and out and "DMed you" in out[0], (out, amrish.sent[before:]))
code = link[0].rsplit("/l/", 1)[1].strip()
token, wu, rm = app.use_link(code)
check("the link signs you in as your Discord self, opened on this channel", wu["handle"] == "dc:1" and rm == "dc111" and wu["name"] == "Amrish")
try:
    app.use_link(code)
    check("links work only once", False)
except web.ApiError as e:
    check("links work only once", e.status == 410)
f = app.feed(wu, "dc111", 0)
check("the website shows the channel's chat and Board", f["room"]["source"] == "discord" and f["board"].get("plan", {}).get("status") == "locked"
      and any("wifi" in m["text"] for m in f["messages"]), f["board"].get("plan"))
check("reactions from Discord show on the website", any(m.get("reactions") for m in f["messages"]))

# ---------- typing on the website posts in Discord ----------
before = len(general.sent)
app.post(wu, "dc111", "running 5 min late")
time.sleep(0.2)
check("messages from the website are posted in the Discord channel",
      any("**Amrish** (on the web): running 5 min late" == x["text"] for x in general.sent[before:]), general.sent[before:])
before = len(general.sent)
app.post(wu, "dc111", "@squad weather")
time.sleep(0.2)
check("bot replies to website messages reach Discord too", len(general.sent[before:]) >= 2, general.sent[before:])

# ---------- privacy ----------
stranger_token, stranger = app.join("Stranger")
try:
    app.join_room(stranger, "dc111")
    check("a Discord squad can't be joined by guessing its code", False)
except web.ApiError as e:
    check("a Discord squad can't be joined by guessing its code", e.status == 403)

# ---------- calling the bot by name ----------
out = say(jake, general, "hi radar")
check("'hi radar' gets a hello", out and "hey" in out[0].lower() and "what do you need" in out[0], out)
out = say(jake, general, "thanks squad radar")
check("'thanks squad radar' gets a reply", out and "anytime" in out[0], out)
out = say(jake, general, "what's for sale radar?")
check("name at the end works too", out and ("for sale" in out[0] or "nothing matching" in out[0]), out)
out = say(jake, general, "radar who still owes money?")
check("a free-form question by name gets a model answer with the chat as context", out and "tacos" in out[0], out)
check("ordinary chat that mentions radar in passing is left alone", say(jake, general, "the speed radar on rural got me lol") == [])

# ---------- several commands in one message ----------
out = say(amrish, general, "@squad help\n@squad pins")
check("two @squad lines in one message both get answered", len(out) == 2 and "Squad Radar" in out[0] and "pin" in out[1].lower(), out)

# ---------- @squad simulate: a demo server full of friends ----------
import discord as _d  # noqa: E402
bridge.client.user  # property exists
type(bridge.client).user = property(lambda self: type("U", (), {"id": 4242})())
Perms.manage_channels = False
out = say(amrish, general, "@squad simulate")
check("without channel permissions it explains and gives a re-invite link", out and "Manage Channels" in out[0] and "permissions=" in out[0], out)
Perms.manage_channels = True
import time as _t
_orig_sleep = asyncio.sleep
async def _fast(x, *a, **k):
    return await _orig_sleep(0)
asyncio.sleep = _fast
t0 = _t.time()
out = say(amrish, general, "@squad help\n@squad simulate")
asyncio.sleep = _orig_sleep
cat = next((c for c in guild.categories if c.name == "Squad Radar demo"), None)
names = [c.name for c in cat.text_channels] if cat else []
check("simulate builds the scripted channels plus the extra groups", names[:5] == ["apartment-4b", "weekend-plans", "cse-310-project", "move-out-market", "intl-friends"]
      and len(names) == 13 and "isa-events" in names and "spanish-club" in names, names)
iso = next(c for c in cat.text_channels if c.name == "isa-events")
check("extra groups start with their own friends' messages", {x.get("as") for x in iso.sent if x.get("as")} >= {"Kavya", "Rohan"}, [x.get("as") for x in iso.sent])
check("extra-group friends get profiles with classes", any(c.get("label") == "BIO 181" for c in app.bot.profiles.get("Kavya", {}).get("classes", [])))
check("live chat starts after the build", bridge.live_task is not None and (bridge.live or bridge.live_count > 0),
      (bridge.live, bridge.live_count, bridge.live_task and bridge.live_task.done() and bridge.live_task.exception()))
check("simulate announces itself and finishes", any("Building a demo" in x for x in out) and any("Demo ready" in x for x in out), out)
apt = cat.text_channels[0]
persona_posts = [x for x in apt.sent if x.get("as")]
check("friends post as themselves through a webhook", {x["as"] for x in persona_posts} >= {"Priya", "Jake", "Sam"}, [x.get("as") for x in apt.sent][:6])
check("{you} becomes a real mention of whoever started it", any("<@1>" in (x["text"] or "") for x in persona_posts))
bot_lines = [x["text"] for x in apt.sent if not x.get("as")]
check("the bot answers the friends in order (chores, groceries, bill)", any("chores" in (t or "") for t in bot_lines) and any("list:" in (t or "") for t in bot_lines)
      and any("Priya paid" in (t or "") for t in bot_lines), bot_lines)
landlord = next(x["msg"] for x in persona_posts if "landlord" in x["text"])
check("scripted reactions are added and pin the message", "📌" in landlord.reactions and any(
    h.get("pinned") for h in app.bot.state["dc%d" % apt.id]["history"] if "landlord" in h["text"]))
wk = cat.text_channels[1]
check("the weekend vote locks with a calendar invite", any((x.get("file") or "").endswith(".ics") for x in wk.sent), [x["text"] for x in wk.sent][-5:])
mk = cat.text_channels[3]
check("the market runs: holds, offers, the bike match alert", any("on hold for 24h" in (x["text"] or "") for x in mk.sent)
      and any("matches what you're looking for" in (x["text"] or "") for x in mk.sent))
check("sim friends have real schedules (Maya's class is known)", any(c.get("label") == "SPA 202" for c in app.bot.profiles.get("Maya", {}).get("classes", [])))
demo_rooms = [r for r in app.db["rooms"].values() if r.get("demo")]
check("every demo channel is on the website, open to anyone signed in", len(demo_rooms) == 13, len(demo_rooms))
tkn, viewer = app.join("Judge")
listed = [r["code"] for r in app.me(viewer)["rooms"] if r["demo"]]
check("a new website visitor sees the demo channels", len(listed) == 13, listed)

# ---------- live mode: the groups keep talking ----------
bridge.live = False
time.sleep(0.3)
bridge.live_every, start_count = 0.01, bridge.live_count
bridge.live_max = start_count + 6
asyncio.sleep = _fast
out = say(amrish, general, "@squad live on")
deadline = _t.time() + 10
while bridge.live and _t.time() < deadline:
    _t.sleep(0.1)
asyncio.sleep = _orig_sleep
check("@squad live on starts the live chat", out and "live demo chat is on" in out[0], out)
posted = [x for c in cat.text_channels for x in c.sent if x.get("as")]
check("live mode keeps posting new friend messages (model-written)", bridge.live_count >= start_count + 4
      and any(x["text"] == "anyone free this weekend??" for x in posted), (bridge.live_count, start_count))
check("live mode only uses real members of the group", not any(x.get("as") == "Nobody" for x in posted))
check("it stops at the limit so it can't burn the model quota", not bridge.live and bridge.live_count <= bridge.live_max + 2)
out = say(amrish, general, "@squad live")
check("@squad live shows the status", out and "live demo chat is off" in out[0], out)
saved_json = FakeModel.json
FakeModel.json = lambda self, user, system="": None
bridge.live_max = bridge.live_count + 2
asyncio.sleep = _fast
say(amrish, general, "@squad live on")
deadline = _t.time() + 10
while bridge.live and _t.time() < deadline:
    _t.sleep(0.1)
asyncio.sleep = _orig_sleep
FakeModel.json = saved_json
check("without the model, live mode uses each group's scripted lines", bridge.live_count >= bridge.live_max - 1)
fd = app.feed(viewer, "dc%d" % apt.id, 0)
check("and their chat and Board", fd["room"]["demo"] and fd["board"].get("groceries") and fd["board"].get("chores"), list(fd["board"]))
res = app.assist(viewer, "dc%d" % apt.id, "summary")
check("website assistant: summary (model-written, labeled)", res["text"] and res["ai"] == "gemini-3.6-flash" and res["speech"], res)
res = app.assist(viewer, "dc%d" % apt.id, "highlights")
check("website assistant: what matters", "landlord" in res["text"] and "remind #1" not in res["text"], res["text"][:300])
res = app.assist(viewer, "dc%d" % apt.id, "translate", text="rent is due friday", lang="tamil")
check("website assistant: translate", res["text"] and res["ai"], res)
when = (clock["now"] + timedelta(hours=1)).replace(tzinfo=None).isoformat(timespec="minutes")
res = app.assist(viewer, "dc%d" % apt.id, "remind", text="take out the trash", at=when)
check("website assistant: reminders", "Reminder set" in res["text"] and "Discord" in res["text"], res)
clock["now"] += timedelta(hours=1, minutes=1)
before = len(apt.sent)
app.tick(); time.sleep(0.3)
check("the website's reminder posts in the Discord channel on time", any("take out the trash" in (x["text"] or "") for x in apt.sent[before:]), apt.sent[before:])
# ---------- a restarted (or brand new) server rebuilds the demo from Discord ----------
type(bridge.client).guilds = property(lambda self: [guild])
os.environ["SQUAD_LIVE_AUTOSTART"] = "0"
app2 = web.App(tempfile.mkdtemp(), llm=FakeModel(), clock=lambda: clock["now"], samples=False)
bridge2 = DiscordBridge(app2, "fake-token", "", website=True)
check("a fresh server starts with no demo channels", not [r for r in app2.db["rooms"].values() if r.get("demo")])
asyncio.run_coroutine_threadsafe(bridge2.restore(), loop).result(30)
restored = [r for r in app2.db["rooms"].values() if r.get("demo")]
check("on startup it finds all 13 demo channels and puts them back on the website", len(restored) == 13, len(restored))
apt2 = app2.db["rooms"]["dc%d" % apt.id]
check("with their recent messages, friends and bot replies in order",
      any(m["who"] == "Priya" and "landlord" in m["text"] for m in apt2["messages"]) and any(m["kind"] == "bot" for m in apt2["messages"])
      and not any("written by" in (m["text"] or "") for m in apt2["messages"]), [m["who"] for m in apt2["messages"]][:8])
check("the bot remembers them too, so tldr and highlights work right away",
      any("landlord" in h["text"] for h in app2.bot.state["dc%d" % apt.id]["history"]))
check("restoring twice doesn't duplicate anything", asyncio.run_coroutine_threadsafe(bridge2.restore(), loop).result(30) is None
      and len(app2.db["rooms"]["dc%d" % apt.id]["messages"]) == len(apt2["messages"]))
res = app2.assist(app2.join("Visitor")[1], "dc%d" % apt.id, "highlights")
check("the restored board works on the website", "landlord" in res["text"], res["text"][:200])
h2 = app2.llm_status()
check("health explains a missing model key", web.App(tempfile.mkdtemp(), llm=type("M", (), {"enabled": False, "status": lambda self: {"enabled": False}})()).llm_status().get("fix", "").startswith("No model key"))
os.environ.pop("SQUAD_LIVE_AUTOSTART")

asyncio.sleep = _fast
out = say(amrish, general, "@squad simulate reset")
asyncio.sleep = _orig_sleep
check("simulate reset removes the demo channels and their website copies", not [c for c in guild.categories if c.name == "Squad Radar demo"]
      and not [r for r in app.db["rooms"].values() if r.get("demo")], out)

# ---------- channel allowlist ----------
bridge.allow = {"general"}
seen = len(app.bot.state.get("dc222", {}).get("history", []))
out = say(amrish, other, "@squad help") + say(amrish, other, "rent is due friday")
check("with DISCORD_CHANNELS set, other channels are ignored", out == [] and len(app.bot.state.get("dc222", {}).get("history", [])) == seen, out)

# ---------- web still works alongside ----------
token, u = app.join("Webby")
r = app.create_room(u, "Web squad")
new = app.post(u, r["code"], "@squad help")
check("the web app keeps working next to Discord", any(x["kind"] == "bot" for x in new))

print("\n%s (%d failed)" % ("ALL PASSED" if not fails else "SOME FAILED", fails))
sys.stdout.flush()
os._exit(1 if fails else 0)  # skip tearing down the background event loop
