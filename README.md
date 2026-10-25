# Squad Radar

**The friend in your group chat who actually knows when everyone's free.**

Squad Radar is an AI that lives in your iMessage group chat. Someone says "anyone free this weekend??" and instead
of 40 messages of "idk when are you free", it answers with two times that fit everyone's classes, something to do
(free pizza included), a vote, and a calendar invite. Switch the same chat to project mode and it splits the work
fairly, settles "dibs" fights, checks in before deadlines, and keeps receipts for peer evals.

Built for the Cubbie Hackathon, **Track 3 (Group Chat Assistant)**. Our Track 1 entry is
[Campus Radar](https://github.com/AmrishS2004/campus-radar).

## What it looks like

> **Priya:** anyone free this weekend??
>
> 🤖 ok i checked everyone's classes for this weekend 👀
> 1. Sat Oct 31 7pm-9pm: Sun Devil Movie Night (free pizza) @ Hayden Lawn (free) 🍕 free food
> 2. Sun Nov 1 5pm-7pm: boba run + walk on Palm Walk (~$7)
>
> worked around: Amrish busy Fri 6pm-7pm (robotics club) · Jake busy Sat 12pm-5pm (work shift) · Sam busy Sun 10am-1pm · nothing late Sun (Priya has an early start)
>
> reply 1 or 2 🗳️

The full scripted conversation (hangout, vote, invite, reminder, project split, dibs conflict, check-ins, receipts)
is in [demo_transcript.md](demo_transcript.md). It uses sample people and sample events.

## Two modes, one bot

**Friends mode**
- "anyone free ...?" (or `@squad plan saturday night`): finds times that fit everyone's classes, exams, shifts and preferences.
- Suggests something to do at that time: campus events first (free food gets a bonus), otherwise ideas that fit the group's budget and diets.
- Reply `1` or `2` to vote. When most people agree, reply `in`. It sends a calendar invite and a reminder 2 hours before.
- `@squad decide tacos, sushi, pizza` ends the food debate.

**Project mode**
- `@squad project CSE 310 pitch due nov 6`, then `@squad tasks research 3h, slides 2h, ...` or `@squad suggest` (Grok breaks it down).
- `dibs slides` before the split. First dibs wins, and the second person is told right away.
- `@squad split` assigns parts by free hours, skills and things people avoid, and gives anyone in an exam week a later internal deadline.
- Everyone replies `ok` to accept. `dibs <part>` after the split asks the owner to swap.
- `done slides` celebrates with a progress bar. Friendly check-ins go out the day a part is due, never during quiet hours.
- `@squad status` and `@squad receipts` (dated record of who finished what).

## How the agents work together

Every student has a **personal agent** that holds their private schedule (set up by DM, never in the group).
The **group agent** never sees that schedule. It can only ask each personal agent narrow questions:

| The group agent asks | The personal agent shares | It never shares |
| --- | --- | --- |
| Can you make Sat 7-9pm? | yes or no, plus a soft cost (for example "prefers earlier") | class names, exam names, where they are |
| Why not? | the reason, **only** if that student turned `share reasons` on; otherwise "a commitment" | anything else |
| Does $10 fit? | yes or no | their actual budget |
| Diet okay? | yes or no for the dish's tags | other health details |
| Hours free this week? Skills? | a number and a short skill list | what else fills their week |
| Exam-heavy week? | yes or no (and the exam name only if sharing is on) | grades |

**Conflicting constraints.** Hard conflicts (class, shift, exam) rule a time out. Soft ones (too early, a late
night before an early exam, over budget) add a cost. The group agent picks the lowest-cost times, keeps the two
options on different days or hours apart, and says what it worked around. If no time fits everyone, it picks the
time that misses the fewest people and names who can't make it. In projects, first dibs wins, overloaded people
get less, exam weeks get later internal deadlines, and if the parts add up to more hours than the group has, it
says so and asks.

**When it asks for confirmation.** Nothing is final until people say so:
- a hangout needs a majority vote, then a majority of `in` replies before the invite goes out,
- each person taps the invite to add it to their own calendar,
- a project split needs everyone's `ok`,
- a swap needs the current owner's `ok`.

`@squad why` shows the group exactly what each agent shared for the last plan or split.

## Run it

You need a Mac with Messages signed in, Python 3.9 or newer, and nothing else to install.

```bash
git clone https://github.com/AmrishS2004/squad-radar && cd squad-radar
cp .env.example .env            # add your Grok key and the group chat's name
python3 run.py demo             # scripted demo, no setup needed
python3 run.py sim              # chat with it in the terminal as Priya, Jake, Sam or Amrish
```

To run it in a real iMessage group chat:

1. **Full Disk Access** for Terminal: System Settings > Privacy & Security > Full Disk Access > turn on Terminal,
   then quit and reopen Terminal. This lets the bot read new messages in the chats you allow.
2. `python3 run.py chats` lists your recent chats. Put the group's name in `SQUAD_CHATS` in `.env`.
3. `python3 run.py check` confirms Messages access, profiles and your Grok key.
4. `python3 run.py imessage` starts it. The first time it replies, macOS asks to let Terminal control Messages:
   click OK (or turn it on in Privacy & Security > Automation).
5. Each friend DMs the bot `squad i'm <name>`, then their schedule, for example:
   `squad classes MW 10:30-11:45 CSE 310` · `squad exam nov 2 9am BIO midterm` · `squad budget 15` ·
   `squad vegetarian` · `squad hours 6` · `squad skills research, writing` · `squad share reasons off`.

**Which account sends the bot's messages.** With `SQUAD_ACCOUNT=own` (default) the bot runs on your Apple ID and
starts every message with 🤖. For a bot with its own contact, sign a separate Apple ID into Messages (for example in
a second macOS user account), add that address to the group, set `SQUAD_ACCOUNT=separate`, and run it there.

## Privacy

- It only reads group chats you list in `SQUAD_CHATS`, plus DMs that start with "squad". Everything else is skipped
  without being read.
- Schedules stay in `profiles/` on your Mac. Grok only ever sees messages addressed to the bot, never anyone's
  schedule, and the scheduling math runs locally.
- Without a Grok key, every feature still works with built-in replies. Grok adds free-form understanding, task
  suggestions and jokes.

## How it's built

Python standard library only. `squad/agents.py` (personal agents), `squad/planner.py` (group agent for hangouts),
`squad/project.py` (group agent for projects), `squad/bot.py` (chat logic), `squad/imessage.py` (reads Messages'
database read-only, sends with AppleScript), `squad/llm.py` (Grok, with automatic model pick and fallback).

Tests: `python3 tests/test_squad.py` (48 checks with a fake clock, a fake Messages database and a fake Grok server,
including a sweep that no private reason ever reaches the chat).
