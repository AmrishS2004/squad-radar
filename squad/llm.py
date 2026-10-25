"""Grok (xAI) client, standard library only. Every call is optional: if there's no key or the API
fails, the bot falls back to its built-in rules, so the core features never depend on the model.
The model only ever sees messages addressed to the bot, never anyone's schedule."""
import json
import os
import urllib.error
import urllib.request

API = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/")
PREFERRED = ["grok-4-fast", "grok-4", "grok-3-mini", "grok-3"]

SYSTEM = (
    "You are Squad Radar, a friendly, slightly playful assistant inside a college students' group chat. "
    "Keep replies to one or two short lines, lowercase is fine, at most one emoji. Never invent times, "
    "deadlines, grades or facts about people; if asked to schedule or split work, say to use '@squad plan' "
    "or '@squad split'."
)


class Grok:
    def __init__(self, key=None, model=None, timeout=20):
        self.key = key or os.environ.get("XAI_API_KEY", "")
        self.model = model or os.environ.get("XAI_MODEL", "")
        self.timeout = timeout
        self.last_error = None

    @property
    def enabled(self):
        return bool(self.key)

    def _req(self, method, path, body=None):
        req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json",
                                              "User-Agent": "squad-radar"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode())

    def pick_model(self):
        if self.model:
            return self.model
        try:
            ids = [m["id"] for m in self._req("GET", "/models").get("data", [])]
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            ids = []
        for want in PREFERRED:
            for i in ids:
                if i == want or i.startswith(want + "-"):
                    self.model = i
                    return i
        grok = [i for i in ids if "grok" in i and "image" not in i and "vision" not in i]
        self.model = grok[0] if grok else "grok-3-mini"
        return self.model

    def chat(self, user, system=SYSTEM, json_mode=False, max_tokens=300):
        if not self.enabled:
            return None
        body = {"model": self.pick_model(), "messages": [{"role": "system", "content": system},
                                                         {"role": "user", "content": user}],
                "temperature": 0.7, "max_tokens": max_tokens}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            data = self._req("POST", "/chat/completions", body)
            return data["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            self.last_error = "HTTP %d: %s" % (e.code, e.read().decode(errors="replace")[:200])
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
        return None

    def json(self, user, system):
        out = self.chat(user, system=system, json_mode=True, max_tokens=500)
        if not out:
            return None
        try:
            return json.loads(out[out.find("{"): out.rfind("}") + 1])
        except ValueError:
            return None


INTENT_SYSTEM = (
    "Classify a group-chat message sent to a campus assistant. Reply with JSON only: "
    '{"intent": one of ["plan","decide","project","tasks","suggest","split","dibs","done","status","receipts",'
    '"why","help","chat"], "when": short phrase like "this weekend" or "", "options": [strings] for decide, '
    '"task": string for dibs/done, "project": string, "due": string}. Use "chat" for small talk or questions.'
)

TASKS_SYSTEM = (
    "Break a student group project into 4 to 6 parts. Reply with JSON only: {\"tasks\": [{\"name\": short, "
    "\"hours\": number, \"skill\": one of research, writing, slides, video, coding, design, editing, presenting}]}. "
    "Hours are realistic for undergrads. No answers or graded content, just the parts of the work."
)

TRANSLATE_SYSTEM = (
    "Translate the text into the target language. Keep slang, names, emoji and tone. Reply with the translation only, "
    "no quotes or notes. If it's already in the target language, return it unchanged."
)

TLDR_SYSTEM = (
    "Summarize group-chat messages for a college student who missed them. At most 5 short lines starting with '- '. "
    "Cover decisions made, plans with times, questions still open, and anything that mentions or asks the reader "
    "(named in 'Reader'). Only use what's in the messages. No intro line."
)

RECEIPT_SYSTEM = (
    "Read this restaurant or store receipt. Reply with JSON only: {\"items\": [{\"name\": short, \"price\": number}], "
    "\"tax\": number or 0, \"tip\": number or 0, \"total\": number or null}. Prices in dollars. Don't invent items."
)


def _image_data_url(path):
    import base64
    import mimetypes
    import shutil
    import subprocess
    import tempfile
    p = path
    if p.lower().endswith(".heic") and shutil.which("sips"):
        out = tempfile.mktemp(suffix=".jpg")
        subprocess.run(["sips", "-s", "format", "jpeg", p, "--out", out], capture_output=True, timeout=30)
        p = out
    mime = mimetypes.guess_type(p)[0] or "image/jpeg"
    with open(p, "rb") as fh:
        return "data:%s;base64,%s" % (mime, base64.b64encode(fh.read()).decode())


def _vision_json(self, path, system):
    """Ask a Grok model that can see images to read `path`. Returns parsed JSON or None."""
    if not self.enabled:
        return None
    try:
        url = _image_data_url(path)
    except OSError as e:
        self.last_error = str(e)
        return None
    model = os.environ.get("XAI_VISION_MODEL") or self.pick_model()
    body = {"model": model, "temperature": 0.1, "max_tokens": 800, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}},
                                                      {"type": "text", "text": "Read the receipt."}]}]}
    try:
        data = self._req("POST", "/chat/completions", body)
        out = data["choices"][0]["message"]["content"]
        return json.loads(out[out.find("{"): out.rfind("}") + 1])
    except Exception as e:  # noqa: BLE001
        self.last_error = str(e)
        return None


Grok.vision_json = _vision_json
