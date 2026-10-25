"""Move-out marketplace for big student group chats: numbered listings, a fair queue with 24h holds,
offers, price drops, "looking for" alerts, and nudges to drop the price before move-out day."""
import re
from datetime import datetime, timedelta

CATEGORIES = {
    "furniture": ["desk", "chair", "table", "couch", "sofa", "bed", "mattress", "shelf", "bookshelf", "dresser", "futon",
                  "nightstand", "drawer", "ottoman", "stool", "bean bag"],
    "appliances": ["fridge", "microwave", "fan", "heater", "vacuum", "air fryer", "rice cooker", "kettle", "toaster",
                   "instant pot", "blender", "iron", "air purifier", "coffee maker", "ac unit"],
    "kitchen": ["pan", "pot", "plates", "cups", "utensils", "knife", "cutlery", "bowls", "mugs", "tupperware"],
    "electronics": ["monitor", "tv", "speaker", "keyboard", "mouse", "printer", "lamp", "router", "headphones",
                    "ipad", "laptop", "cable", "charger", "hdmi"],
    "books": ["textbook", "book", "calculator", "notes"],
    "decor": ["mirror", "rug", "curtain", "plant", "poster", "lights", "frame"],
    "bikes": ["bike", "bicycle", "scooter", "skateboard", "helmet", "lock"],
    "clothes": ["jacket", "shoes", "hoodie", "dress", "coat"],
}
SELLING = re.compile(r"\b(selling|for sale|moving out sale|move[- ]out sale|giving away|free to (?:a )?good home|"
                     r"curb alert|obo|or best offer)\b", re.I)
PRICE = re.compile(r"(?:\$\s?(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\s?(?:bucks|dollars|usd)\b|(\d+(?:\.\d{1,2})?)\$)", re.I)
STRIP_SELLING = re.compile(r"\b(selling|for sale|moving out sale|move[- ]out sale|giving away|free to (?:a )?good home|curb alert)\b", re.I)
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
SAFETY = "tip: meet somewhere public on campus (like the MU) and see it before you pay"


def category_of(title):
    t = title.lower()
    for cat, words in CATEGORIES.items():
        if any(re.search(r"\b%s" % re.escape(w), t) for w in words):
            return cat
    return "other"


def price_of(text):
    t = text.lower()
    if re.search(r"\b(free|giving away|curb alert)\b", t) and not PRICE.search(t):
        return 0.0
    m = PRICE.search(text)
    return float(m.group(1) or m.group(2) or m.group(3)) if m else None


class MarketMixin:
    def market(self, st):
        return st.setdefault("market", {"next": 1, "items": {}, "iso": [], "pending": {}, "safety_day": ""})

    def _pickup(self, text):
        t = text.lower()
        m = re.search(r"(?:pick ?up|gone|leaving|move ?out|moving)\s*(?:by|before|on)?\s*(\d{1,2})/(\d{1,2})", t) or \
            re.search(r"(?:pick ?up|gone|leaving|move ?out|moving)\s*(?:by|before|on)?\s*(%s)[a-z]*\.?\s+(\d{1,2})" % "|".join(MONTHS), t)
        if not m:
            return None
        a, b = m.group(1), m.group(2)
        mo = int(a) if a.isdigit() else MONTHS[a[:3]]
        now = self.clock()
        y = now.year + (1 if mo < now.month - 1 else 0)
        return datetime(y, mo, int(b), 20, 0, tzinfo=self.tz).isoformat()

    def _where(self, text):
        m = re.search(r"(?:@|\bat\b|\bin\b|\bnear\b)\s+([A-Z][\w']*(?:\s+[A-Z][\w']*){0,3})", text)
        return m.group(1) if m else ""

    def parse_listing(self, text):
        """Split one message into items. Uses Grok when available, else simple rules."""
        if self.llm and self.llm.enabled:
            got = self.llm.json("Message: " + text, MARKET_SYSTEM) or {}
            items = []
            for it in got.get("items", []):
                if isinstance(it, dict) and it.get("title"):
                    try:
                        price = None if it.get("price") in (None, "") else float(it["price"])
                    except (TypeError, ValueError):
                        price = price_of(str(it.get("price")))
                    items.append({"title": str(it["title"])[:60], "price": price, "condition": str(it.get("condition") or "")[:30]})
            if items:
                return items
        body = STRIP_SELLING.sub("", text)
        body = re.sub(r"(?i)\b(pick ?up|gone|leaving|move ?out|moving)\s*(by|before|on)?\s*\S+(\s+\d{1,2})?", "", body)
        parts = [p for p in re.split(r"\n|;|\s\|\s", body) if p.strip()]
        if len(parts) == 1:
            pieces = [p for p in re.split(r",", parts[0]) if p.strip()]
            if sum(1 for p in pieces if price_of(p) is not None) >= 2:
                parts = pieces
        items = []
        for p in parts:
            price = price_of(p)
            title = PRICE.sub("", p)
            title = re.sub(r"(?i)\b(obo|or best offer|each|firm|free|my|a|an|the)\b", " ", title)
            title = re.sub(r"(?:@|\bat\b)\s+[A-Z][\w' ]*$", "", title)
            title = re.sub(r"[\s:,.\-!]+", " ", title).strip()
            if title and (price is not None or len(parts) == 1):
                items.append({"title": title[:60], "price": price, "condition": "obo" if re.search(r"(?i)obo|best offer", p) else ""})
        return items

    def post_items(self, cid, st, seller, items, pickup, where):
        mk = self.market(st)
        now = self.clock()
        lines = []
        new = []
        for it in items:
            iid = mk["next"]
            mk["next"] += 1
            rec = {"id": iid, "seller": seller, "title": it["title"], "price": it["price"], "condition": it.get("condition", ""),
                   "category": category_of(it["title"]), "pickup_by": pickup, "where": where, "status": "available",
                   "queue": [], "hold": None, "offers": [], "created": now.isoformat(), "drop_nudged": False}
            mk["items"][str(iid)] = rec
            new.append(rec)
            lines.append("🛒 #%d %s · %s%s" % (iid, rec["title"], self.price_str(rec["price"]), " obo" if rec["condition"] == "obo" else ""))
        meta = []
        if pickup:
            pb = datetime.fromisoformat(pickup)
            meta.append("pickup by %s" % (pb.strftime("%a %b ") + str(pb.day)))
        if where:
            meta.append("@ " + where)
        head = "%s is selling%s:" % (seller, (" (" + ", ".join(meta) + ")") if meta else "")
        msg = head + "\n" + "\n".join(lines) + "\nreply \"want #%d\" to get in line%s" % (new[0]["id"], ", \"offer #%d $X\" to offer" % new[0]["id"])
        today = now.date().isoformat()
        if mk.get("safety_day") != today:
            mk["safety_day"] = today
            msg += "\n" + SAFETY
        out = [self.say(cid, msg)]
        # tell anyone looking for this
        for iso in mk["iso"]:
            for rec in new:
                if iso["who"] != seller and self.iso_match(iso, rec):
                    out.append(self.say(cid, "🔔 %s, #%d (%s, %s) matches what you're looking for" % (
                        iso["who"], rec["id"], rec["title"], self.price_str(rec["price"]))))
        return out

    @staticmethod
    def price_str(p):
        if p is None:
            return "price? DM seller"
        return "FREE" if p == 0 else ("$%g" % p)

    def iso_match(self, iso, rec):
        words = [w for w in re.findall(r"[a-z]+", iso["what"].lower()) if len(w) > 2 and w not in ("for", "and", "any", "good", "cheap", "the")]
        title = rec["title"].lower()
        hit = any(w in title or w.rstrip("s") in title for w in words) or (iso.get("category") and iso["category"] == rec["category"] and iso["category"] != "other")
        if iso.get("max") is not None and rec["price"] is not None and rec["price"] > iso["max"]:
            return False
        return hit

    # ----- commands -----
    def cmd_sell(self, msg, st, who, text):
        cid = msg["chat_id"]
        items = self.parse_listing(text)
        if not items:
            return [self.say(cid, "list it like: \"@squad sell IKEA desk $40, lamp $10 pickup by 5/10 @ Tooker\"")]
        return self.post_items(cid, st, who, items, self._pickup(text), self._where(text))

    def cmd_market(self, msg, st, who, text):
        cid = msg["chat_id"]
        mk = self.market(st)
        t = text.lower()
        cap = None
        m = re.search(r"under\s*\$?(\d+)", t)
        if m:
            cap = float(m.group(1))
        cat = next((c for c in CATEGORIES if c in t or c.rstrip("s") in t), None)
        free = "free" in t
        mine = "mine" in t or "my listings" in t
        rows = []
        for rec in sorted(mk["items"].values(), key=lambda r: r["id"], reverse=True):
            if rec["status"] == "sold":
                continue
            if mine and rec["seller"] != who:
                continue
            if cat and rec["category"] != cat:
                continue
            if free and rec["price"] != 0:
                continue
            if cap is not None and (rec["price"] is None or rec["price"] > cap):
                continue
            words = [w for w in re.findall(r"[a-z]+", t) if w not in ("market", "under", "free", "mine", "my", "listings", "show", "me") and w not in CATEGORIES]
            if words and not cat and not any(w in rec["title"].lower() for w in words):
                continue
            tag = " (on hold)" if rec["status"] == "held" else ""
            rows.append("#%d %s · %s · %s%s" % (rec["id"], rec["title"], self.price_str(rec["price"]), rec["seller"], tag))
        if not rows:
            return [self.say(cid, "nothing matching right now. \"@squad looking for <thing>\" and i'll ping you when it shows up")]
        more = "" if len(rows) <= 12 else "\n+%d more, narrow it down (\"@squad market furniture under $30\")" % (len(rows) - 12)
        return [self.say(cid, "🛒 for sale:\n" + "\n".join(rows[:12]) + more)]

    def cmd_iso(self, msg, st, who, text):
        cid = msg["chat_id"]
        mk = self.market(st)
        t = re.sub(r"(?i)^(iso|looking for|lf|need a|need an|need)\s*", "", text).strip()
        m = re.search(r"under\s*\$?(\d+)", t, re.I)
        cap = float(m.group(1)) if m else None
        what = re.sub(r"(?i)under\s*\$?\d+", "", t).strip(" ,.")
        if not what:
            return [self.say(cid, "what are you looking for? \"@squad looking for mini fridge under $50\"")]
        iso = {"who": who, "what": what, "max": cap, "category": category_of(what), "at": self.clock().isoformat()}
        mk["iso"] = [i for i in mk["iso"] if not (i["who"] == who and i["what"] == what)] + [iso]
        hits = [r for r in mk["items"].values() if r["status"] != "sold" and r["seller"] != who and self.iso_match(iso, r)]
        if hits:
            return [self.say(cid, "already here 👀 " + " · ".join("#%d %s %s" % (r["id"], r["title"], self.price_str(r["price"])) for r in hits[:4]))]
        return [self.say(cid, "on it 🔎 i'll ping you when someone lists %s%s" % (what, (" under $%g" % cap) if cap else ""))]

    # ----- replies with #N -----
    def ctx_market(self, msg, st, who, low):
        m = re.match(r"^(want|interested(?: in)?|i'?ll take|dibs on|dibs|hold|pass(?: on)?|offer|sold|drop|unlist|remove|accept)\s*#?(\d+)\b(.*)$", low)
        if not m or "market" not in st:
            return None
        verb, iid, rest = m.group(1), m.group(2), m.group(3).strip()
        mk = st["market"]
        rec = mk["items"].get(iid)
        cid = msg["chat_id"]
        if not rec:
            return [self.say(cid, "no listing #%s" % iid)]
        is_seller = who == rec["seller"]
        if verb in ("sold", "drop", "unlist", "remove", "accept") and not is_seller:
            return [self.say(cid, "only %s can do that for #%s" % (rec["seller"], iid))]
        if rec["status"] == "sold" and verb not in ("unlist", "remove"):
            return [self.say(cid, "#%s already sold 😔 \"@squad looking for %s\" and i'll watch for another" % (iid, rec["title"]))]
        now = self.clock()
        if verb in ("want", "interested", "interested in", "i'll take", "ill take", "dibs on", "dibs", "hold"):
            if is_seller:
                return [self.say(cid, "that's your own listing 😄")]
            if who in rec["queue"]:
                return [self.say(cid, "you're #%d in line for #%s" % (rec["queue"].index(who) + 1, iid))]
            rec["queue"].append(who)
            if rec["status"] == "available":
                return [self.say(cid, self._give_hold(rec, who, now))]
            return [self.say(cid, "%s is on hold for %s. you're #%d in line, i'll ping you if it frees up" % (
                rec["title"], rec["hold"]["by"], len(rec["queue"]) - 1))]
        if verb in ("pass", "pass on"):
            if who in rec["queue"]:
                rec["queue"].remove(who)
            if rec["hold"] and rec["hold"]["by"] == who:
                return [self.say(cid, self._next_in_line(rec, now, "%s passed." % who))]
            return [self.say(cid, "ok, you're out of line for #%s" % iid)]
        if verb == "offer":
            p = price_of(rest)
            if p is None:
                return [self.say(cid, "how much? \"offer #%s $30\"" % iid)]
            rec["offers"].append({"by": who, "amount": p, "at": now.isoformat()})
            return [self.say(cid, "%s offers $%g for #%s %s. %s, reply \"accept #%s %s\" to take it" % (
                who, p, iid, rec["title"], rec["seller"], iid, who.lower()))]
        if verb == "accept":
            name = rest.strip().lower()
            offer = next((o for o in reversed(rec["offers"]) if not name or o["by"].lower() == name), None)
            if not offer:
                return [self.say(cid, "no offer from %s on #%s" % (name or "anyone", iid))]
            rec["price"] = offer["amount"]
            if offer["by"] in rec["queue"]:
                rec["queue"].remove(offer["by"])
            rec["queue"].insert(0, offer["by"])
            return [self.say(cid, "deal 🤝 #%s for $%g. " % (iid, offer["amount"]) + self._give_hold(rec, offer["by"], now))]
        if verb == "sold":
            rec["status"] = "sold"
            self.log_event(st, "sold", rec.get("cents", 0))
            rec["sold_to"] = re.sub(r"^to\s+", "", rest.strip()).title() or (rec["hold"]["by"] if rec["hold"] else "")
            rec["sold_at"] = now.isoformat()
            rec["hold"] = None
            left = sum(1 for r in mk["items"].values() if r["seller"] == who and r["status"] != "sold")
            return [self.say(cid, "SOLD ✅ #%s %s%s%s" % (iid, rec["title"], (" to " + rec["sold_to"]) if rec["sold_to"] else "",
                                                       (" · %s has %d left" % (who, left)) if left else " · all your stuff is gone 🎉"))]
        if verb == "drop":
            p = price_of(rest)
            if p is None:
                return [self.say(cid, "new price? \"drop #%s $25\"" % iid)]
            old = rec["price"]
            rec["price"] = p
            ping = [i["who"] for i in mk["iso"] if self.iso_match(i, rec)] + rec["queue"]
            ping = sorted(set(ping) - {who})
            return [self.say(cid, "📉 #%s %s now %s (was %s)%s" % (iid, rec["title"], self.price_str(p), self.price_str(old),
                                                               (" · " + ", ".join(ping) + " 👀") if ping else ""))]
        if verb in ("unlist", "remove"):
            mk["items"].pop(iid, None)
            return [self.say(cid, "took #%s down" % iid)]
        return None

    def _give_hold(self, rec, who, now):
        rec["status"] = "held"
        rec["hold"] = {"by": who, "until": (now + timedelta(hours=24)).isoformat()}
        return "%s has #%d (%s, %s) on hold for 24h 🤝 %s & %s, DM each other to set up pickup. %s: reply \"sold #%d\" when it's done" % (
            who, rec["id"], rec["title"], self.price_str(rec["price"]), rec["seller"], who, rec["seller"], rec["id"])

    def _next_in_line(self, rec, now, why):
        if rec["hold"] and rec["hold"]["by"] in rec["queue"]:
            rec["queue"].remove(rec["hold"]["by"])
        rec["hold"] = None
        if rec["queue"]:
            return why + " " + self._give_hold(rec, rec["queue"][0], now)
        rec["status"] = "available"
        return why + " #%d %s is available again 🛒" % (rec["id"], rec["title"])

    def tick_market(self, cid, st, now):
        out = []
        mk = st.get("market")
        if not mk:
            return out
        for rec in mk["items"].values():
            if rec["status"] == "held" and rec["hold"] and datetime.fromisoformat(rec["hold"]["until"]) <= now:
                out.append(self.say(cid, self._next_in_line(rec, now, "⏰ %s's hold on #%d ran out." % (rec["hold"]["by"], rec["id"]))))
            if (rec["status"] == "available" and rec["pickup_by"] and not rec["drop_nudged"] and rec["price"]
                    and datetime.fromisoformat(rec["pickup_by"]) - now <= timedelta(days=2) and 9 <= now.hour < 21):
                rec["drop_nudged"] = True
                new = max(0, round(rec["price"] * 0.7))
                out.append(self.say(cid, "%s, #%d %s is still up and pickup is %s. drop it to $%d? reply \"drop #%d $%d\" (or \"drop #%d $0\" to give it away)" % (
                    rec["seller"], rec["id"], rec["title"], datetime.fromisoformat(rec["pickup_by"]).strftime("%a"), new, rec["id"], new, rec["id"])))
        return out

    # ----- passive: spot "selling ..." posts and offer to list them -----
    def passive_market(self, msg, st, who, text):
        if not SELLING.search(text) or price_of(text) is None:
            return None
        items = self.parse_listing(text)
        if not items:
            return None
        self.market(st)["pending"][who] = {"text": text, "items": items, "at": self.clock().isoformat()}
        preview = ", ".join("%s %s" % (i["title"], self.price_str(i["price"])) for i in items[:4])
        return [self.say(msg["chat_id"], "want me to list this so it doesn't get buried? %s → reply \"list it\"" % preview)]

    def ctx_listit(self, msg, st, who, low):
        if not re.fullmatch(r"(list it|yes list it|yes|yep|list)[!.]*", low):
            return None
        pend = st.get("market", {}).get("pending", {}).pop(who, None)
        if not pend:
            return None
        return self.post_items(msg["chat_id"], st, who, pend["items"], self._pickup(pend["text"]), self._where(pend["text"]))


MARKET_SYSTEM = (
    "Extract items for sale from a student's group-chat message. Reply with JSON only: "
    '{"items": [{"title": short item name, "price": number or null (0 if free), "condition": short or ""}]}. '
    "Don't invent prices. Leave out pickup times and places."
)
