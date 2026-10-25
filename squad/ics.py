"""Calendar invite (.ics) that any phone can add with one tap."""
import os
import uuid
from datetime import timezone


def make_ics(path, title, start, end, location="", description=""):
    def utc(dt):
        return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def esc(s):
        return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    body = "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Squad Radar//EN", "METHOD:PUBLISH", "BEGIN:VEVENT",
        "UID:%s@squad-radar" % uuid.uuid4(), "DTSTAMP:%s" % utc(start), "DTSTART:%s" % utc(start), "DTEND:%s" % utc(end),
        "SUMMARY:%s" % esc(title), "LOCATION:%s" % esc(location), "DESCRIPTION:%s" % esc(description),
        "BEGIN:VALARM", "TRIGGER:-PT1H", "ACTION:DISPLAY", "DESCRIPTION:%s" % esc(title), "END:VALARM",
        "END:VEVENT", "END:VCALENDAR", ""])
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(body)
    return path
