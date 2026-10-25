"""iMessage transport for macOS: reads new messages from Messages' database (read-only) and sends
replies through the Messages app with AppleScript. Only chats on the allowlist are ever read."""
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone

DB = os.path.expanduser("~/Library/Messages/chat.db")
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

SEND_TEXT = '''on run argv
  set msgText to item 1 of argv
  set chatGuid to item 2 of argv
  tell application "Messages"
    send msgText to chat id chatGuid
  end tell
end run'''

SEND_FILE = '''on run argv
  set theFile to POSIX file (item 1 of argv)
  set chatGuid to item 2 of argv
  tell application "Messages"
    send theFile to chat id chatGuid
  end tell
end run'''


def decode_attributed_body(blob):
    """Newer macOS stores message text in an NSAttributedString blob instead of the text column."""
    if not blob:
        return None
    try:
        part = blob.split(b"NSString", 1)[1][5:]
        if part[0] == 0x81:
            n = int.from_bytes(part[1:3], "little")
            raw = part[3:3 + n]
        elif part[0] == 0x82:
            n = int.from_bytes(part[1:4], "little")
            raw = part[4:4 + n]
        else:
            n = part[0]
            raw = part[1:1 + n]
        return raw.decode("utf-8", errors="replace")
    except (IndexError, ValueError):
        return None


def apple_time(v):
    if v is None:
        return None
    secs = v / 1e9 if v > 1e12 else v
    return APPLE_EPOCH + timedelta(seconds=secs)


class IMessage:
    def __init__(self, allow_names, db=DB, attach_dir=None):
        self.db = db
        self.allow = {n.strip().lower() for n in allow_names if n.strip()}
        self.attach_dir = attach_dir or os.path.expanduser("~/Pictures/squad-radar")
        self.last = None

    def _conn(self):
        return sqlite3.connect("file:%s?mode=ro" % self.db, uri=True, timeout=5)

    def check_access(self):
        try:
            with self._conn() as c:
                c.execute("SELECT 1 FROM message LIMIT 1").fetchone()
            return True, "ok"
        except sqlite3.Error as e:
            return False, str(e)

    def list_chats(self, limit=25):
        q = """SELECT c.guid, c.display_name, c.chat_identifier, COUNT(chj.handle_id), MAX(m.date)
               FROM chat c LEFT JOIN chat_handle_join chj ON chj.chat_id = c.ROWID
               LEFT JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
               LEFT JOIN message m ON m.ROWID = cmj.message_id
               GROUP BY c.ROWID ORDER BY MAX(m.date) DESC LIMIT ?"""
        with self._conn() as c:
            return c.execute(q, (limit,)).fetchall()

    def members(self, chat_guid):
        q = """SELECT h.id FROM chat c JOIN chat_handle_join chj ON chj.chat_id = c.ROWID
               JOIN handle h ON h.ROWID = chj.handle_id WHERE c.guid = ?"""
        with self._conn() as c:
            return [r[0] for r in c.execute(q, (chat_guid,)).fetchall()] + ["me"]

    def start(self):
        with self._conn() as c:
            self.last = c.execute("SELECT IFNULL(MAX(ROWID), 0) FROM message").fetchone()[0]

    def fetch(self):
        if self.last is None:
            self.start()
        q = """SELECT m.ROWID, m.text, m.attributedBody, m.is_from_me, h.id, c.guid, c.display_name, m.date,
                      (SELECT COUNT(*) FROM chat_handle_join x WHERE x.chat_id = c.ROWID)
               FROM message m JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
               JOIN chat c ON c.ROWID = cmj.chat_id LEFT JOIN handle h ON h.ROWID = m.handle_id
               WHERE m.ROWID > ? AND IFNULL(m.associated_message_type, 0) = 0 ORDER BY m.ROWID"""
        with self._conn() as c:
            rows = c.execute(q, (self.last,)).fetchall()
        out = []
        for rid, text, body, from_me, handle, guid, name, date, n in rows:
            self.last = max(self.last, rid)
            text = text or decode_attributed_body(body)
            if not text:
                continue
            is_group = n > 1
            if is_group and (name or "").lower() not in self.allow and guid.lower() not in self.allow:
                continue  # never read chats that aren't on the allowlist
            out.append({"chat_id": guid, "chat_name": name or "", "text": text, "from_me": bool(from_me),
                        "sender": "me" if from_me else (handle or "unknown"), "is_group": is_group,
                        "ts": apple_time(date)})
        return out

    @staticmethod
    def _guids(chat_guid):
        # newer macOS stores "any;+;chat..." but AppleScript sometimes wants "iMessage;+;chat..."
        alts = [chat_guid]
        if chat_guid.startswith("any;"):
            alts.append("iMessage;" + chat_guid[4:])
        return alts

    def _osa(self, script, arg, chat_guid):
        err = ""
        for g in self._guids(chat_guid):
            r = subprocess.run(["osascript", "-e", script, arg, g], capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                return
            err = r.stderr.strip()
        raise RuntimeError(err)

    def send_text(self, chat_guid, text):
        try:
            self._osa(SEND_TEXT, text, chat_guid)
        except RuntimeError as e:
            raise RuntimeError("Messages refused to send (allow Terminal to control Messages in System Settings > "
                               "Privacy & Security > Automation): %s" % e)

    def send_file(self, chat_guid, path):
        # Messages can only attach files from places it's allowed to read; ~/Pictures works
        os.makedirs(self.attach_dir, exist_ok=True)
        dest = os.path.join(self.attach_dir, os.path.basename(path))
        shutil.copy(path, dest)
        try:
            self._osa(SEND_FILE, dest, chat_guid)
        except RuntimeError as e:
            raise RuntimeError("Messages refused the attachment: %s" % e)
