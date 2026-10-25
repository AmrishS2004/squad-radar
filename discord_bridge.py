"""Squad Radar on Discord. Each server channel the bot can see is a squad; DMs to the bot are private setup.

Runs inside web.py when DISCORD_TOKEN is set, so one deploy serves the web app and Discord together.
Setup: discord.com/developers/applications -> New Application -> Bot -> Reset Token (that's DISCORD_TOKEN),
turn on "Message Content Intent", then OAuth2 -> URL Generator -> scopes "bot", permissions: View Channels,
Send Messages, Attach Files, Read Message History, Add Reactions, Send TTS Messages -> open the link to invite it.
Optional: DISCORD_CHANNELS=general,squad (names or IDs) limits which channels it listens in."""
import asyncio
import copy
import json
import os
import random
import re
import threading
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
SIM_FILE = os.path.join(HERE, "data", "discord_sim.json")
GROUPS_FILE = os.path.join(HERE, "data", "discord_groups.json")
LIVE = re.compile(r"(?i)^\s*@?squad\s+live\b\s*(on|off|start|stop|pause|resume|status)?")
GREETING = r"(?:hi+|hey+|hello+|yo+|ok|okay|thanks|thank you|ty|gm|sup)"
SIM = re.compile(r"(?i)^\s*@?squad\s+(?:simulate|simulation|demo)\b\s*(reset|remove|clear|stop)?")
HOOK_NAME = "Squad Radar sim"
# View Channels, Send Messages, Send TTS, Attach Files, Read History, Add Reactions, Manage Channels, Manage Webhooks
PERMISSIONS = 1024 | 2048 | 4096 | 32768 | 65536 | 64 | 16 | 536870912
# Discord reactions that count like iMessage tapbacks. ‼️ and 📌 pin the message.
EMOJI = {"❤️": "love", "❤": "love", "♥️": "love", "😍": "love", "👍": "like", "😂": "laugh", "🤣": "laugh", "‼️": "emphasize",
         "📌": "emphasize", "❗": "emphasize", "❓": "question", "👎": "dislike"}


def handle_of(user_id):
    return "dc:%d" % int(user_id)


def chat_of(channel_id):
    return "dc%d" % int(channel_id)  # also the squad's code on the website


def is_discord_chat(cid):
    return cid.startswith("dc") and cid[2:].isdigit()


BOARD = re.compile(r"(?i)^\s*@?squad\s+(board|web|website|dashboard|link|open)\b")


def normalize(content, bot_id, names):
    """Turn Discord's <@123> mentions into names the bot understands; mentioning the bot means "@squad"."""
    t = content or ""
    if bot_id:
        t = re.sub(r"<@!?%d>" % int(bot_id), "@squad", t)
        t = re.sub(r"^\s*@squad\s*@squad", "@squad", t)
    t = re.sub(r"<@!?(\d+)>", lambda m: "@" + names.get(int(m.group(1)), "someone").split()[0], t)
    t = re.sub(r"<#\d+>", "#channel", t)
    t = re.sub(r"<a?:(\w+):\d+>", r":\1:", t)  # custom emoji
    return t.strip()


CMD_LINE = re.compile(r"(?i)^\s*(?:@squad\b|hey squad\b)")


def split_commands(text):
    """One Discord message can hold several commands on separate lines ("@squad help" then "@squad simulate").
    Each line that starts with @squad begins a new command; other lines stay with the one before (a bill's items)."""
    parts, cur = [], []
    for line in (text or "").split("\n"):
        if cur and CMD_LINE.match(line):
            parts.append("\n".join(cur).strip())
            cur = []
        cur.append(line)
    if cur:
        parts.append("\n".join(cur).strip())
    return [p for p in parts if p] or [text or ""]


def address(text, bot_names, is_reply=False):
    """Calling the bot by name works like @squad: "hi radar", "hey squad radar what's for sale?", "thanks radar",
    or replying to one of its messages."""
    t = (text or "").strip()
    if re.match(r"(?i)^\s*(@?squad\b|hey squad\b)(?!\s*radar)", t):
        return t
    names = sorted({n.lower() for n in bot_names if n} | {"squad radar", "square radar"}, key=len, reverse=True)
    full = "|".join(re.escape(n) for n in names)
    m = re.match(r"(?i)^\s*(%s)?[\s,!]*@?(?:%s|radar)\b[\s,:!.?-]*(.*)$" % (GREETING, full), t, re.S)
    if m:
        rest = m.group(2).strip()
        return "@squad " + (rest or (m.group(1) or "hi"))
    m = re.match(r"(?i)^(.*?)[\s,]+@?(?:%s|radar)[\s!.?]*$" % full, t, re.S)
    if m and m.group(1).strip():
        return "@squad " + m.group(1).strip()
    if re.search(r"(?i)\b(?:%s)\b" % full, t):
        return "@squad " + re.sub(r"(?i)@?\b(?:%s)\b[,]?\s*" % full, "", t).strip()
    if is_reply:
        return "@squad " + t
    return t


def chunks(text, limit=1900):
    """Discord caps messages at 2,000 characters; split on line breaks."""
    out, cur = [], ""
    for line in (text or "").split("\n"):
        while len(line) > limit:
            out.append(line[:limit]); line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            out.append(cur); cur = line
        else:
            cur = (cur + "\n" + line) if cur else line
    if cur:
        out.append(cur)
    return out or [""]


def render(o, ai=None):
    """What to post on Discord for one bot output: (text, file_path, tts)."""
    if o.get("file"):
        path = o["file"]
        if path.endswith(".speech.json"):
            try:
                v = json.load(open(path, encoding="utf-8"))
            except (OSError, ValueError):
                return None
            return (v.get("text", ""), None, True)  # read aloud by Discord's text-to-speech
        return ("", path, False)
    text = o.get("text", "")
    if ai and text:
        text += "\n-# written by %s" % ai
    return (text, None, False)


class DiscordBridge:
    def __init__(self, app, token, channels="", website=True):
        import discord  # imported here so the rest of Squad Radar never needs it
        self.discord = discord
        self.app, self.token, self.website = app, token, website
        self.allow = {c.strip().lower().lstrip("#") for c in (channels or "").split(",") if c.strip()}
        intents = discord.Intents.default()
        intents.message_content = True
        intents.reactions = True
        intents.dm_messages = True
        self.client = discord.Client(intents=intents)
        self.loop = None
        self.queue = None
        self.sim_running = False
        self.live = False
        self.live_task = None
        self.live_count = 0
        self.live_every = float(os.environ.get("SQUAD_LIVE_EVERY") or 30)
        self.live_max = int(os.environ.get("SQUAD_LIVE_MAX") or 300)
        self.quiet = bool(os.environ.get("SQUAD_QUIET"))
        with app.db_lock:
            self.members = app.db.setdefault("discord", {})  # chat id -> handles who've talked there
        app.senders.append(self.send)
        c = self.client

        @c.event
        async def on_ready():
            print("Discord: signed in as %s, in %d server(s)" % (c.user, len(c.guilds)))

        @c.event
        async def on_message(m):
            await self.on_message(m)

        @c.event
        async def on_raw_reaction_add(p):
            await self.on_reaction(p, False)

        @c.event
        async def on_raw_reaction_remove(p):
            await self.on_reaction(p, True)

    # ---------- start ----------
    def start(self):
        threading.Thread(target=self._run, name="discord", daemon=True).start()

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.queue = asyncio.Queue()
        self.loop.create_task(self._sender())
        try:
            self.loop.run_until_complete(self.client.start(self.token))
        except self.discord.LoginFailure:
            print("Discord: the token was refused. Copy a fresh one from the Bot page (Reset Token) into DISCORD_TOKEN.")
        except self.discord.PrivilegedIntentsRequired:
            print("Discord: turn on 'Message Content Intent' on the Bot page of your app, then restart.")
        except Exception as e:  # noqa: BLE001  (the web app keeps running either way)
            print("Discord: couldn't connect (%s).%s" % (str(e)[:160] or type(e).__name__,
                                                         " The web app is still running." if self.website else " Check your internet and the token, then try again."))

    # ---------- incoming ----------
    def listening(self, channel):
        if not self.allow:
            return True
        return str(channel.id) in self.allow or getattr(channel, "name", "").lower() in self.allow

    @staticmethod
    def title(channel):
        guild = getattr(getattr(channel, "guild", None), "name", None)
        return "#%s%s" % (getattr(channel, "name", "channel"), (" (%s)" % guild) if guild else "")

    def names(self):
        out = {}
        for n, p in self.app.bot.profiles.items():
            for h in p.get("handles", []):
                if h.startswith("dc:"):
                    out[int(h[3:])] = n
        return out

    async def on_message(self, m):
        if m.webhook_id or m.author.bot or (not m.content and not m.attachments):
            return  # the simulation posts persona messages itself and feeds them to the bot in order
        try:
            await self._on_message(m)
        except Exception:  # noqa: BLE001
            print("Discord: error handling a message:\n" + traceback.format_exc())

    async def _on_message(self, m):
        d = self.discord
        handle = handle_of(m.author.id)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.app.ensure_profile, handle, m.author.display_name)
        text = normalize(m.content, self.client.user.id if self.client.user else None, self.names())
        ref = getattr(getattr(m, "reference", None), "resolved", None)
        is_reply = bool(ref and self.client.user and getattr(getattr(ref, "author", None), "id", None) == self.client.user.id)
        bot_names = [getattr(self.client.user, "name", ""), getattr(self.client.user, "display_name", "")] if self.client.user else []
        text = "\n".join(address(line, bot_names, is_reply and i == 0) if i == 0 or CMD_LINE.match(line) is None else line
                         for i, line in enumerate(text.split("\n")))
        if not self.quiet:
            where = "DM" if isinstance(m.channel, d.DMChannel) else "#" + getattr(m.channel, "name", "?")
            print("Discord: %s %s: %s" % (where, m.author.display_name, text.replace("\n", " | ")[:120]))
        parts = split_commands(text)
        for i, part in enumerate(parts):
            await self._handle(m, handle, part, loop, first=(i == 0), guid="dc-%d" % m.id if i == 0 else "dc-%d-%d" % (m.id, i))

    async def _handle(self, m, handle, text, loop, first=True, guid=None):
        d = self.discord
        lv = LIVE.match(text)
        if lv and not isinstance(m.channel, d.DMChannel):
            await self.live_command(m, (lv.group(1) or "status").lower())
            return
        sim = SIM.match(text)
        if sim and not isinstance(m.channel, d.DMChannel):
            await self.simulate(m, reset=bool(sim.group(1)))
            return
        if BOARD.match(text) and self.website:  # a private sign-in link to this channel's board on the website
            room = None if isinstance(m.channel, d.DMChannel) else chat_of(m.channel.id)
            if room and not self.listening(m.channel):
                return
            if room:
                await loop.run_in_executor(None, lambda: self.app.mirror_room(room, self.title(m.channel), [handle]))
            link = self.app.make_link(handle, room)
            try:
                await m.author.send("Here's your board%s on the website (private, works once, expires in 30 minutes):\n%s" % (
                    (" for " + self.title(m.channel)) if room else "", link))
                if room:
                    await m.channel.send("📬 %s, I DMed you a private link to this channel's board." % m.author.display_name,
                                         allowed_mentions=d.AllowedMentions.none())
            except d.Forbidden:
                await m.channel.send("I can't DM you. Turn on DMs from server members (Server Settings → Privacy), then try again.")
            return
        if isinstance(m.channel, d.DMChannel):
            if text:
                await loop.run_in_executor(None, lambda: self.app.external_message(None, handle, text, [handle], private=True))
            return
        if not self.listening(m.channel) and not self.is_demo(m.channel):
            if not self.quiet:
                print("Discord: ignoring #%s (not in DISCORD_CHANNELS)" % getattr(m.channel, "name", "?"))
            return
        cid = chat_of(m.channel.id)
        with self.app.db_lock:
            greeted = self.app.db.setdefault("discord_greeted", [])
            first_time = cid not in greeted and not self.is_demo(m.channel)
            if first_time:
                greeted.append(cid)
        if first_time and not re.match(r"(?i)^\s*(@?squad\b|hey squad)", text):
            await m.channel.send(
                "hey 👋 I'm Squad Radar. I stay quiet until someone needs me, then I plan around everyone's classes, split bills, "
                "run move-out sales and catch you up on what you missed.\n"
                "Try `@squad help`, or `@squad simulate` to watch me work in a demo full of friends.",
                allowed_mentions=self.discord.AllowedMentions.none())
        with self.app.db_lock:
            members = self.members.setdefault(cid, [])
            if handle not in members:
                members.append(handle)
            members = list(members)
        files = []
        for a in (m.attachments[:3] if first else []):
            if (a.content_type or "").startswith("image/") and a.size < 8_000_000:
                path = os.path.join(self.app.files, "dc-%d-%s" % (a.id, re.sub(r"[^\w.-]", "_", a.filename)))
                await a.save(path)
                files.append(path)
        if not text and files:
            text = "(photo)"
        typing = m.channel.typing() if re.match(r"(?i)^\s*(@?squad\b|hey squad)", text) else None
        if typing:
            async with typing:
                await loop.run_in_executor(None, lambda: self.app.external_message(cid, handle, text, members, guid, files,
                                                                                    title=self.title(m.channel), demo=self.is_demo(m.channel)))
        else:
            await loop.run_in_executor(None, lambda: self.app.external_message(cid, handle, text, members, guid, files,
                                                                                title=self.title(m.channel), demo=self.is_demo(m.channel)))

    async def on_reaction(self, p, removed):
        if self.client.user and p.user_id == self.client.user.id:
            return
        kind = EMOJI.get(str(p.emoji))
        if not kind or not p.guild_id:
            return
        cid = chat_of(p.channel_id)
        with self.app.db_lock:
            members = list(self.members.get(cid, []))
        await asyncio.get_running_loop().run_in_executor(
            None, self.app.external_reaction, cid, handle_of(p.user_id), members, "dc-%d" % p.message_id, kind, removed)

    # ---------- simulation: a demo server full of friends chatting ----------
    def is_demo(self, channel):
        cat = getattr(channel, "category", None)
        try:
            name = json.load(open(SIM_FILE, encoding="utf-8")).get("category", "Squad Radar demo")
        except (OSError, ValueError):
            name = "Squad Radar demo"
        return bool(cat and cat.name == name)

    def invite_url(self):
        return "https://discord.com/oauth2/authorize?client_id=%d&permissions=%d&integration_type=0&scope=bot" % (
            self.client.user.id, PERMISSIONS)

    def ensure_personas(self, personas):
        """Sim friends get real schedules so planning has something to work around. Returns name -> handle."""
        out = {}
        with self.app.lock:
            for p in personas:
                name, handle = p["name"], "sim:" + p["name"].lower()
                existing = self.app.bot.profiles.get(name)
                if existing and handle in existing.get("handles", []):
                    out[p["name"]] = handle
                    continue
                if existing and any(h.startswith(("dc:", "web:")) for h in existing.get("handles", [])):
                    name = name + " (demo)"  # a real person already has this name
                if name in self.app.bot.profiles:
                    if handle not in self.app.bot.profiles[name].setdefault("handles", []):
                        self.app.bot.profiles[name]["handles"].append(handle)
                else:
                    src = p.get("profile")
                    if isinstance(src, str):
                        prof = json.load(open(os.path.join(HERE, "profiles", src + ".json"), encoding="utf-8"))
                    else:
                        prof = copy.deepcopy(src or {})
                    prof.update({"name": name, "handles": [handle]})
                    for k, v in (("classes", []), ("busy", []), ("exams", []), ("prefs", {}), ("project", {}), ("share_reasons", False)):
                        prof.setdefault(k, v)
                    self.app.bot.profiles[name] = prof
                self.app.bot.save_profile(name)
                out[p["name"]] = handle
        return out

    async def simulate(self, m, reset=False):
        d = self.discord
        g = m.guild
        perms = g.me.guild_permissions
        if not (perms.manage_channels and perms.manage_webhooks):
            await m.channel.send("To build the demo I need **Manage Channels** and **Manage Webhooks**. Open this link and "
                                 "authorize me again for this server (nothing else changes):\n" + self.invite_url())
            return
        try:
            data = json.load(open(SIM_FILE, encoding="utf-8"))
        except (OSError, ValueError) as e:
            await m.channel.send("I couldn't read data/discord_sim.json (%s)." % e)
            return
        cat = d.utils.get(g.categories, name=data["category"])
        loop = asyncio.get_running_loop()
        if reset:
            if not cat:
                await m.channel.send("There's no demo to remove.")
                return
            self.live = False
            names = {c["name"] for c in data["channels"]} | {gr["name"] for gr in self.load_groups().get("groups", [])}
            inside = getattr(m.channel, "category", None) == cat
            for ch in list(cat.text_channels):
                if ch.name in names:
                    await loop.run_in_executor(None, self.app.forget_room, chat_of(ch.id))
                    await ch.delete(reason="Squad Radar demo reset")
            if not cat.channels:
                await cat.delete(reason="Squad Radar demo reset")
            if not inside:
                await m.channel.send("Removed the demo channels 🧹")
            return
        if self.sim_running:
            await m.channel.send("A demo is already being built, give it a minute.")
            return
        self.sim_running = True
        you = m.author
        first = you.display_name.split()[0]
        try:
            await m.channel.send("Building a demo: %d channels of friends chatting under **%s**. I'll tag you, %s, where you can "
                                 "jump in. Takes about two minutes." % (len(data["channels"]), data["category"], first))
            personas = await loop.run_in_executor(None, self.ensure_personas, data["personas"])
            await loop.run_in_executor(None, self.app.ensure_profile, handle_of(you.id), you.display_name)
            if not cat:
                cat = await g.create_category(data["category"], reason="Squad Radar demo")
            made = []
            for spec in data["channels"]:
                ch = d.utils.get(cat.text_channels, name=spec["name"]) or await g.create_text_channel(
                    spec["name"], category=cat, topic=spec.get("topic") or None, reason="Squad Radar demo")
                hook = next((w for w in await ch.webhooks() if w.name == HOOK_NAME), None) or await ch.create_webhook(name=HOOK_NAME)
                cid, title = chat_of(ch.id), self.title(ch)
                with self.app.db_lock:
                    self.members[cid] = list(dict.fromkeys(self.members.get(cid, []) + list(personas.values()) + [handle_of(you.id)]))
                    members = list(self.members[cid])
                await loop.run_in_executor(None, lambda: self.app.mirror_room(cid, title, members, demo=True))
                posted = []
                for step in spec["messages"]:
                    who = step["who"]
                    if who not in personas:
                        continue
                    if "react" in step:
                        if not posted:
                            continue
                        target = posted[step.get("to", -1)]
                        try:
                            await target.add_reaction(step["react"])
                        except Exception:  # noqa: BLE001
                            pass
                        await loop.run_in_executor(None, self.app.external_reaction, cid, personas[who], members,
                                                   "dc-%d" % target.id, EMOJI.get(step["react"], "like"), False)
                        continue
                    shown = step["text"].replace("{you}", "<@%d>" % you.id)
                    text = step["text"].replace("{you}", first)
                    msg = await hook.send(shown, username=who, wait=True,
                                          allowed_mentions=d.AllowedMentions(everyone=False, roles=False, users=True))
                    posted.append(msg)
                    if not self.quiet:
                        print("Discord: #%s %s (demo): %s" % (ch.name, who, text.replace("\n", " ")[:80]))
                    await loop.run_in_executor(None, lambda: self.app.external_message(
                        cid, personas[who], text, members, "dc-%d" % msg.id, title=title, demo=True))
                    await self.queue.join()  # let the bot's replies land before the next friend talks
                    await asyncio.sleep(float(step.get("pause", 1.0)))
                made.append(ch)
            # more groups, each with its own friends, seeded and then kept alive by live mode
            groups = self.load_groups()
            extra = await loop.run_in_executor(None, self.ensure_personas, self.group_personas(groups))
            everyone = dict(personas, **extra)
            for gspec in groups.get("groups", []):
                ch = d.utils.get(cat.text_channels, name=gspec["name"]) or await g.create_text_channel(
                    gspec["name"], category=cat, topic=gspec.get("topic") or None, reason="Squad Radar demo")
                hook = next((w for w in await ch.webhooks() if w.name == HOOK_NAME), None) or await ch.create_webhook(name=HOOK_NAME)
                cid, title = chat_of(ch.id), self.title(ch)
                with self.app.db_lock:
                    self.members[cid] = list(dict.fromkeys(self.members.get(cid, []) + [everyone[n] for n in gspec["members"] if n in everyone]
                                                           + [handle_of(you.id)]))
                    members = list(self.members[cid])
                await loop.run_in_executor(None, lambda: self.app.mirror_room(cid, title, members, demo=True))
                slot = {"channel": ch, "hook": hook, "cid": cid, "title": title, "spec": gspec, "personas": everyone}
                for step in gspec.get("seed", []):
                    await self.post_as(slot, step["who"], step["text"], mention_id=you.id)
                    await asyncio.sleep(1.0)
                made.append(ch)
            await self.start_live(g, self.live_specs(g), m.channel)
            await m.channel.send(
                "Demo ready ✅ %d channels: %s\nLive chat is on: the friends keep talking in these channels about every %ds%s, "
                "and I answer them like a real group. Jump in anywhere, or try `@squad highlights`, `@squad tldr`, `hi radar`.\n"
                "`@squad live off` pauses it, `@squad simulate reset` removes everything." % (
                    len(made), " ".join(c.mention for c in made), int(self.live_every),
                    "" if getattr(self.app.bot.llm, "enabled", False) else " (scripted lines, add a model key for fresh conversation)"))
        except d.Forbidden:
            await m.channel.send("Discord blocked me from creating channels or webhooks. Re-authorize me with:\n" + self.invite_url())
        except Exception:  # noqa: BLE001
            print("Discord: simulation failed:\n" + traceback.format_exc())
            await m.channel.send("Something went wrong building the demo. The terminal running the bot has the details.")
        finally:
            self.sim_running = False

    # ---------- live mode: groups that keep chatting on their own ----------
    def load_groups(self):
        try:
            return json.load(open(GROUPS_FILE, encoding="utf-8"))
        except (OSError, ValueError):
            return {"personas": {}, "groups": []}

    def group_personas(self, groups):
        """Turn the short persona specs in discord_groups.json into full profiles (classes, dorm, language)."""
        out = []
        for name, p in groups.get("personas", {}).items():
            prof = {"classes": [{"days": c[0], "start": c[1], "end": c[2], "label": c[3]} for c in p.get("classes", [])],
                    "busy": [{"days": b[0], "start": b[1], "end": b[2], "label": b[3]} for b in p.get("busy", [])],
                    "exams": [], "share_reasons": False,
                    "prefs": {k: v for k, v in (("dorm", p.get("dorm")), ("language", p.get("language"))) if v},
                    "project": {"hours_this_week": 5, "skills": [], "avoid": []}, "vibe": p.get("vibe", "")}
            out.append({"name": name, "profile": prof})
        return out

    def live_specs(self, guild):
        """Every demo channel with who's in it and what it's about, for live mode."""
        try:
            sim = json.load(open(SIM_FILE, encoding="utf-8"))
        except (OSError, ValueError):
            return []
        groups = self.load_groups()
        cat = self.discord.utils.get(guild.categories, name=sim.get("category", "Squad Radar demo"))
        if not cat:
            return []
        base = [p["name"] for p in sim.get("personas", [])]
        specs = {c["name"]: {"about": c.get("topic", ""), "members": base} for c in sim.get("channels", [])}
        specs.update({g["name"]: {"about": g.get("about", g.get("topic", "")), "members": g["members"], "lines": g.get("lines", [])}
                      for g in groups.get("groups", [])})
        return [(ch, specs[ch.name]) for ch in cat.text_channels if ch.name in specs]

    async def live_command(self, m, arg):
        if arg in ("off", "stop", "pause"):
            self.live = False
            await m.channel.send("⏸️ live demo chat paused after %d messages. `@squad live on` to continue." % self.live_count)
            return
        if arg in ("on", "start", "resume"):
            if self.live:
                await m.channel.send("live chat is already running (%d messages so far)." % self.live_count)
                return
            specs = self.live_specs(m.guild)
            if not specs:
                await m.channel.send("there's no demo yet. `@squad simulate` builds it and starts live chat.")
                return
            await self.start_live(m.guild, specs, m.channel)
            await m.channel.send("▶️ live demo chat is on in %d channels, a new message about every %ds. `@squad live off` to pause." % (
                len(specs), int(self.live_every)))
            return
        await m.channel.send("live demo chat is %s (%d messages so far, every ~%ds). `@squad live on` / `@squad live off`." % (
            "on" if self.live else "off", self.live_count, int(self.live_every)))

    async def start_live(self, guild, specs, control):
        groups = self.load_groups()
        names = set()
        for _, spec in specs:
            names |= set(spec["members"])
        sim_personas = json.load(open(SIM_FILE, encoding="utf-8")).get("personas", [])
        wanted = [p for p in sim_personas + self.group_personas(groups) if p["name"] in names]
        loop = asyncio.get_running_loop()
        personas = await loop.run_in_executor(None, self.ensure_personas, wanted)
        slots = []
        for ch, spec in specs:
            hook = next((w for w in await ch.webhooks() if w.name == HOOK_NAME), None) or await ch.create_webhook(name=HOOK_NAME)
            cid = chat_of(ch.id)
            with self.app.db_lock:
                self.members[cid] = list(dict.fromkeys(self.members.get(cid, []) + [personas[n] for n in spec["members"] if n in personas]))
                members = list(self.members[cid])
            await loop.run_in_executor(None, lambda: self.app.mirror_room(cid, self.title(ch), members, demo=True))
            slots.append({"channel": ch, "hook": hook, "cid": cid, "title": self.title(ch), "spec": spec, "personas": personas})
        self.live = True
        if self.live_task and not self.live_task.done():
            return
        self.live_task = asyncio.ensure_future(self.live_loop(slots, control))

    async def post_as(self, slot, who, text, mention_id=None):
        """Post one persona message through the channel's webhook and let the bot see it, in order."""
        d = self.discord
        loop = asyncio.get_running_loop()
        handle = slot["personas"].get(who)
        if not handle:
            return None
        shown = text.replace("{you}", "<@%d>" % mention_id) if mention_id else text
        msg = await slot["hook"].send(shown[:1900], username=who, wait=True,
                                      allowed_mentions=d.AllowedMentions(everyone=False, roles=False, users=True))
        bot_names = [getattr(self.client.user, "name", "")] if self.client.user else []
        fed = address(text, bot_names)
        if not self.quiet:
            print("Discord: #%s %s (demo): %s" % (slot["channel"].name, who, fed.replace("\n", " | ")[:100]))
        with self.app.db_lock:
            members = list(self.members.get(slot["cid"], []))
        await loop.run_in_executor(None, lambda: self.app.external_message(
            slot["cid"], handle, fed, members, "dc-%d" % msg.id, title=slot["title"], demo=True))
        await self.queue.join()
        return msg

    def write_next(self, slot):
        """Ask the model for the next few messages in this group, in character. Falls back to scripted lines."""
        spec = slot["spec"]
        room = self.app.db["rooms"].get(slot["cid"]) or {"messages": []}
        recent = [m for m in room["messages"] if m["kind"] in ("user", "bot")][-14:]
        members = [n for n in spec["members"] if n in slot["personas"]]
        llm = self.app.bot.llm
        if llm is not None and getattr(llm, "enabled", False):
            profiles = self.app.bot.profiles
            people = "\n".join("- %s: %s%s" % (n, profiles.get(n, {}).get("vibe") or "college student",
                                                 (", sometimes writes in " + profiles[n]["prefs"]["language"]) if profiles.get(n, {}).get("prefs", {}).get("language") else "")
                               for n in members)
            convo = "\n".join("%s: %s" % ("Squad Radar (the assistant)" if m["kind"] == "bot" else m["who"], (m["text"] or "")[:240].replace("\n", " "))
                              for m in recent) or "(no messages yet)"
            from squad.llm import LIVE_SYSTEM
            res = llm.json("Group: #%s, %s\nMembers:\n%s\n\nRecent messages:\n%s\n\nNow: %s" % (
                slot["channel"].name, spec.get("about", ""), people, convo, self.app.now().strftime("%a %b %d %I:%M %p")), LIVE_SYSTEM)
            msgs = []
            for x in (res or {}).get("messages", [])[:3]:
                who, text = str(x.get("who", "")).strip(), str(x.get("text", "")).strip()
                if who in members and text and not text.lower().startswith("squad radar:"):
                    msgs.append((who, text[:400]))
            if msgs:
                return msgs
        lines = spec.get("lines") or []
        said = {(m["who"], (m["text"] or "").strip()) for m in recent}
        fresh = [l for l in lines if (l["who"], l["text"]) not in said and l["who"] in members]
        pick = random.choice(fresh or lines) if (fresh or lines) else None
        return [(pick["who"], pick["text"])] if pick else []

    async def live_loop(self, slots, control):
        loop = asyncio.get_running_loop()
        try:
            while self.live and self.live_count < self.live_max:
                await asyncio.sleep(self.live_every * random.uniform(0.7, 1.3))
                if not self.live:
                    break
                slot = random.choice(slots)
                try:
                    msgs = await loop.run_in_executor(None, self.write_next, slot)
                    for who, text in msgs:
                        if not self.live:
                            break
                        await self.post_as(slot, who, text)
                        self.live_count += 1
                        await asyncio.sleep(random.uniform(2, 5))
                except Exception:  # noqa: BLE001
                    print("Discord: live chat hiccup:\n" + traceback.format_exc())
            if self.live_count >= self.live_max and control is not None:
                await control.send("⏸️ live demo chat paused after %d messages (the limit, so it doesn't use up your model quota). "
                                   "`@squad live on` to keep going." % self.live_count)
        finally:
            self.live = False

    # ---------- outgoing ----------
    def send(self, o, ai=None):
        """Called by the app for every bot output. Takes the ones meant for Discord and queues them in order."""
        cid = o.get("chat_id", "")
        dm = o.get("dm") or (cid[3:] if cid.startswith("dm:") else None)
        if dm is not None and not dm.startswith("dc:"):
            return False
        if dm is None and not is_discord_chat(cid):
            return False
        if not self.loop:
            return True  # not connected yet; drop rather than crash
        item = (("user", int(dm[3:])) if dm else ("channel", int(cid[2:])), o, ai)
        self.loop.call_soon_threadsafe(self.queue.put_nowait, item)
        return True

    async def _sender(self):
        d = self.discord
        while True:
            (kind, ident), o, ai = await self.queue.get()
            try:
                target = (await self.client.fetch_user(ident)) if kind == "user" else (
                    self.client.get_channel(ident) or await self.client.fetch_channel(ident))
                r = render(o, ai)
                if not r:
                    continue
                text, path, tts = r
                if path:
                    await target.send(file=d.File(path, filename=os.path.basename(path)))
                elif text:
                    parts = chunks(text)
                    for i, part in enumerate(parts):
                        await target.send(("🔊 " if tts and i == 0 else "") + part, tts=tts and i == 0,
                                          allowed_mentions=d.AllowedMentions.none())
            except Exception as e:  # noqa: BLE001
                print("Discord: couldn't send to %s %s: %r" % (kind, ident, e))
                if "Missing Permissions" in str(e) or "Missing Access" in str(e):
                    print("Discord: give the bot's role Send Messages / Attach Files in that channel, or re-invite it.")
            finally:
                self.queue.task_done()
