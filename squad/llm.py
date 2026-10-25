"""Language model client (Grok by default, any OpenAI-compatible API works), standard library only. Every call is optional: if there's no key or the API
fails, the bot falls back to its built-in rules, so the core features never depend on the model.
The model only ever sees messages addressed to the bot, never anyone's schedule."""
import json
import os
import re
import time
import urllib.error
import urllib.request

PREFERRED = ["grok-4-fast", "grok-4", "grok-3-mini", "grok-3"]
PROVIDERS = {"api.x.ai": "Grok (xAI)", "api.openai.com": "OpenAI", "api.groq.com": "Groq", "openrouter.ai": "OpenRouter",
             "api.together.xyz": "Together", "api.deepseek.com": "DeepSeek", "api.mistral.ai": "Mistral",
             "generativelanguage.googleapis.com": "Gemini", "localhost": "local model", "127.0.0.1": "local model"}

SYSTEM = (
    "You are Squad Radar, a friendly, slightly playful assistant inside a college students' group chat. "
    "Keep replies to one or two short lines, lowercase is fine, at most one emoji. Never invent times, "
    "deadlines, grades or facts about people; if asked to schedule or split work, say to use '@squad plan' "
    "or '@squad split'."
)


def _api_base():
    return (os.environ.get("LLM_BASE_URL") or os.environ.get("XAI_BASE_URL") or "https://api.x.ai/v1").rstrip("/")


class Grok:
    """OpenAI-compatible chat client. Grok (xAI) by default; set LLM_BASE_URL + LLM_API_KEY + LLM_MODEL to use
    another provider (OpenAI, Groq, OpenRouter, Together, DeepSeek, a local Ollama server...)."""

    def __init__(self, key=None, model=None, timeout=None, base=None, fallback=True):
        self.key = key or os.environ.get("LLM_API_KEY") or os.environ.get("XAI_API_KEY", "")
        self.model = model or os.environ.get("LLM_MODEL") or os.environ.get("XAI_MODEL", "")
        self.base = (base or _api_base()).rstrip("/")
        self.timeout = timeout or float(os.environ.get("LLM_TIMEOUT") or 45)
        # Thinking models (Gemini 3, Grok reasoning) are slow by default; "low" keeps group chat replies quick.
        self.effort = os.environ.get("LLM_REASONING_EFFORT", "low" if "googleapis.com" in self.base else "").strip().lower()
        self.json_native = os.environ.get("LLM_JSON_MODE", "off" if "googleapis.com" in self.base else "on") != "off"
        self.last_error = None
        self.recent = []    # the last few answers, so the app can label exactly which replies the model wrote
        self.calls = 0      # requests attempted
        self.ok = 0         # requests that returned an answer
        self.last_ok = None
        self.cooldown_until = 0  # set when the provider says "too many requests / quota exceeded"
        self.last_model = None   # which model wrote the latest answer (the backup, while the main one rests)
        self.fallback = None
        fb_key = os.environ.get("LLM_FALLBACK_API_KEY", "")
        fb_base = os.environ.get("LLM_FALLBACK_BASE_URL", "")
        if fallback and fb_base and (fb_key or "localhost" in fb_base):
            self.fallback = Grok(key=fb_key, model=os.environ.get("LLM_FALLBACK_MODEL", ""), base=fb_base, fallback=False)

    @property
    def configured(self):
        return bool(self.key) or "localhost" in self.base or "127.0.0.1" in self.base

    @property
    def resting(self):
        """Seconds left before we try the main model again after a quota/rate-limit answer."""
        return max(0, int(self.cooldown_until - time.time()))

    @property
    def enabled(self):
        if not self.configured:
            return False
        return not self.resting or bool(self.fallback and self.fallback.enabled)

    @property
    def provider(self):
        host = re.sub(r"^https?://", "", self.base).split("/")[0].split(":")[0]
        return PROVIDERS.get(host, host)

    def status(self):
        st = {"enabled": self.enabled, "provider": self.provider, "model": self.last_model or self.model or None, "calls": self.calls,
              "ok": self.ok, "last_error": self.last_error, "last_ok": self.last_ok, "resting": self.resting,
              "main_model": self.model or None}
        if self.fallback:
            st["fallback"] = {"provider": self.fallback.provider, "model": self.fallback.model or None, "ok": self.fallback.ok}
        return st

    def _req(self, method, path, body=None):
        headers = {"Content-Type": "application/json", "User-Agent": "squad-radar"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        req = urllib.request.Request(self.base + path, method=method, data=json.dumps(body).encode() if body else None,
                                     headers=headers)
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
        self.model = grok[0] if grok else (ids[0] if ids and "x.ai" not in self.base else "grok-3-mini")
        return self.model

    def chat(self, user, system=SYSTEM, json_mode=False, max_tokens=300):
        if not self.configured:
            return None
        out = None if self.resting else self._chat(user, system, json_mode, max_tokens)
        if out is None and self.resting and self.fallback and self.fallback.enabled:
            out = self.fallback.chat(user, system, json_mode, max_tokens)
            if out:
                self.ok += 1
                self.last_ok = time.time()
                self.last_model = self.fallback.model
                if not json_mode:
                    self.recent = (self.recent + [out])[-20:]
        return out

    def _rest(self, code, detail, retry_after=""):
        """The provider said slow down. Wait as long as it asks (or longer for a daily quota) before trying again."""
        m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', detail) or re.search(r"(?i)retry (?:in|after) (\d+(?:\.\d+)?)\s*s", detail)
        wait = float(m.group(1)) + 2 if m else (float(retry_after) + 1 if str(retry_after).strip().replace(".", "", 1).isdigit() else 60)
        if re.search(r"(?i)per ?day|daily|PerDay", detail):
            wait = max(wait, 3600)
        self.cooldown_until = time.time() + min(wait, 6 * 3600)

    def _chat(self, user, system, json_mode, max_tokens):
        if json_mode and not self.json_native:
            user += "\n\nReply with one JSON object only, no other text."
        body = {"model": self.pick_model(), "messages": [{"role": "system", "content": system},
                                                         {"role": "user", "content": user}],
                "temperature": 0.7, "max_tokens": max_tokens}
        if json_mode and self.json_native:
            body["response_format"] = {"type": "json_object"}
        if self.effort:
            body["reasoning_effort"] = self.effort
            body["max_tokens"] = max_tokens + 1500  # leave room for the model's thinking
        self.calls += 1
        for attempt in range(2):
            try:
                data = self._req("POST", "/chat/completions", body)
                out = (data["choices"][0]["message"].get("content") or "").strip()
                if out:
                    self.ok += 1
                    self.last_ok = time.time()
                    self.last_error = None
                    self.last_model = self.model
                    if not json_mode:
                        self.recent = (self.recent + [out])[-20:]
                else:
                    self.last_error = "the model returned an empty answer"
                return out or None
            except urllib.error.HTTPError as e:
                full = e.read().decode(errors="replace")[:6000]
                detail = full[:300]
                self.last_error = "HTTP %d: %s" % (e.code, detail)
                if e.code in (429, 503):
                    self._rest(e.code, full, (e.headers or {}).get("Retry-After", "") if e.headers else "")
                    self.last_error = ("HTTP %d: %s quota or rate limit reached, resting %d min" % (e.code, self.provider, max(1, self.resting // 60)))
                    return None
                # Some providers reject settings they don't know. Drop them once and retry.
                drop = [k for k in ("reasoning_effort", "response_format") if k in body and k in detail]
                if e.code == 400 and attempt == 0 and (drop or "response_format" in body or "reasoning_effort" in body):
                    for k in drop or ["reasoning_effort", "response_format"]:
                        body.pop(k, None)
                        if k == "reasoning_effort":
                            self.effort = ""
                        if k == "response_format":
                            self.json_native = False
                            body["messages"][-1]["content"] += "\n\nReply with one JSON object only, no other text."
                    continue
                return None
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)
                return None
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
    '"why","help","market","owes","tldr","highlights","mentions","stats","pins","groceries","chat"], '
    '"when": short phrase like "this weekend" or "", "options": [strings] for decide, '
    '"task": string for dibs/done, "project": string, "due": string}. '
    'market = what is for sale or anything to buy; owes = who owes money; tldr = what did I miss; highlights = what matters; '
    'mentions = who tagged me; groceries = the shopping list. Use "chat" for greetings, small talk and other questions.'
)
LIVE_SYSTEM = (
    "You write realistic Discord messages between college students at Arizona State University for a live demo of a "
    "group-chat assistant. Given the group, its members and the recent messages, write the NEXT 1 to 3 messages, each "
    "from a member listed (never from the assistant, never invent new people). Casual texting: short (under 25 words), "
    "lowercase is fine, emojis sparingly, stay in character and keep the conversation going naturally from the recent "
    "messages (answer questions, react to what the assistant said, move plans forward). Members may write in their own "
    "language mix if their description says so. About one turn in three, include something a group assistant can act "
    "on: a hangout question like 'anyone free this weekend??', selling an item with a price, a bill or who paid, a "
    "deadline or time change, asking someone by @name, voting 1 or 2 or saying 'in' when a plan vote is open, or "
    "talking to the assistant directly starting with '@squad ' (for example '@squad tldr', '@squad decide pizza, "
    "tacos', '@squad add eggs, milk', '@squad sell lamp $10 pickup by friday', '@squad what's for sale', '@squad "
    "highlights'). Don't repeat earlier messages. Reply as JSON only: "
    '{"messages": [{"who": "<member name>", "text": "<message>"}]}'
)
CHAT_SYSTEM = (
    "You are Squad Radar, a friendly assistant inside a college students' group chat. Answer the person's message using "
    "the recent chat and the squad's current state given to you. Be specific when the answer is in there (names, prices, "
    "times). If it isn't, say so in a few words and suggest the command that would help (like '@squad plan', '@squad sell', "
    "'@squad bill'). Keep it to one to three short lines, casual, lowercase is fine, at most one emoji. Never invent "
    "times, prices, deadlines or facts about people."
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

HIGHLIGHT_SYSTEM = (
    "You pick what matters in a college students' group chat for a member who was away. You get numbered messages with "
    "rule-based tags. Choose up to 5 they must not miss: plan or time changes, deadlines, money asks, decisions, direct "
    "asks for help or for the reader, important links. Skip banter and jokes. Reply only as JSON: "
    "{\"picks\": [{\"n\": <number>, \"why\": \"<max 8 casual words>\"}]} ordered by importance."
)
MENTIONS_SYSTEM = (
    "Someone was tagged in their group chat. From these messages, list what they need to do or answer, at most 3 short "
    "lowercase bullets starting with '- '. Casual texting tone, no names of the reader. If nothing is needed reply '- nothing you need to do'."
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
