"""Build a Beijing-local date and Chongqing weather sentence for scheduled messages."""

from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen


WEEKDAYS = "一二三四五六日"
WEATHER = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "有雾", 48: "有雾凇", 51: "小毛毛雨", 53: "毛毛雨", 55: "较强毛毛雨",
    56: "冻毛毛雨", 57: "较强冻毛毛雨", 61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "较强冻雨", 71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "较强阵雨", 82: "强阵雨", 85: "阵雪", 86: "较强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷阵雨伴冰雹",
}


def get_json(base: str, params: dict[str, object]) -> dict:
    request = Request(base + "?" + urlencode(params), headers={"User-Agent": "douyin-spark/1.0"})
    with urlopen(request, timeout=8) as response:
        return json.load(response)


def weather_sentence(city: str, day: str) -> str:
    locations = get_json("https://geocoding-api.open-meteo.com/v1/search", {
        "name": city, "count": 5, "language": "zh", "countryCode": "CN",
    }).get("results", [])
    location = next((item for item in locations if
                     str(item.get("name", "")).replace("市", "") == city.replace("市", "")), None)
    if location is None:
        raise ValueError(f"无法唯一找到城市 {city}")
    forecast = get_json("https://api.open-meteo.com/v1/forecast", {
        "latitude": location["latitude"], "longitude": location["longitude"],
        "daily": "weather_code,temperature_2m_max,temperature_2m_min",
        "timezone": "Asia/Shanghai", "forecast_days": 1,
    })["daily"]
    if forecast["time"][0] != day:
        raise ValueError("天气预报日期与北京时间不一致")
    code = forecast["weather_code"][0]
    condition = WEATHER.get(code)
    if condition is None:
        raise ValueError(f"未知天气代码 {code}")
    low = round(forecast["temperature_2m_min"][0])
    high = round(forecast["temperature_2m_max"][0])
    return f"{city}今天预计{condition}，气温{low}～{high}℃（Open-Meteo 预报）。"


def build_daily_note(city: str, now: datetime) -> tuple[str, str | None]:
    day = now.strftime("%Y-%m-%d")
    date_text = f"今天是{now.month}月{now.day}日，星期{WEEKDAYS[now.weekday()]}。"
    try:
        weather = weather_sentence(city, day)
        warning = None
    except Exception as exc:
        weather = ""
        warning = f"天气获取失败：{type(exc).__name__}: {exc}"
    return date_text + weather, warning


def append_daily_note(message: str, note: str) -> str:
    return f"{message.rstrip()} {note}"
