import os
import requests
from src.utils.logger import get_logger

logger = get_logger("weather_fetcher")

TIMEOUT = 10
# 和风天气专属 API Host——在 config.yaml 中配置
DEFAULT_API_HOST = "jt52qd3e2a.re.qweatherapi.com"

# 无 Key 天气源：沿用本机 OpenClaw weather skill 的思路
OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
WTTR_URL = "https://wttr.in/{city}"

WEATHER_CODE_TEXT = {
    0: "晴", 1: "多云", 2: "多云", 3: "阴",
    45: "雾", 48: "雾", 51: "小雨", 53: "小雨", 55: "小雨",
    61: "雨", 63: "雨", 65: "大雨", 71: "雪", 73: "雪", 75: "大雪",
    80: "阵雨", 81: "阵雨", 82: "强阵雨", 95: "雷雨", 96: "雷雨", 99: "雷雨",
}


def fetch_weather(city: str, api_key: str = None, api_host: str = None, location: str = None) -> dict:
    key = api_key or os.getenv("QWEATHER_API_KEY", "")
    if not key:
        logger.info("QWEATHER_API_KEY 未设置，改用 Open-Meteo / wttr.in 无 Key 天气源")
        return _fetch_open_meteo(city=city, location=location) or _fetch_wttr(city) or _fallback(city)

    host = api_host or os.getenv("QWEATHER_API_HOST", DEFAULT_API_HOST)
    headers = {"X-QW-Api-Key": key}

    try:
        # 1. GEO 查询城市 ID
        geo_url = f"https://{host}/geo/v2/city/lookup"
        geo_resp = requests.get(
            geo_url,
            params={"location": location or city},
            headers=headers,
            timeout=TIMEOUT,
        )
        geo_resp.raise_for_status()
        geo_data = geo_resp.json()
        locations = geo_data.get("location", [])
        if not locations:
            logger.warning(f"未找到城市: {city}")
            return _fetch_open_meteo(city=city, location=location) or _fetch_wttr(city) or _fallback(city)
        location_id = locations[0]["id"]
        city_name = locations[0].get("name", city)

        # 2. 获取实时天气
        weather_url = f"https://{host}/v7/weather/now"
        weather_resp = requests.get(
            weather_url,
            params={"location": location_id},
            headers=headers,
            timeout=TIMEOUT,
        )
        weather_resp.raise_for_status()
        weather_data = weather_resp.json()
        now = weather_data.get("now", {})

        # 3. 获取当日预报（温度范围 + 白天风力）
        forecast_url = f"https://{host}/v7/weather/3d"
        forecast_resp = requests.get(
            forecast_url,
            params={"location": location_id},
            headers=headers,
            timeout=TIMEOUT,
        )
        forecast_resp.raise_for_status()
        forecast_data = forecast_resp.json()
        today = forecast_data.get("daily", [{}])[0]

        result = {
            "city": city_name,
            "temp": now.get("temp", "--"),
            "temp_min": today.get("tempMin", "--"),
            "temp_max": today.get("tempMax", "--"),
            "condition": now.get("text", ""),
            "condition_day": today.get("textDay", ""),
            "condition_night": today.get("textNight", ""),
            "humidity": now.get("humidity", ""),
            "wind_dir": today.get("windDirDay", now.get("windDir", "")),
            "wind_scale": today.get("windScaleDay", now.get("windScale", "")),
        }
        logger.info(
            f"天气获取成功: {city_name} {result['condition_day']} "
            f"{result['temp_min']}~{result['temp_max']}°C "
            f"{result['wind_dir']}{result['wind_scale']}级"
        )
        return result

    except requests.exceptions.Timeout:
        logger.warning("天气 API 请求超时，改用无 Key 天气源")
        return _fetch_open_meteo(city=city, location=location) or _fetch_wttr(city) or _fallback(city)
    except Exception as e:
        logger.warning(f"天气获取失败: {e}，改用无 Key 天气源")
        return _fetch_open_meteo(city=city, location=location) or _fetch_wttr(city) or _fallback(city)


def _fetch_open_meteo(city: str, location: str = None) -> dict | None:
    try:
        query = location or city
        geo_resp = requests.get(
            OPEN_METEO_GEOCODE,
            params={"name": query, "count": 1, "language": "zh", "format": "json"},
            timeout=TIMEOUT,
        )
        geo_resp.raise_for_status()
        geo_data = geo_resp.json()
        results = geo_data.get("results") or []
        if not results and city != "南京":
            return _fetch_open_meteo("南京", "Nanjing")
        if not results:
            return None
        loc = results[0]
        forecast_resp = requests.get(
            OPEN_METEO_FORECAST,
            params={
                "latitude": loc["latitude"],
                "longitude": loc["longitude"],
                "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,wind_direction_10m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                "timezone": "Asia/Shanghai",
            },
            timeout=TIMEOUT,
        )
        forecast_resp.raise_for_status()
        data = forecast_resp.json()
        current = data.get("current", {})
        daily = data.get("daily", {})
        code = current.get("weather_code")
        day_code = (daily.get("weather_code") or [code])[0]
        result = {
            "city": loc.get("name", city),
            "temp": _round(current.get("temperature_2m")),
            "temp_min": _round((daily.get("temperature_2m_min") or ["--"])[0]),
            "temp_max": _round((daily.get("temperature_2m_max") or ["--"])[0]),
            "condition": WEATHER_CODE_TEXT.get(code, ""),
            "condition_day": WEATHER_CODE_TEXT.get(day_code, WEATHER_CODE_TEXT.get(code, "")),
            "condition_night": "",
            "humidity": str(current.get("relative_humidity_2m", "")),
            "wind_dir": _wind_dir(current.get("wind_direction_10m")),
            "wind_scale": _wind_scale(current.get("wind_speed_10m")),
        }
        logger.info(f"Open-Meteo 天气获取成功: {result}")
        return result
    except Exception as e:
        logger.warning(f"Open-Meteo 天气获取失败: {e}")
        return None


def _fetch_wttr(city: str) -> dict | None:
    try:
        resp = requests.get(
            WTTR_URL.format(city=city),
            params={"format": "j1", "lang": "zh"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        current = (data.get("current_condition") or [{}])[0]
        today = (data.get("weather") or [{}])[0]
        condition = (current.get("lang_zh") or current.get("weatherDesc") or [{}])[0].get("value", "")
        result = {
            "city": city,
            "temp": current.get("temp_C", "--"),
            "temp_min": today.get("mintempC", "--"),
            "temp_max": today.get("maxtempC", "--"),
            "condition": condition,
            "condition_day": condition,
            "condition_night": "",
            "humidity": current.get("humidity", ""),
            "wind_dir": current.get("winddir16Point", ""),
            "wind_scale": current.get("windspeedKmph", ""),
        }
        logger.info(f"wttr.in 天气获取成功: {result}")
        return result
    except Exception as e:
        logger.warning(f"wttr.in 天气获取失败: {e}")
        return None


def _round(value):
    if value in (None, ""):
        return "--"
    try:
        return str(round(float(value)))
    except Exception:
        return str(value)


def _wind_dir(deg):
    if deg in (None, ""):
        return ""
    dirs = ["北风", "东北风", "东风", "东南风", "南风", "西南风", "西风", "西北风"]
    try:
        return dirs[int((float(deg) + 22.5) // 45) % 8]
    except Exception:
        return ""


def _wind_scale(kmh):
    if kmh in (None, ""):
        return ""
    try:
        speed = float(kmh)
        if speed < 1:
            return "0"
        if speed < 6:
            return "1"
        if speed < 12:
            return "2"
        if speed < 20:
            return "3"
        if speed < 29:
            return "4"
        if speed < 39:
            return "5"
        return "6+"
    except Exception:
        return ""


def _fallback(city: str) -> dict:
    return {
        "city": city,
        "temp": "--",
        "temp_min": "--",
        "temp_max": "--",
        "condition": "暂不可用",
        "condition_day": "",
        "condition_night": "",
        "humidity": "",
        "wind_dir": "",
        "wind_scale": "",
    }
