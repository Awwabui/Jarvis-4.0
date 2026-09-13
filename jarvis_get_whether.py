import os
import asyncio
import logging
import time
from dotenv import load_dotenv
from livekit.agents import function_tool

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Caches
# ──────────────────────────────────────────────
_WEATHER_CACHE: dict = {}          # city.lower() → (timestamp, result_str)
_WEATHER_TTL   = 600               # 10 minutes — weather doesn't change faster
_CITY_CACHE    = {"city": None}    # session-lifetime IP→city cache


async def _detect_city_by_ip() -> str:
    if _CITY_CACHE["city"]:
        return _CITY_CACHE["city"]
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get("https://ipapi.co/json/", timeout=aiohttp.ClientTimeout(total=6)) as resp:
                ip_info = await resp.json()
                city = ip_info.get("city")
    except Exception:
        # Fallback to requests in thread
        try:
            import requests as _req
            ip_info = await asyncio.to_thread(
                lambda: _req.get("https://ipapi.co/json/", timeout=6).json()
            )
            city = ip_info.get("city")
        except Exception:
            city = None

    city = city or os.getenv("DEFAULT_CITY") or "Karachi"
    _CITY_CACHE["city"] = city
    logger.info(f"IP سے شہر ڈیٹیکٹ کیا گیا: {city}")
    return city


@function_tool
async def get_weather(city: str = "") -> str:
    """کسی شہر کا موجودہ موسم (درجہ حرارت، feels-like، نمی، ہوا) معلوم کریں۔ خالی چھوڑیں تو default شہر۔"""
    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
        return "❌ OpenWeather API key موجود نہیں ہے۔"

    if not city:
        city = os.getenv("DEFAULT_CITY") or await _detect_city_by_ip()

    cache_key = city.lower().strip()
    cached = _WEATHER_CACHE.get(cache_key)
    if cached:
        ts, result = cached
        if time.time() - ts < _WEATHER_TTL:
            logger.info(f"موسم cache سے دیا گیا: {city}")
            return result

    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {"q": city, "appid": api_key, "units": "metric"}

    try:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params,
                                       timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status != 200:
                        return f"❌ خرابی: {city} کا موسم نہیں ملا (HTTP {resp.status})۔"
                    data = await resp.json()
        except ImportError:
            import requests as _req
            resp2 = await asyncio.to_thread(
                lambda: _req.get(url, params=params, timeout=8)
            )
            if resp2.status_code != 200:
                return f"❌ خرابی: {city} کا موسم نہیں ملا۔"
            data = resp2.json()

        weather     = data["weather"][0]["description"].title()
        temp        = data["main"]["temp"]
        feels_like  = data["main"]["feels_like"]
        humidity    = data["main"]["humidity"]
        wind_speed  = data["wind"]["speed"]
        visibility  = data.get("visibility", 0) // 1000  # km

        result = (
            f"🌤 {city} کا موسم:\n"
            f"  • صورتحال: {weather}\n"
            f"  • درجہ حرارت: {temp:.1f}°C (feels like {feels_like:.1f}°C)\n"
            f"  • نمی: {humidity}%\n"
            f"  • ہوا: {wind_speed} m/s\n"
            f"  • visibility: {visibility} km"
        )
        _WEATHER_CACHE[cache_key] = (time.time(), result)
        return result

    except Exception as e:
        logger.exception(f"موسم حاصل کرتے وقت Exception: {e}")
        return f"❌ موسم حاصل نہیں ہو سکا: {e}"
