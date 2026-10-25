# Squad Radar

**The friend in your group chat who handles plans, money, move-out sales and everything in between, and tells you
what actually mattered in the 200 messages you missed.**

Squad Radar is an AI that lives in your iMessage group chat. Someone says "anyone free this weekend??" and instead of
40 messages of "idk when are you free", it answers with two times that fit everyone's classes, something to do, a
vote and a calendar invite. It also splits the bill from a receipt photo, runs move-out sales so nobody's desk gets
buried in the chat, translates for international friends, catches you up on 200 missed messages, and keeps group
projects fair.

Built for the Cubbie Hackathon, **Track 3 (Group Chat Assistant)**. Our Track 1 entry is
[Campus Radar](https://github.com/AmrishS2004/campus-radar).

The full scripted conversation covering every feature is in [demo_transcript.md](demo_transcript.md) (sample
people, sample events, made-up money).

## Everything it does

**📅 Plans**
- "anyone free ...?" → two times that fit everyone's classes, exams, shifts and budgets, plus something to do (free
  food wins). Reply `1`/`2`, then `in`: calendar invite + reminder 2 hours before.
- Says what it worked around ("Jake busy Sat 12-5pm (work shift) · nothing late Sun (Priya has an early start)").
- `@squad decide tacos, sushi` · `@squad game day` (tailgate time, "got tix"/"need tix") · `@squad weather`
- 🥵 **Phoenix heat check**: if a locked outdoor plan is forecast at 100°F+, it suggests a cooler hour (Open-Meteo, no key).

**🛒 Move-out market** (built for big apartment and class group chats)
- `@squad sell IKEA desk $40, lamp $10, mini fridge $50 obo pickup by 5/10 @ Tooker` → numbered listings (#1, #2, #3).
- It notices posts like "selling my air fryer, 30 bucks" and asks the seller "list it?" before listing anything.
- `want #1` → first person gets a **24-hour hold**; everyone else gets a place in line. A hold that runs out passes
  to the next person automatically. `pass #1` gives it up.
- `offer #3 $35` → the seller replies `accept #3 amrish`. `drop #2 $7` announces a price drop to people waiting.
- `sold #1 to sam` (seller only) archives it. `@squad market furniture under $30` filters what's left.
- `@squad looking for mini fridge under $50` pings you the moment a match is listed.
- Two days before pickup day, it nudges sellers to drop the price on what's still unsold. Safe-meetup tip once a day.

**💸 Money**
- `@squad bill` with a receipt photo → numbered items → everyone replies `claim 1 3` → each person's share with
  tax and tip spread fairly, exact to the cent, plus a Venmo link each person taps themselves.
- Or type it: `@squad bill tax 8% tip 18%` / `Jake: burger 14` / `shared: fries 8`.
- `paid` · `@squad owes` · one polite reminder after 2 days.
- `@squad food run kung fu tea 10` → "+ taro milk tea $6.5" → order list and totals when it closes.
  "anyone want boba?" gets an offer to start one.

**✨ Group radar: what matters in the chat**
- `@squad highlights` → the 5 messages that matter out of everything (last 24h, or `today`, `week`, `6h`). It scores
  every message for deadlines and changes ("moved to", "due", "landlord"), times, money, decisions, open questions,
  links, @everyone, and how many ❤️ ‼️ 👍 tapbacks it got. Banter like "lmaooo" and "💀💀" never makes the cut. With a
  Grok key, Grok makes the final pick and says why ("← your rent money").
- Filters: `@squad highlights money` · `plans` · `deadlines` · `questions` · `links` · `@squad decisions` (what did we decide this week?).
- `@squad mentions` → every message that tagged you (your name, your nicknames, @everyone), split into "asked you",
  "tagged you" and "@everyone", with ⚠️ on questions you haven't answered yet. With Grok, it adds your to-do.
- 📌 **Pins**: ‼️ (Emphasize) any message to pin it, or reply 📌 right after it. `@squad pins` lists them. Wifi
  passwords and the lease link stop getting buried.
- `remind #2` (or `remind #2 tonight`, `in 3h`) after highlights sets a reminder about that message.
- 👀 **Question bumps**: a question to the group that nobody answers for 3 hours gets bumped once (daytime only,
  at most twice a day). `@squad bumps off` turns it off.
- 👋 **Welcome back**: `@squad welcome back on`. When someone comes back after 12+ hours and 25+ messages, they get a
  private DM with what mattered and how many times they were tagged. The group sees nothing.

**🔊 Voice notes**
- `@squad voice tldr` · `@squad voice highlights` · `@squad voice me` (your tags) · `@squad read that` (the last
  message, out loud) · `@squad voice <any text>`.
- Spoken in your language: set `squad language tamil` and the voice note is translated, then read with a Tamil voice
  if your Mac has one. Or ask once: `@squad voice tldr in spanish`.
- `@squad voice on` attaches a voice note to every highlights, tldr and mentions reply. Good while driving or walking
  to class, and for friends who'd rather listen than read.
- Uses the Mac's built-in voices. No key, no cost, nothing sent anywhere.

**📊 Participation**
- `@squad stats` → who's talking this week (bars and %), who starts conversations, fastest replier, most reactions,
  most questions, night owl, and a gentle "haven't heard from Sam this week, someone check in?". In a project chat it
  also shows parts done per person, which helps with peer evals.
- 🎁 `@squad wrapped` → the week in one message: totals, busiest hour, top yapper, message of the week (by
  reactions), emoji of the week, plans locked, bills split, items sold, carpools. `@squad wrapped on` posts it every
  Sunday at 8pm.
- Anyone can DM `squad hide my stats` and they're left out of stats and Wrapped.

**🌐 Translation and catching up**
- `@squad translate on` → messages in other languages (including Hinglish and Tanglish) get an English version.
- `@squad translate see you at 7 to spanish` · `@squad translate` (the last message).
- `@squad tldr` → what you missed since your last message: decisions, plans, questions, anything that mentions you.

**🚗 Rides**
- `@squad ride to the game` → "driving 3" / "ride from Barrett" → `@squad match rides` assigns seats by pickup
  distance and orders the stops. Drivers confirm with `ok`.
- `@squad airport` → groups people flying out within 2 hours of each other into shared Ubers to Sky Harbor.
  Only leave times are shared, never flight details.

**📚 School**
- Group projects: `@squad project ... due ...` → `@squad tasks` / `@squad suggest` → `dibs` → `@squad split`
  (by free hours, skills, exam weeks) → `ok` → `done <part>` → friendly check-ins → `@squad receipts` for peer evals.
- 📚 **Study buddies**: if two friends both opted in and share a class with an exam coming, it suggests a session at a
  time both are free.

**🏠 Roommates and fun**
- `@squad chores dishes, trash, bathroom` → weekly rotation, "done dishes", Sunday reset that mentions what was skipped.
- `@squad add oat milk, paper towels` → shared grocery list, "got paper towels".
- 🎂 **Secret birthdays**: a week before someone's birthday, it privately DMs everyone else to collect gift ideas and
  chip-ins, then sends a summary. The birthday person never sees it.
- 🎲 `@squad this or that` (weekly with `on`) · 😈 `@squad flake meter on` → only people who reply `track me` are
  counted; roll call at the event, a playful leaderboard after.

`@squad help` lists everything; `@squad help radar` (or voice, stats, market, money, rides, project, home, fun, lang)
explains one area.

## How the agents work together

Every student has a **personal agent** holding their private setup (sent by DM, never in the group): classes,
exams, shifts, budget, diet, free hours, skills, dorm, flights, birthday, Venmo, language. Group features can only
ask narrow questions:

| Group feature asks | The personal agent shares | It never shares |
| --- | --- | --- |
| Can you make Sat 7-9pm? | yes/no, plus a soft cost | class or exam names, where they are |
| Why not? | the reason only if `share reasons on`, otherwise "a commitment" | anything else |
| Does $10 fit? Diet okay? | yes/no | the actual budget, health details |
| Hours free? Skills? Exam week? | a number, a skill list, yes/no | what fills their week, grades |
| Shared class for study buddies? | the course, only if **both** people opted in | other classes |
| Airport ride? | a leave time | flight number or time |
| Birthday? | DMs to friends only | nothing in the group |
| Stats? | message counts in this chat, unless they opted out | anything from other chats |

**Conflicts.** Hard conflicts (class, shift, exam) rule a time out; soft ones (too early, late before an early exam,
over budget) add a cost; it picks the lowest-cost times and says what it worked around. In projects, first dibs wins
and exam weeks get later deadlines. In the market, first `want` gets the hold, and the line moves on its own when a
hold expires. In carpools, seats go to the closest riders and the rest get an Uber suggestion.

**When it asks people to confirm.** A plan needs a majority vote and then `in`; each person taps their own calendar
invite; a project split needs everyone's `ok`; a swap needs the owner's `ok`; a listing from a passing "selling ..."
message needs the seller's `list it`; only the seller can accept offers, drop prices or mark sold; bills only post
amounts and Venmo links, and each person pays themselves; drivers confirm their pickups. `@squad why` shows what was
shared for the last plan or split.

## Web app (works on any phone or laptop, deploys to Railway)

`python3 web.py` runs Squad Radar as a website: a group chat in the browser with the bot built in, and a **Board**
beside the chat that fills itself in as people talk: the next plan with its calendar invite, the live vote, what's
waiting on you, what matters today, who owes who, the move-out market, rides, the group project, chores, groceries,
pins and who's talking. Your private setup (classes, exams, budget...) lives in a private panel nobody else sees.

- Sign in with just a first name. Start a squad and send the invite link, or open the **sample squad**, where Priya,
  Jake and Sam are mid-conversation and you can type as any of them.
- Every bot reply written by the language model is labeled ("Written by grok-4-fast"), and the sidebar shows whether
  the model is connected and how many answers it has given. Replies without the label come from the bot's own rules.
- Voice notes play in the browser with the phone's or laptop's own voice.
- Works on phones (Chat / Board / You switcher) and in dark mode.

### Try it on your laptop

```bash
python3 web.py          # then open http://localhost:8080
python3 run.py llm      # one live call to the model, to prove the key works
```

### Put it online with Railway

1. Push this repo to GitHub.
2. On railway.com: **New Project → Deploy from GitHub repo →** pick `squad-radar`. It builds from the `Dockerfile`.
3. In the service's **Variables**, add `XAI_API_KEY` (your Grok key). Optional: `SQUAD_TZ` (default
   `America/Phoenix`) and `SQUAD_LLM_DAILY_LIMIT` (default 800 model calls a day, so a public link can't run up a bill).
4. **Add a volume** to the service with mount path `/data`, so chats survive redeploys.
5. **Settings → Networking → Generate Domain.** Open the link, then check `https://<your-domain>/api/health` shows
   `"enabled": true` for the model.

### Discord

The same bot can live in a Discord server. Each channel it can see becomes a squad, DMs to the bot are the private
setup, ❤️ 👍 😂 reactions count like tapbacks and ‼️ or 📌 pins a message, calendar invites arrive as files, voice
notes use Discord's text-to-speech, and model-written replies carry a small "written by ..." line.

1. discord.com/developers/applications → **New Application** → **Bot** → **Reset Token** and copy it.
2. On the same Bot page, turn on **Message Content Intent** and save.
3. **OAuth2 → URL Generator**: tick `bot`, then View Channels, Send Messages, Read Message History, Attach Files,
   Add Reactions and Send TTS Messages. Open the link it makes and add the bot to your server.
4. `pip3 install -r requirements.txt`, put the token in `.env` as `DISCORD_TOKEN=...`, and run `python3 web.py`.
   You'll see `Discord: signed in as ...`. On Railway, add `DISCORD_TOKEN` under Variables instead.
5. In any channel: `@squad help`, or mention the bot. DM the bot `classes MW 10:30-11:45 CSE 310` to set up privately.

`DISCORD_CHANNELS=general,squad` limits it to those channels. `python3 discord_bot.py` runs only the Discord bot,
with no website and no sample people.

**Demo with simulated friends.** Type `@squad simulate` in any channel. The bot creates a "Squad Radar demo" category
with five channels (#apartment-4b, #weekend-plans, #cse-310-project, #move-out-market, #intl-friends) and plays a
realistic conversation into them, posted as Priya, Jake, Sam and Maya through webhooks. The bot answers each message
as it lands: chores, a split bill, a food run, a vote that locks with a calendar invite, a group project split,
market holds and offers, and auto-translation. It tags you where you can join in. The conversations live in
`data/discord_sim.json`, so you can edit them. `@squad simulate reset` removes the demo. This needs the bot to have
**Manage Channels** and **Manage Webhooks**; if it doesn't, it replies with a link to grant them.

After those five, it builds eight more groups (dorm floor, CSE study group, intramural soccer, ISA events, hackathon
team, Flagstaff road trip, another apartment, Spanish club), each with its own friends from `data/discord_groups.json`,
and turns on **live mode**: about every 30 seconds the language model writes the next few messages in one of the
groups, in character, and the bot responds as it would to real people. `@squad live off` pauses it, `@squad live on`
resumes, `@squad live` shows the status. It stops itself after 300 messages (`SQUAD_LIVE_MAX`, `SQUAD_LIVE_EVERY`)
so it can't use up your model quota. With no model key, it uses each group's scripted lines instead.

**Talk to it by name.** "hi radar", "hey squad radar", "radar who still owes money?", "thanks radar", or replying to
one of its messages all work like `@squad`. Questions it has no command for are answered by the model using the
recent chat and what the bot is tracking (sales, plans, money, chores, the project).

With `python3 web.py` running, the demo channels also appear on the website for anyone who signs in, with the live
chat, the Board, and an **Assistant** panel: summary, what matters, your mentions, translate into 12 languages,
listen out loud, and reminders that post back into the Discord channel when they're due. Every message has
Translate and Listen buttons too.

### Using a different model

Grok is the default. Any OpenAI-compatible API works instead: set `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL`
(examples for OpenAI, Groq, OpenRouter and a local Ollama server are in `.env.example`). The model handles free-form
questions to `@squad`, tldr summaries, translation, picking highlights, your to-do from mentions, task suggestions for
projects and reading receipt photos. Planning, splitting, money and the market run on the bot's own rules, so they
work the same with or without a model.

## Run it in iMessage

You need a Mac with Messages signed in and Python 3.9 or newer. Nothing to install.

```bash
cd squad-radar
python3 run.py demo     # the scripted demo, no setup
python3 run.py sim      # chat with it in the terminal as Priya, Jake, Sam or Amrish ("+2h" moves time)
```

In a real iMessage group:

1. Give Terminal **Full Disk Access** (System Settings > Privacy & Security > Full Disk Access), then reopen Terminal.
2. `cp .env.example .env`, run `python3 run.py chats`, and put your group's name in `SQUAD_CHATS`. Add your Grok key
   as `XAI_API_KEY` (translation, tldr, receipt photos and free-form chat use it; everything else works without it).
3. `python3 run.py check`, then `python3 run.py imessage`. Click OK when macOS asks to let Terminal control Messages.
4. Each friend DMs the bot `squad i'm <name>` and then lines like: `squad classes MW 10:30-11:45 CSE 310` ·
   `squad exam nov 2 9am BIO midterm` · `squad budget 15` · `squad vegetarian` · `squad hours 6` ·
   `squad skills research, writing` · `squad dorm Barrett` · `squad venmo @priya-r` · `squad flight dec 12 2pm` ·
   `squad birthday nov 10` · `squad language tamil` · `squad nickname ammu, am` · `squad study buddy on` ·
   `squad share reasons off` · `squad hide my stats`.

**No group chat yet?** Text yourself. Put your own phone number or Apple ID email in `SQUAD_TEST_CHAT` and run
`python3 run.py imessage`. Your chat with yourself becomes a pretend group of you plus the sample friends:

- `anyone free this weekend??` is you talking
- `as Priya: 1` or `as Sam: want #1` plays a sample friend
- `dm: squad budget 10` or `as Jake dm: squad flight dec 13 9am` is a private setup DM

Private DMs (setup replies, secret birthday messages) show up in the same chat labeled `[private DM to Jake]`. They
never go to the sample phone numbers. It's real iMessage on your phone, so it also works for filming a demo.

`SQUAD_ACCOUNT=own` (default) runs on your Apple ID and starts every bot message with 🤖. For a bot with its own
contact, sign a separate Apple ID into Messages (for example in a second macOS user), add it to the group and set
`SQUAD_ACCOUNT=separate`.

## Privacy

- It only reads the group chats listed in `SQUAD_CHATS`, plus DMs that start with "squad". Other chats are skipped
  unread. It keeps the last 300 group messages (and their tapbacks) on your Mac for `tldr`, highlights, mentions,
  stats and `translate`. Tapbacks and pins never post anything in the chat.
- Voice notes are made on your Mac with its built-in voices. Only a translated voice note goes through Grok.
- Web app: people sign in with a first name only (no email, no password). Chats, setups and invites are saved on the
  server's volume (`SQUAD_DATA`). Each person's setup is only ever shown in their own private panel; the squad and the
  board only see free/busy. Sample squads are private to whoever opened them. Message and model-call limits stop a
  public link from being abused.
- Everyone's setup stays in `profiles/` on your Mac. Grok sees only messages addressed to the bot, messages being
  translated or summarized, and receipt photos sent to `@squad bill`. Never anyone's schedule.
- It never sends money, posts on anyone's behalf outside the chat, or contacts people outside the group, except the
  private birthday DMs to the birthday person's friends.

## How it's built

Python standard library only. `squad/agents.py` (personal agents), `planner.py` and `project.py` (group agents),
`market.py`, `money.py`, `rides.py`, `life.py`, `radar.py` (highlights, mentions, pins, stats, Wrapped),
`voice.py` (voice notes), `web.py` + `webapp/` (the web app and its JSON API), `discord_bridge.py` (Discord), `features.py` (wiring), `bot.py` (chat logic),
`imessage.py` (reads Messages' database read-only, sends with AppleScript, reads photo attachments, sends DMs),
`llm.py` (Grok: text, JSON, vision, automatic model pick, safe fallback).

Tests: `for t in tests/test_*.py; do python3 $t; done` (six suites, including fake Discord channels and reactions: a fake clock, a fake Messages database with
tapbacks, a fake model, a fake voice, and a real HTTP server for the web app, including privacy checks).
