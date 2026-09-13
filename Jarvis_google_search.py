import os
import asyncio
import logging
import time
from dotenv import load_dotenv
from livekit.agents import function_tool
from datetime import datetime

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Simple TTL cache for repeated identical queries
# ──────────────────────────────────────────────
_SEARCH_CACHE: dict = {}   # query.lower() → (timestamp, result)
_CACHE_TTL = 60            # seconds


def _cached(query: str):
    entry = _SEARCH_CACHE.get(query.lower().strip())
    if entry:
        ts, result = entry
        if time.time() - ts < _CACHE_TTL:
            return result
    return None


def _store_cache(query: str, result: str):
    _SEARCH_CACHE[query.lower().strip()] = (time.time(), result)
    # Keep cache small
    if len(_SEARCH_CACHE) > 50:
        oldest = min(_SEARCH_CACHE, key=lambda k: _SEARCH_CACHE[k][0])
        del _SEARCH_CACHE[oldest]


async def _duckduckgo_fallback(query: str) -> str:
    """Backup search when Google API is unavailable (quota, key, network)."""
    def _search():
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=3))

    try:
        results = await asyncio.to_thread(_search)
    except Exception as e:
        logger.error(f"DuckDuckGo fallback بھی ناکام: {e}")
        return f"❌ تلاش مکمل طور پر ناکام (API + fallback): {e}"

    if not results:
        return "کوئی نتائج نہیں ملے۔"

    formatted = ""
    for i, item in enumerate(results, start=1):
        title   = item.get("title", "")
        link    = item.get("href") or item.get("link", "")
        snippet = item.get("body") or item.get("snippet", "")
        formatted += f"{i}. {title}\n{link}\n{snippet}\n\n"
    return formatted.strip()


@function_tool
async def google_search(query: str) -> str:
    """ویب پر فوری تلاش (تیز API — براؤزر نہیں کھولتا)۔"""
    logger.info(f"سرچ: {query}")

    # Return cached result if fresh
    cached = _cached(query)
    if cached:
        logger.info("cache سے نتیجہ دیا گیا")
        return cached

    api_key          = os.getenv("GOOGLE_SEARCH_API_KEY")
    search_engine_id = os.getenv("SEARCH_ENGINE_ID")

    if not api_key or not search_engine_id:
        logger.warning("API key نہیں — DuckDuckGo استعمال ہو رہا ہے")
        result = await _duckduckgo_fallback(query)
        _store_cache(query, result)
        return result

    url    = "https://www.googleapis.com/customsearch/v1"
    params = {"key": api_key, "cx": search_engine_id, "q": query, "num": 3}

    try:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params,
                                       timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 429:
                        logger.warning("Google quota ختم — DuckDuckGo")
                        result = await _duckduckgo_fallback(query)
                        _store_cache(query, result)
                        return result
                    if resp.status != 200:
                        logger.error(f"Google API خرابی: {resp.status} — fallback")
                        result = await _duckduckgo_fallback(query)
                        _store_cache(query, result)
                        return result
                    data = await resp.json()
        except ImportError:
            import requests as _req
            response = await asyncio.to_thread(
                lambda: _req.get(url, params=params, timeout=8)
            )
            if response.status_code in (429, 403):
                result = await _duckduckgo_fallback(query)
                _store_cache(query, result)
                return result
            if response.status_code != 200:
                result = await _duckduckgo_fallback(query)
                _store_cache(query, result)
                return result
            data = response.json()

    except Exception as e:
        logger.error(f"Google API نیٹ ورک خرابی: {e} — fallback")
        result = await _duckduckgo_fallback(query)
        _store_cache(query, result)
        return result

    items = data.get("items", [])
    if not items:
        return "کوئی نتائج نہیں ملے۔"

    formatted = ""
    for i, item in enumerate(items, start=1):
        title   = item.get("title", "")
        link    = item.get("link", "")
        snippet = item.get("snippet", "")
        formatted += f"{i}. {title}\n{link}\n{snippet}\n\n"

    result = formatted.strip()
    _store_cache(query, result)
    return result


@function_tool
async def get_current_datetime() -> str:
    """موجودہ تاریخ، وقت اور دن کا حصہ (صبح/دوپہر/شام/رات) حاصل کریں۔"""
    tz_name = os.getenv("USER_TIMEZONE", "Asia/Karachi")
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = None

    now  = datetime.now(tz)
    hour = now.hour
    if   5  <= hour < 12: part = "Subha (Morning / صبح)"
    elif 12 <= hour < 17: part = "Dopaher (Afternoon / دوپہر)"
    elif 17 <= hour < 21: part = "Shaam (Evening / شام)"
    else:                  part = "Raat (Night / رات)"

    return (
        f"Waqt: {now.strftime('%Y-%m-%d %I:%M %p')} ({tz_name})\n"
        f"Waqt ka hissa: {part}"
    )
