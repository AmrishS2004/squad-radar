"""Text to voice notes. Uses the Mac's built-in voices (say + afconvert), so there is no key and no cost.
Falls back to espeak-ng on Linux. Returns the path of an audio file Messages can send, or None."""
import os
import re
import shutil
import subprocess
import sys
import tempfile

LOCALES = {
    "english": "en_", "spanish": "es_", "hindi": "hi_IN", "tamil": "ta_IN", "telugu": "te_IN", "french": "fr_",
    "german": "de_", "mandarin": "zh_CN", "chinese": "zh_", "japanese": "ja_JP", "korean": "ko_KR", "portuguese": "pt_",
    "arabic": "ar_", "italian": "it_IT", "russian": "ru_RU", "vietnamese": "vi_VN", "turkish": "tr_TR",
    "indonesian": "id_ID", "thai": "th_TH", "kannada": "kn_IN", "malayalam": "ml_IN", "marathi": "mr_IN",
    "bengali": "bn_IN", "gujarati": "gu_IN", "punjabi": "pa_IN", "urdu": "ur_", "farsi": "fa_", "persian": "fa_",
}
ESPEAK = {"english": "en-us", "spanish": "es", "hindi": "hi", "tamil": "ta", "telugu": "te", "french": "fr", "german": "de",
          "mandarin": "cmn", "chinese": "cmn", "japanese": "ja", "korean": "ko", "portuguese": "pt", "arabic": "ar",
          "italian": "it", "russian": "ru", "vietnamese": "vi", "kannada": "kn", "malayalam": "ml", "marathi": "mr",
          "bengali": "bn", "gujarati": "gu", "punjabi": "pa", "urdu": "ur"}
NICE_EN = ["Ava (Premium)", "Zoe (Premium)", "Evan (Enhanced)", "Ava", "Samantha", "Daniel", "Alex"]
_voices = None

EMOJI = re.compile("(?:[\U0001F000-\U0001FAFF\u2300-\u23FF\u2600-\u27BF\u2190-\u21FF\u2B00-\u2BFF\u2500-\u259F\uFE0F\u200D\u203C\u2049\u20E3])+\\d*")


def speakable(text):
    """Turn a chat message into something that sounds right out loud."""
    t = re.sub(r"https?://\S+", "a link", text)
    t = re.sub(r"\s*\([^)]*\bmsgs?\b[^)]*\)", "", t)                 # "(last 24h · 87 msgs → 5 that matter)"
    t = t.replace("→", " then ").replace("←", ", ")
    t = EMOJI.sub("", t)
    t = re.sub(r"^\s*(?:\d+\.|[-•])\s*", "", t, flags=re.M)            # list numbers and bullets
    t = re.sub(r"([A-Z][\w-]*) · ([^\"\n]+?): \"", r"\1, \2, said: ", t)  # Priya · 2:14pm: "..."
    t = re.sub(r"#(\d+)", r"number \1", t)
    t = t.replace("·", ",").replace("&", "and").replace("@squad", "squad")
    t = re.sub(r"@(\w)", r"\1", t)
    t = re.sub(r"\btldr\b", "T L D R", t, flags=re.I)
    t = re.sub(r"(\d{1,2})(?::(\d{2}))?(am|pm)\b",
               lambda m: m.group(1) + (" " + m.group(2) if m.group(2) and m.group(2) != "00" else "") + " " + m.group(3)[0].upper() + " M", t)
    t = t.replace('"', "").replace("“", "").replace("”", "")
    t = re.sub(r":\s*\n", ". ", t)
    t = re.sub(r"\s*\n+\s*", ". ", t)
    t = re.sub(r"([?!])\.", r"\1", t)
    t = re.sub(r"\s+([,.])", r"\1", t)
    t = re.sub(r"([,.]){2,}", ".", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = t.strip(" .,")
    return t if t.endswith(("?", "!")) else t + "."


def mac_voices():
    global _voices
    if _voices is None:
        _voices = []
        try:
            out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=15).stdout
            for line in out.splitlines():
                m = re.match(r"^(.+?)\s+([a-z]{2,3}_[A-Z0-9]{2,3})\s+#", line)
                if m:
                    _voices.append((m.group(1).strip(), m.group(2)))
        except Exception:  # noqa: BLE001
            pass
    return _voices


def pick_voice(lang, voices=None):
    voices = mac_voices() if voices is None else voices
    loc = LOCALES.get((lang or "english").lower(), "en_")
    if loc == "en_":
        names = [n for n, l in voices if l.startswith("en_US")]
        for want in NICE_EN:
            if want in names:
                return want
        return names[0] if names else None
    hits = [n for n, l in voices if l.startswith(loc)]
    return hits[0] if hits else None


def speak(text, path, lang="english", rate=185):
    """Write a voice note for `text` to `path` (.m4a on a Mac). Returns the real path, or None."""
    text = speakable(text)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(text)
        src = fh.name
    try:
        if sys.platform == "darwin" and shutil.which("say"):
            aiff = os.path.splitext(path)[0] + ".aiff"
            cmd = ["say", "-r", str(rate), "-o", aiff, "-f", src]
            v = pick_voice(lang)
            if v:
                cmd[1:1] = ["-v", v]
            subprocess.run(cmd, check=True, timeout=120)
            out = os.path.splitext(path)[0] + ".m4a"
            if shutil.which("afconvert"):
                subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", aiff, out], check=True, timeout=120)
                os.remove(aiff)
                return out
            return aiff
        engine = shutil.which("espeak-ng") or shutil.which("espeak")
        if engine:
            wav = os.path.splitext(path)[0] + ".wav"
            subprocess.run([engine, "-v", ESPEAK.get((lang or "english").lower(), "en-us"), "-s", "170", "-w", wav, "-f", src],
                           check=True, timeout=120, capture_output=True)
            if shutil.which("ffmpeg"):
                out = os.path.splitext(path)[0] + ".m4a"
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-c:a", "aac", out], check=True, timeout=120)
                os.remove(wav)
                return out
            return wav
    except Exception:  # noqa: BLE001
        return None
    finally:
        os.remove(src)
    return None
