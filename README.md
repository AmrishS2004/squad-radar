# Squad Radar

**The friend in your group chat who handles plans, money, move-out sales and everything in between.**

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

`@squad help` lists everything; `@squad help market` (or money, rides, project, home, fun, lang) explains one area.

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

**Conflicts.** Hard conflicts (class, shift, exam) rule a time out; soft ones (too early, late before an early exam,
over budget) add a cost; it picks the lowest-cost times and says what it worked around. In projects, first dibs wins
and exam weeks get later deadlines. In the market, first `want` gets the hold, and the line moves on its own when a
hold expires. In carpools, seats go to the closest riders and the rest get an Uber suggestion.

**When it asks people to confirm.** A plan needs a majority vote and then `in`; each person taps their own calendar
invite; a project split needs everyone's `ok`; a swap needs the owner's `ok`; a listing from a passing "selling ..."
message needs the seller's `list it`; only the seller can accept offers, drop prices or mark sold; bills only post
amounts and Venmo links, and each person pays themselves; drivers confirm their pickups. `@squad why` shows what was
shared for the last plan or split.

## Run it

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
   `squad birthday nov 10` · `squad language tamil` · `squad study buddy on` · `squad share reasons off`.

`SQUAD_ACCOUNT=own` (default) runs on your Apple ID and starts every bot message with 🤖. For a bot with its own
contact, sign a separate Apple ID into Messages (for example in a second macOS user), add it to the group and set
`SQUAD_ACCOUNT=separate`.

## Privacy

- It only reads the group chats listed in `SQUAD_CHATS`, plus DMs that start with "squad". Other chats are skipped
  unread. It keeps the last 300 group messages on your Mac for `tldr` and `translate`.
- Everyone's setup stays in `profiles/` on your Mac. Grok sees only messages addressed to the bot, messages being
  translated or summarized, and receipt photos sent to `@squad bill`. Never anyone's schedule.
- It never sends money, posts on anyone's behalf outside the chat, or contacts people outside the group, except the
  private birthday DMs to the birthday person's friends.

## How it's built

Python standard library only. `squad/agents.py` (personal agents), `planner.py` and `project.py` (group agents),
`market.py`, `money.py`, `rides.py`, `life.py` (features), `features.py` (wiring), `bot.py` (chat logic),
`imessage.py` (reads Messages' database read-only, sends with AppleScript, reads photo attachments, sends DMs),
`llm.py` (Grok: text, JSON, vision, automatic model pick, safe fallback).

Tests: `python3 tests/test_squad.py && python3 tests/test_features.py` (122 checks with a fake clock, a fake Messages
database and fake Grok, including privacy sweeps).
