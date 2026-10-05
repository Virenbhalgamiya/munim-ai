"""Natural-sounding speech: Microsoft neural voices via edge-tts (free, no API key).

Hindi: hi-IN-SwaraNeural (female) / hi-IN-MadhurNeural (male); Marathi: mr-IN-AarohiNeural /
mr-IN-ManoharNeural; English (India): en-IN-NeerjaNeural / en-IN-PrabhatNeural.
edge-tts uses the same online service as Microsoft Edge's "Read aloud". It is unofficial, so the web
page falls back to the browser's own voice whenever this fails.
"""
from __future__ import annotations

import asyncio
import re
from collections import OrderedDict

from app import config

VOICES = {
    "hi": {"female": "hi-IN-SwaraNeural", "male": "hi-IN-MadhurNeural"},
    "mr": {"female": "mr-IN-AarohiNeural", "male": "mr-IN-ManoharNeural"},
    "en": {"female": "en-IN-NeerjaNeural", "male": "en-IN-PrabhatNeural"},
}
_cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
_CACHE_MAX = 64


class TTSError(RuntimeError):
    pass


def speech_text(text: str, lang: str = "hi") -> str:
    """Make on-screen text sound right when spoken: ₹ amounts, %, symbols."""
    rupees = "rupees" if lang == "en" else "रुपये"
    percent = "percent" if lang == "en" else "प्रतिशत"
    t = re.sub(r"₹\s?([\d,]+(?:\.\d+)?)", rf"\1 {rupees}", text)
    t = re.sub(r"(\d)\s?%", rf"\1 {percent}", t)
    t = t.replace("/हफ़्ता", " प्रति हफ़्ता").replace("/week", " per week")
    t = re.sub(r"[—–]", ", ", t)
    t = re.sub(r"[+−]\s?(?=\d)", "", t)
    t = re.sub(r"[*_#`~|<>]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def speech_lang(text: str, lang: str) -> str:
    """Language whose voice can actually read `text`: Devanagari text never goes to the English voice."""
    letters = [ch for ch in text or "" if ch.isalpha()]
    dev = sum("\u0900" <= ch <= "\u097f" for ch in letters)
    if lang == "en" and letters and dev > len(letters) * 0.3:
        return "hi"
    if lang in ("hi", "mr") and letters and dev == 0:
        return "en"                       # an all-English sentence reads best with the Indian-English voice
    return lang if lang in VOICES else "hi"


def voice_for(lang: str, gender: str) -> str:
    return VOICES.get(lang, VOICES["hi"]).get(gender, VOICES["hi"]["female"])


async def synthesize(text: str, lang: str = "hi", gender: str = "female") -> bytes:
    """MP3 bytes for `text`. Cached, so the daily briefing is only generated once."""
    import edge_tts

    lang = speech_lang(text, lang)
    voice = voice_for(lang, gender)
    spoken = speech_text(text, lang)[:1500]
    if not spoken:
        raise TTSError("empty text")
    key = (voice, spoken)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]

    async def _run() -> bytes:
        audio = bytearray()
        async for chunk in edge_tts.Communicate(spoken, voice, rate=config.TTS_RATE).stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
        return bytes(audio)

    try:
        data = await asyncio.wait_for(_run(), timeout=config.TTS_TIMEOUT_SECONDS)
    except Exception as e:  # network down, service change, timeout
        raise TTSError(f"{type(e).__name__}: {e}"[:200]) from None
    if not data:
        raise TTSError("no audio returned")
    _cache[key] = data
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return data
