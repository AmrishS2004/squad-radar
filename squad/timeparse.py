"""Turn casual phrases ("this weekend", "tomorrow night", "for 3 hours") into concrete time windows."""
import re
from datetime import datetime, time, timedelta

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
SHORT = {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3, "fri": 4, "sat": 5, "sun": 6}


def _at(d, hh, mm=0):
    return datetime.combine(d, time(hh, mm), tzinfo=None)


def window(text, now):
    """Return (start, end, label). `now` is timezone-aware; results share its tzinfo."""
    tz = now.tzinfo
    t = (text or "").lower()
    today = now.date()

    def z(dt):
        return dt.replace(tzinfo=tz)

    if "tonight" in t:
        s = max(now, z(_at(today, 17)))
        return s, z(_at(today, 23, 59)), "tonight"
    if "tomorrow" in t:
        d = today + timedelta(days=1)
        if "night" in t or "evening" in t:
            return z(_at(d, 17)), z(_at(d, 23, 59)), "tomorrow night"
        return z(_at(d, 9)), z(_at(d, 23, 59)), "tomorrow"
    if "next week" in t:
        mon = today + timedelta(days=(7 - today.weekday()))
        return z(_at(mon, 9)), z(_at(mon + timedelta(days=6), 23, 59)), "next week"
    if "weekend" in t:
        wd = today.weekday()
        if wd >= 5 or (wd == 4 and now.hour >= 17):
            sun = today + timedelta(days=6 - wd)
            return now, z(_at(sun, 23)), "this weekend"
        fri = today + timedelta(days=4 - wd)
        return z(_at(fri, 17)), z(_at(fri + timedelta(days=2), 23)), "this weekend"
    for name, idx in list(zip(DAYS, range(7))) + list(SHORT.items()):
        if re.search(r"\b%s\b" % name, t):
            ahead = (idx - today.weekday()) % 7
            d = today + timedelta(days=ahead)
            s = max(now, z(_at(d, 9))) if ahead == 0 else z(_at(d, 9))
            label = DAYS[idx].capitalize()
            if "night" in t or "evening" in t:
                s = max(s, z(_at(d, 17)))
                label += " night"
            return s, z(_at(d, 23, 59)), label
    return now, now + timedelta(days=3), "the next few days"


def duration_minutes(text, default=120):
    t = (text or "").lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b", t)
    if m:
        return max(30, min(8 * 60, int(float(m.group(1)) * 60)))
    m = re.search(r"(\d+)\s*(?:m|min|mins|minutes)\b", t)
    if m:
        return max(30, min(8 * 60, int(m.group(1))))
    for word, mins in (("movie", 180), ("game night", 180), ("dinner", 90), ("lunch", 60), ("coffee", 45),
                       ("boba", 45), ("study", 120), ("hike", 180), ("gym", 75)):
        if word in t:
            return mins
    return default


def parse_clock(s):
    """'10:30' / '1:30pm' / '9am' / '13:00' -> (h, m)."""
    s = s.strip().lower().replace(".", "")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
    if not m:
        raise ValueError("can't read time '%s'" % s)
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "pm" and h != 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise ValueError("bad time '%s'" % s)
    return h, mi


def fmt_slot(start, end):
    def clock(dt):
        h = dt.strftime("%I").lstrip("0")
        return h + (dt.strftime(":%M") if dt.minute else "") + dt.strftime("%p").lower()
    return "%s %s-%s" % (start.strftime("%a %b ") + str(start.day), clock(start), clock(end))
