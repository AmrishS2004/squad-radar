"""Tests for the combined features: market, translation, tldr, bills, food runs, rides, airport, game day,
study buddies, heat check, flake meter, birthdays, this-or-that, chores, groceries. Fake clock, fake Grok."""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from squad.bot import SquadBot  # noqa: E402
from squad.money import split_costs  # noqa: E402
from squad.market import category_of, price_of  # noqa: E402

TZ = ZoneInfo("America/Phoenix")
fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  -> " + str(detail)[:500]))
    fails += 0 if cond else 1


class FakeGrok:
    enabled = True

    def __init__(self):
        self.calls = []

    def chat(self, user, system="", json_mode=False, max_tokens=300):
        self.calls.append((system[:20], user))
        if "Translate" in system:
            text = user.split("Text: ", 1)[1]
            lang = user.split("Target language: ", 1)[1].split("\n")[0]
            return {"english": {"¿alguien quiere ir al cine?": "anyone want to go to the movies?",
                                "நாளை வரலாமா?": "can we come tomorrow?"}.get(text, "[en] " + text)}.get(lang, "[%s] %s" % (lang, text))
        if "Summarize" in system:
            return "- plan: movie sat 7pm\n- Priya asked you about notes"
        return "ok"

    def json(self, user, system):
        if "items for sale" in system:
            return {"items": [{"title": "mini fridge", "price": 50, "condition": "obo"}]}
        if "this or that" in user:
            return {"a": "sunrise hike", "b": "sleep in"}
        return None

    def vision_json(self, path, system):
        return {"items": [{"name": "burger", "price": 14}, {"name": "pasta", "price": 16}, {"name": "fries", "price": 8}],
                "tax": 3.04, "tip": 6.84, "total": 47.88}


def profiles():
    out = {}
    for f in sorted(os.listdir(os.path.join(ROOT, "profiles"))):
        p = json.load(open(os.path.join(ROOT, "profiles", f)))
        out[p["name"]] = p
    return out


def new_bot(now, llm=None):
    clock = {"now": now}
    events = json.load(open(os.path.join(ROOT, "data", "events.json")))
    tmp = tempfile.mkdtemp()
    b = SquadBot(profiles(), events, TZ, lambda: clock["now"], state_path=os.path.join(tmp, "s.json"), llm=llm, outdir=tmp)
    b.profile_dir = os.path.join(tmp, "profiles")
    return b, clock


MEMBERS = ["me", "+14805550101", "+14805550102", "+14805550103"]
H = {"Amrish": "me", "Priya": "+14805550101", "Jake": "+14805550102", "Sam": "+14805550103"}


def send(b, who, text, chat="g", attachments=None):
    return b.on_message({"chat_id": chat, "text": text, "sender": H[who], "members": MEMBERS, "is_group": True,
                         "attachments": attachments or []})


def dm(b, who, text):
    return b.on_message({"chat_id": "dm-" + who, "text": text, "sender": H[who], "members": [H[who]], "is_group": False})


def T(outs):
    return "\n".join(o.get("text", "") for o in outs)


now = datetime(2026, 4, 28, 12, 0, tzinfo=TZ)

# ---------- helpers ----------
check("price parsing", price_of("desk $40") == 40 and price_of("lamp 10 bucks") == 10 and price_of("free couch") == 0 and price_of("nice chair") is None)
check("categories", category_of("IKEA desk") == "furniture" and category_of("mini fridge") == "appliances" and category_of("weird thing") == "other")
sh = split_costs({"Jake": 1400, "Priya": 1900}, 304, 684, 800, ["Jake", "Priya"])
check("bill split adds up to the cent", sum(sh.values()) == 1400 + 1900 + 800 + 304 + 684, sh)
check("bill split proportional", sh["Priya"] > sh["Jake"], sh)

# ---------- move-out market ----------
b, clock = new_bot(now)
out = send(b, "Jake", "@squad sell IKEA desk $40, lamp $10, mini fridge $50 obo pickup by 5/2 @ Tooker")
t = T(out)
check("multi-item listing with ids", "#1 IKEA desk · $40" in t and "#2 lamp · $10" in t and "#3 mini fridge · $50 obo" in t, t)
check("pickup date and place parsed", "pickup by Sat May 2" in t and "@ Tooker" in t, t)
check("safety tip once a day", "meet somewhere public" in t, t)
out = send(b, "Sam", "@squad sell textbook CSE 310 $25")
check("no second safety tip same day", "meet somewhere public" not in T(out), T(out))
out = send(b, "Priya", "want #1")
check("first buyer gets a 24h hold", "Priya has #1" in T(out) and "24h" in T(out), T(out))
out = send(b, "Sam", "want #1")
check("second buyer is in line", "you're #1 in line" in T(out), T(out))
out = send(b, "Sam", "sold #1")
check("only the seller can mark sold", "only Jake can do that" in T(out), T(out))
out = send(b, "Jake", "want #1")
check("seller can't buy own item", "your own listing" in T(out), T(out))
clock["now"] = now + timedelta(hours=25)
out = b.tick()
check("expired hold passes to next in line", "Priya's hold on #1 ran out" in T(out) and "Sam has #1" in T(out), T(out))
out = send(b, "Amrish", "offer #3 $35")
check("offers go to the seller", "Amrish offers $35 for #3" in T(out) and "accept #3 amrish" in T(out), T(out))
out = send(b, "Jake", "accept #3 amrish")
check("accepting an offer sets price and hold", "deal 🤝 #3 for $35" in T(out) and b.state["g"]["market"]["items"]["3"]["hold"]["by"] == "Amrish", T(out))
out = send(b, "Jake", "sold #1 to sam")
check("sold archives with count left", "SOLD ✅ #1 IKEA desk to Sam" in T(out) and "Jake has 2 left" in T(out), T(out))
out = send(b, "Priya", "want #1")
check("can't want a sold item", "already sold" in T(out), T(out))
out = send(b, "Priya", "@squad looking for mini fridge under $40")
check("looking-for finds existing matches", "#3 mini fridge" in T(out), T(out))
out = send(b, "Amrish", "@squad looking for bike under $60")
check("looking-for waits when nothing matches", "i'll ping you" in T(out), T(out))
out = send(b, "Sam", "@squad sell road bike $55 pickup by 5/1")
check("new listing pings the looking-for person", "🔔 Amrish, #5 (road bike, $55)" in T(out), T(out))
out = send(b, "Amrish", "@squad market under $30")
check("market filter by price", "#2 lamp" in T(out) and "#4 textbook" in T(out) and "road bike" not in T(out) and "IKEA desk" not in T(out), T(out))
out = send(b, "Amrish", "@squad market furniture")
check("market filter by category hides sold", "nothing matching" in T(out), T(out))
clock["now"] = datetime(2026, 5, 1, 10, 0, tzinfo=TZ)
out = b.tick()
check("price-drop nudge 2 days before pickup", "drop it to $" in T(out) and "#2 lamp" in T(out), T(out))
check("price-drop nudge only once", "#2 lamp" not in T(b.tick()))
out = send(b, "Jake", "drop #2 $7")
check("price drop announced", "📉 #2 lamp now $7 (was $10)" in T(out), T(out))
out = send(b, "Priya", "selling my air fryer, barely used, 30 bucks obo")
check("passive: spots a selling post and offers to list", "want me to list this" in T(out) and "air fryer" in T(out), T(out))
out = send(b, "Priya", "list it")
check("'list it' posts it with the seller's yes", "Priya is selling" in T(out) and "air fryer" in T(out), T(out))
out = send(b, "Sam", "pass #1")
check("pass on a sold item handled", "already sold" in T(out), T(out))

# ---------- translation + tldr (fake Grok) ----------
g = FakeGrok()
b2, clock2 = new_bot(now, llm=g)
send(b2, "Amrish", "brb class")
send(b2, "Priya", "@squad translate on")
out = send(b2, "Sam", "¿alguien quiere ir al cine?")
check("auto-translate non-English", "🌐 Sam: anyone want to go to the movies?" in T(out), T(out))
out = send(b2, "Jake", "sure lol")
check("english messages aren't translated", out == [], out)
out = send(b2, "Jake", "@squad translate see you at 7 to tamil")
check("translate to a chosen language", "🌐 (tamil) [tamil] see you at 7" in T(out), T(out))
send(b2, "Priya", "நாளை வரலாமா?")
out = send(b2, "Amrish", "@squad translate")
check("translate last message", "Priya" in T(out) and "can we come tomorrow" in T(out), T(out))
out = send(b2, "Amrish", "@squad tldr")
check("tldr summarizes since my last message", "📝 tldr" in T(out) and "Priya asked you" in T(out), T(out))
b3, _ = new_bot(now)
send(b3, "Amrish", "ok")
send(b3, "Priya", "Amrish do you have the notes?")
send(b3, "Jake", "what time tomorrow?")
out = send(b3, "Amrish", "@squad tldr")
check("tldr works without Grok (mentions + questions)", "you got mentioned" in T(out) and "questions" in T(out), T(out))
out = send(b3, "Amrish", "@squad translate hola")
check("translate without key explains", "needs a language model key" in T(out), T(out))

# ---------- bills ----------
b4, c4 = new_bot(now)
for n, v in (("Jake", "jake-k"), ("Amrish", "amrish-s")):
    b4.profiles[n].setdefault("prefs", {})["venmo"] = v
out = send(b4, "Amrish", "@squad bill tax 8% tip 18%\nJake: burger 14\nPriya: pasta 16, soda 3\nshared: fries 8")
t = T(out)
check("typed bill splits with tax/tip", "Amrish paid" in t and "Jake:" in t and "Priya:" in t, t)
rows = b4.state["g"]["owes"]
food = 1400 + 1900 + 800
check("owes add up (payer's own share excluded)", sum(r["cents"] for r in rows) == round(food * 1.26) and {r["from"] for r in rows} == {"Jake", "Priya"}, rows)
check("venmo pay links for the payer", "venmo.com/?txn=pay&recipients=amrish-s" in t, t)
out = send(b4, "Jake", "paid")
check("paid marks only your own debts", "Jake → Amrish" in T(out) and not any(r["paid"] for r in b4.state["g"]["owes"] if r["from"] == "Priya"), T(out))
c4["now"] = now + timedelta(days=2, hours=1)
out = b4.tick()
check("gentle money reminder after 2 days", "Priya → Amrish" in T(out) and "Jake → Amrish" not in T(out), T(out))
b5, _ = new_bot(now, llm=FakeGrok())
out = send(b5, "Sam", "@squad bill", attachments=["/tmp/receipt.jpg"])
check("receipt photo read into numbered items", "1. burger $14.00" in T(out) and "claim 1 3" in T(out), T(out))
send(b5, "Jake", "claim 1")
out = send(b5, "Priya", "claim 2 3")
check("bill closes when every item is claimed", "split done" in T(out) and "Jake" in T(out), T(out))
total = sum(r["cents"] for r in b5.state["g"]["owes"])
check("photo bill owes add up", total == 4788, total)

# ---------- food run ----------
b6, c6 = new_bot(now)
out = send(b6, "Priya", "anyone want boba")
check("passive food run offer", "food run boba" in T(out), T(out))
send(b6, "Priya", "@squad food run kung fu tea 10")
send(b6, "Jake", "+ taro milk tea less ice $6.5")
send(b6, "Sam", "+ mango slush $5.25")
c6["now"] = now + timedelta(minutes=11)
out = b6.tick()
check("food run closes with totals and owes", "total $11.75" in T(out) and "Jake → Priya $6.50" in T(out), T(out))

# ---------- rides, airport, game day ----------
b7, c7 = new_bot(now)
send(b7, "Amrish", "@squad ride to the game sat 5pm")
send(b7, "Jake", "driving 2 from tooker")
send(b7, "Priya", "ride from barrett")
send(b7, "Sam", "ride from vista del sol")
send(b7, "Amrish", "ride from mu")
out = send(b7, "Amrish", "@squad match rides")
t = T(out)
check("ride matching respects seats", "Jake picks up" in t and "no seat yet" in t, t)
check("closest riders get the seats", "Priya (Barrett)" in t, t)
out = send(b7, "Jake", "ok")
check("driver confirms", "confirmed" in T(out), T(out))
b7.profiles["Priya"]["flights"] = [{"at": "2026-05-08T14:00"}]
b7.profiles["Sam"]["flights"] = [{"at": "2026-05-08T15:30"}]
b7.profiles["Jake"]["flights"] = [{"at": "2026-05-09T09:00"}]
out = send(b7, "Amrish", "@squad airport")
t = T(out)
check("airport rides grouped by time, only leave times", "Priya, Sam" in t and "11:30am" in t and "Jake" in t and "14:00" not in t, t)
b7.events.append({"title": "ASU vs Arizona", "start": "2026-05-02T19:00", "where": "Mountain America Stadium", "type": "game"})
out = send(b7, "Sam", "@squad game day")
check("game day with tailgate time", "ASU vs Arizona" in T(out) and "4:30pm" in T(out), T(out))
send(b7, "Sam", "got tix")
out = send(b7, "Jake", "need tix")
check("ticket roll call", "got tix: Sam" in T(out) and "need tix: Jake" in T(out), T(out))

# ---------- study buddies (opt-in only) ----------
b8, c8 = new_bot(datetime(2026, 10, 29, 12, 0, tzinfo=TZ))
b8.profiles["Sam"]["classes"].append({"days": "TTh", "start": "15:00", "end": "16:15", "label": "BIO 181 lab"})
out = send(b8, "Amrish", "@squad study buddies")
check("no match without opt-in (no class leaked)", "BIO" not in T(out) and "study buddy on" in T(out), T(out))
dm(b8, "Priya", "squad study buddy on")
dm(b8, "Sam", "squad study buddy on")
out = send(b8, "Amrish", "@squad study buddies")
check("both opted in → match with exam and a time", "Priya + Sam are both in BIO 181" in T(out) and "exam Mon Nov 2" in T(out), T(out))

# ---------- heat check ----------
b9, c9 = new_bot(datetime(2026, 9, 18, 9, 0, tzinfo=TZ))
b9.weather_fetch = lambda when: (106, 0) if when.hour < 18 else (93, 0)
b9.events.append({"title": "Tempe Town Lake picnic", "start": "2026-09-19T15:00", "where": "Tempe Town Lake", "price": 0})
st = b9.chat_state("g")
st["poll"] = {"status": "locked", "winner": 0, "ins": ["Jake"], "outs": [], "members": ["Jake"],
              "options": [{"start": "2026-09-19T15:00:00-07:00", "end": "2026-09-19T17:00:00-07:00",
                           "activity": {"what": "Tempe Town Lake picnic", "where": "Tempe Town Lake", "price": 0}}]}
out = b9.tick()
check("heat warning for outdoor plan with a cooler time", "106°F" in T(out) and "6pm" in T(out), T(out))
check("heat warning once", "106" not in T(b9.tick()))

# ---------- flake meter ----------
b10, c10 = new_bot(datetime(2026, 10, 29, 18, 0, tzinfo=TZ))
send(b10, "Sam", "@squad flake meter on")
send(b10, "Jake", "track me")
send(b10, "Priya", "track me")
st = b10.state["g"]
st["poll"] = {"status": "locked", "winner": 0, "ins": ["Jake", "Priya"], "outs": [], "members": list(H),
              "options": [{"start": "2026-10-31T19:00:00-07:00", "end": "2026-10-31T21:00:00-07:00", "activity": {"what": "movie", "price": 0}}]}
c10["now"] = datetime(2026, 10, 31, 19, 35, tzinfo=TZ)
out = b10.tick()
check("roll call during the event", "roll call" in T(out), T(out))
send(b10, "Priya", "here")
c10["now"] = datetime(2026, 10, 31, 22, 5, tzinfo=TZ)
out = b10.tick()
check("flake update names who said in but didn't show", "Jake said \"in\"" in T(out) and "Priya" not in T(out), T(out))
out = send(b10, "Sam", "@squad flake meter")
check("leaderboard", "Jake: 1 in, 0 showed" in T(out) and "Priya: 1 in, 1 showed" in T(out), T(out))
check("people who didn't opt in aren't tracked", "Sam:" not in T(out) and "Amrish" not in T(out), T(out))

# ---------- secret birthday ----------
b11, c11 = new_bot(datetime(2026, 11, 3, 11, 0, tzinfo=TZ))
send(b11, "Amrish", "hi all")  # registers the group's members
dm(b11, "Priya", "squad birthday nov 10")
out = b11.tick()
dms = [o for o in out if o.get("dm")]
check("birthday DMs everyone except the birthday person", len(dms) == 3 and all(o["dm"] != H["Priya"] for o in dms) and "Priya's birthday" in dms[0]["text"], out)
r = dm(b11, "Jake", "squad gift karaoke night")
dm(b11, "Sam", "squad chip in 10")
check("gift ideas collected privately", "noted" in T(r), r)
c11["now"] = datetime(2026, 11, 8, 11, 0, tzinfo=TZ)
out = b11.tick()
summ = [o for o in out if o.get("dm")]
check("summary 2 days before, still not to Priya", summ and all(o["dm"] != H["Priya"] for o in summ) and "karaoke night" in summ[0]["text"] and "$10" in summ[0]["text"], summ)

# ---------- this or that ----------
b12, c12 = new_bot(datetime(2026, 11, 1, 12, 0, tzinfo=TZ))
out = send(b12, "Sam", "@squad this or that")
check("this or that posts A/B", "A)" in T(out) and "reply A or B" in T(out), T(out))
send(b12, "Jake", "a"); send(b12, "Priya", "b"); send(b12, "Amrish", "b")
c12["now"] = datetime(2026, 11, 2, 12, 5, tzinfo=TZ)
out = b12.tick()
check("results after 24h", "results" in T(out) and "2" in T(out), T(out))

# ---------- chores + groceries ----------
b13, c13 = new_bot(datetime(2026, 11, 2, 9, 0, tzinfo=TZ))
out = send(b13, "Amrish", "@squad chores dishes, trash, bathroom")
t = T(out)
check("chore rotation assigns everyone", t.count("🧽") == 3, t)
first = b13.chore_assign(b13.state["g"]["chores"], c13["now"])
nxt = b13.chore_assign(b13.state["g"]["chores"], c13["now"] + timedelta(days=7))
check("rotation shifts weekly", first != nxt and set(first) == set(nxt), (first, nxt))
who = first["dishes"]
out = send(b13, who, "done dishes")
check("done marks a chore", "dishes ✅" in T(out), T(out))
out = send(b13, "Sam", "@squad add oat milk, paper towels")
check("grocery add", "oat milk" in T(out) and "paper towels" in T(out), T(out))
out = send(b13, "Jake", "got paper towels")
check("got removes item", "✅ paper towels" in T(out) and "still need: oat milk" in T(out), T(out))
c13["now"] = datetime(2026, 11, 8, 18, 10, tzinfo=TZ)
out = b13.tick()
check("Sunday chore post lists last week's open chores", "new week" in T(out) and "last week still open" in T(out), T(out))

# ---------- project 'done' still beats chores, and help ----------
b14, _ = new_bot(datetime(2026, 11, 1, 20, 0, tzinfo=TZ))
send(b14, "Amrish", "@squad chores dishes, slides")
send(b14, "Amrish", "@squad project pitch due nov 6")
send(b14, "Amrish", "@squad tasks slides 2h, research 3h")
send(b14, "Amrish", "@squad split")
out = send(b14, "Sam", "done slides")
check("project part wins over a chore with the same name", "by Sam" in T(out) and "1/2" in T(out), T(out))
out = send(b14, "Sam", "@squad help market")
check("help topic", "want #12" in T(out), T(out))
out = send(b14, "Sam", "@squad help")
check("help overview lists every area", all(k in T(out) for k in ("move-out market", "money", "rides", "translate", "tldr", "roommates")), T(out))

print("\n%d failure(s)" % fails)
sys.exit(1 if fails else 0)
