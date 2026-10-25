"""Bill splitting (typed or from a receipt photo), who-owes-who, and food runs. Never moves money:
it posts amounts and Venmo links that each person taps themselves."""
import re
import urllib.parse
from datetime import datetime, timedelta

from .llm import RECEIPT_SYSTEM

MONEY = re.compile(r"\$?\s?(\d+(?:\.\d{1,2})?)")


def cents(x):
    return int(round(float(x) * 100))


def fmt(c):
    return "$%.2f" % (c / 100.0)


def split_costs(per_person, tax_c, tip_c, shared_c=0, sharers=None):
    """per_person: {name: cents of their own items}. Shared items split evenly among `sharers`.
    Tax and tip are spread in proportion to each person's subtotal. Returns {name: cents}, exact to the cent."""
    sharers = sharers or list(per_person)
    sub = dict(per_person)
    if shared_c and sharers:
        each, extra = divmod(shared_c, len(sharers))
        for i, n in enumerate(sorted(sharers)):
            sub[n] = sub.get(n, 0) + each + (1 if i < extra else 0)
    total_sub = sum(sub.values())
    if total_sub == 0:
        return {n: 0 for n in sub}
    add = tax_c + tip_c
    out, given = {}, 0
    names = sorted(sub)
    for n in names:
        share = sub[n] * add // total_sub
        out[n] = sub[n] + share
        given += share
    # hand out leftover cents to the biggest spenders so the total matches exactly
    for n in sorted(names, key=lambda k: -sub[k])[: add - given]:
        out[n] += 1
    return out


class MoneyMixin:
    def venmo(self, name):
        p = self.profiles.get(name, {})
        return (p.get("prefs", {}).get("venmo") or p.get("venmo") or "").lstrip("@")

    def pay_link(self, payee, amount_c, note):
        user = self.venmo(payee)
        if not user:
            return ""
        q = urllib.parse.urlencode({"txn": "pay", "recipients": user, "amount": "%.2f" % (amount_c / 100.0), "note": note})
        return "https://venmo.com/?" + q

    def add_owes(self, st, payer, shares, note):
        now = self.clock().isoformat()
        rows = []
        for n, c in shares.items():
            if n != payer and c > 0:
                rec = {"from": n, "to": payer, "cents": c, "note": note, "paid": False, "at": now, "nudged": False}
                st.setdefault("owes", []).append(rec)
                rows.append(rec)
        return rows

    def owes_lines(self, rows):
        lines = []
        for r in rows:
            link = self.pay_link(r["to"], r["cents"], r["note"])
            lines.append("- %s → %s %s%s" % (r["from"], r["to"], fmt(r["cents"]), ("\n  " + link) if link else ""))
        return lines

    # ----- typed bills: "@squad bill tax 6.40 tip 18%\nJake: burger 14\nPriya: pasta 16, soda 3\nshared: fries 8" -----
    def cmd_bill(self, msg, st, who, text):
        cid = msg["chat_id"]
        body = re.sub(r"(?i)^(bill|split (?:the )?bill|split (?:the )?check|receipt)\s*", "", text).strip()
        imgs = [a for a in msg.get("attachments", []) if re.search(r"\.(jpe?g|png|heic|webp)$", a, re.I)]
        if imgs and self.llm and self.llm.enabled:
            return self.bill_from_photo(msg, st, who, imgs[0], body)
        tax_c, tip_c = self._tax_tip(body)
        people, shared = {}, 0
        for line in re.split(r"\n|;", body):
            m = re.match(r"\s*([A-Za-z][\w ]{0,20}?)\s*:\s*(.+)$", line)
            if not m:
                continue
            name, items = m.group(1).strip(), m.group(2)
            amt = sum(cents(x) for x in MONEY.findall(items))
            if name.lower() in ("shared", "split", "everyone", "all"):
                shared += amt
            else:
                people[self._canon(name)] = people.get(self._canon(name), 0) + amt
        if not people:
            return [self.say(cid, "send it like:\n@squad bill tax 6.40 tip 18%\nJake: burger 14\nPriya: pasta 16, soda 3\nshared: fries 8\n(or attach the receipt photo)")]
        sub = sum(people.values()) + shared
        if isinstance(tip_c, float):  # percentage
            tip_c = int(round(sub * tip_c))
        if isinstance(tax_c, float):
            tax_c = int(round(sub * tax_c))
        shares = split_costs(people, tax_c, tip_c, shared, sorted(people))
        rows = self.add_owes(st, who, shares, "squad bill")
        lines = ["🧾 total %s (food %s + tax %s + tip %s), %s paid" % (fmt(sum(shares.values())), fmt(sub), fmt(tax_c), fmt(tip_c), who)]
        lines += ["- %s: %s" % (n, fmt(c)) for n, c in sorted(shares.items())]
        if rows:
            lines.append("to settle up (tap to pay):")
            lines += self.owes_lines(rows)
            lines.append("reply \"paid\" when you've sent it")
        return [self.say(cid, "\n".join(lines))]

    def _canon(self, name):
        for n in self.profiles:
            if n.lower() == name.lower():
                return n
        return name.strip().title()

    def _tax_tip(self, text):
        t = text.lower()
        tax, tip = 0, 0
        m = re.search(r"tax\s*\$?\s*(\d+(?:\.\d+)?)\s*(%)?", t)
        if m:
            tax = float(m.group(1)) / 100 if m.group(2) else cents(m.group(1))
        m = re.search(r"tip\s*\$?\s*(\d+(?:\.\d+)?)\s*(%)?", t)
        if m:
            tip = float(m.group(1)) / 100 if m.group(2) else cents(m.group(1))
        return tax, tip

    # ----- photo bills: read the receipt, everyone claims their items, then split -----
    def bill_from_photo(self, msg, st, who, path, body):
        cid = msg["chat_id"]
        data = self.llm.vision_json(path, RECEIPT_SYSTEM) if hasattr(self.llm, "vision_json") else None
        items = [i for i in (data or {}).get("items", []) if isinstance(i, dict) and i.get("name")]
        if not items:
            return [self.say(cid, "couldn't read that receipt 😅 type it instead: \"@squad bill tax 6.40 tip 18%\\nJake: burger 14 ...\"")]
        tax_c = cents((data or {}).get("tax") or 0)
        tip_c = cents((data or {}).get("tip") or 0)
        t_tax, t_tip = self._tax_tip(body)
        sub = sum(cents(i.get("price") or 0) for i in items)
        if isinstance(t_tip, float):
            tip_c = int(round(sub * t_tip))
        elif t_tip:
            tip_c = t_tip
        st["bill"] = {"payer": who, "items": [{"name": str(i["name"])[:40], "cents": cents(i.get("price") or 0), "by": []} for i in items],
                      "tax": tax_c, "tip": tip_c, "opened": self.clock().isoformat(), "claimed": []}
        lines = ["🧾 read the receipt (%s paid). everyone reply with your item numbers, like \"claim 1 3\". shared stuff: \"claim 4 shared\"" % who]
        lines += ["%d. %s %s" % (k + 1, i["name"], fmt(i["cents"])) for k, i in enumerate(st["bill"]["items"])]
        lines.append("tax %s · tip %s · \"@squad close bill\" when everyone's claimed" % (fmt(tax_c), fmt(tip_c)))
        return [self.say(cid, "\n".join(lines))]

    def ctx_bill(self, msg, st, who, low):
        b = st.get("bill")
        if not b or not re.match(r"^(claim|mine|me)\s+[\d ,]+", low):
            return None
        nums = [int(x) - 1 for x in re.findall(r"\d+", low)]
        for k in nums:
            if 0 <= k < len(b["items"]) and who not in b["items"][k]["by"]:
                b["items"][k]["by"].append(who)
        if who not in b["claimed"]:
            b["claimed"].append(who)
        unclaimed = [str(k + 1) for k, i in enumerate(b["items"]) if not i["by"]]
        if not unclaimed:
            return self.close_bill(msg, st)
        return [self.say(msg["chat_id"], "got it %s ✍️ still unclaimed: %s" % (who, ", ".join(unclaimed)))]

    def close_bill(self, msg, st):
        cid = msg["chat_id"]
        b = st.pop("bill", None)
        if not b:
            return [self.say(cid, "no open bill")]
        per = {}
        for i in b["items"]:
            owners = i["by"] or [b["payer"]]
            each, extra = divmod(i["cents"], len(owners))
            for k, n in enumerate(sorted(owners)):
                per[n] = per.get(n, 0) + each + (1 if k < extra else 0)
        shares = split_costs(per, b["tax"], b["tip"])
        rows = self.add_owes(st, b["payer"], shares, "squad bill")
        lines = ["🧾 split done:"] + ["- %s: %s" % (n, fmt(c)) for n, c in sorted(shares.items())]
        if rows:
            lines += ["to settle up (tap to pay):"] + self.owes_lines(rows) + ["reply \"paid\" when you've sent it"]
        return [self.say(cid, "\n".join(lines))]

    def cmd_owes(self, msg, st, who, text):
        rows = [r for r in st.get("owes", []) if not r["paid"]]
        if not rows:
            return [self.say(msg["chat_id"], "everyone's square ✨")]
        return [self.say(msg["chat_id"], "💸 still open:\n" + "\n".join("- %s owes %s %s (%s)" % (r["from"], r["to"], fmt(r["cents"]), r["note"]) for r in rows))]

    def ctx_paid(self, msg, st, who, low):
        m = re.fullmatch(r"(?:i\s+)?(?:paid|sent it|venmoed|sent)(?:\s+(?:you\s+)?([a-z]+))?[!. ✅💸]*", low)
        g = re.fullmatch(r"got it from ([a-z]+)[!. ]*", low)
        if not (m or g) or not st.get("owes"):
            return None
        if g:
            frm = self._canon(g.group(1))
            rows = [r for r in st["owes"] if not r["paid"] and r["to"] == who and r["from"] == frm]
        else:
            to = self._canon(m.group(1)) if m.group(1) else None
            rows = [r for r in st["owes"] if not r["paid"] and r["from"] == who and (to is None or r["to"] == to)]
        if not rows:
            return None
        for r in rows:
            r["paid"] = True
        return [self.say(msg["chat_id"], "marked paid ✅ %s" % ", ".join("%s → %s %s" % (r["from"], r["to"], fmt(r["cents"])) for r in rows))]

    # ----- food runs -----
    def cmd_foodrun(self, msg, st, who, text):
        cid = msg["chat_id"]
        t = re.sub(r"(?i)^(food run|run to|going to|order from)\s*", "", text).strip()
        m = re.search(r"(\d+)\s*(?:min|mins|minutes)?\s*$", t)
        mins = int(m.group(1)) if m and int(m.group(1)) <= 120 else 15
        place = re.sub(r"\s*\d+\s*(?:min|mins|minutes)?\s*$", "", t).strip() or "the food spot"
        closes = self.clock() + timedelta(minutes=mins)
        st["food"] = {"runner": who, "place": place, "closes": closes.isoformat(), "orders": []}
        return [self.say(cid, "🏃 %s is doing a %s run, orders close in %d min (%s)\nreply \"+ your order $price\", like \"+ taro milk tea less ice $6.5\"" % (
            who, place, mins, closes.strftime("%I:%M %p").lstrip("0").lower()))]

    def ctx_food(self, msg, st, who, low):
        f = st.get("food")
        if not f or not re.match(r"^(\+|add\s|me\s*:|order\s*:)", low):
            return None
        text = re.sub(r"^(\+|add\s|me\s*:|order\s*:)\s*", "", (msg.get("text") or "").strip(), flags=re.I)
        m = MONEY.findall(text)
        f["orders"].append({"who": who, "what": MONEY.sub("", text).strip(" -,$") or text, "cents": cents(m[-1]) if m else 0})
        return [self.say(msg["chat_id"], "added ✍️ %s: %s" % (who, f["orders"][-1]["what"]))]

    def close_food(self, cid, st):
        f = st.pop("food", None)
        if not f:
            return []
        if not f["orders"]:
            return [self.say(cid, "%s run closed, nobody ordered 🥲" % f["place"])]
        per = {}
        for o in f["orders"]:
            per[o["who"]] = per.get(o["who"], 0) + o["cents"]
        lines = ["🛍️ %s run closed. %s, here's the order:" % (f["place"], f["runner"])]
        lines += ["- %s: %s%s" % (o["who"], o["what"], (" " + fmt(o["cents"])) if o["cents"] else "") for o in f["orders"]]
        total = sum(per.values())
        if total:
            lines.append("total %s (before tax/tip)" % fmt(total))
            rows = self.add_owes(st, f["runner"], per, "%s run" % f["place"])
            if rows:
                lines += ["settle up after:"] + self.owes_lines(rows)
        return [self.say(cid, "\n".join(lines))]

    def tick_money(self, cid, st, now):
        out = []
        f = st.get("food")
        if f and datetime.fromisoformat(f["closes"]) <= now:
            out += self.close_food(cid, st)
        if 10 <= now.hour < 21:
            late = [r for r in st.get("owes", []) if not r["paid"] and not r["nudged"]
                    and now - datetime.fromisoformat(r["at"]) >= timedelta(days=2)]
            if late:
                for r in late:
                    r["nudged"] = True
                out.append(self.say(cid, "friendly money reminder 💸 " + " · ".join("%s → %s %s" % (r["from"], r["to"], fmt(r["cents"])) for r in late)
                                    + "\nreply \"paid\" once it's sent"))
        return out
