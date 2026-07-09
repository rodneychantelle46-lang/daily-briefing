import os
import unittest
from unittest.mock import patch

from src.fetchers.weather_fetcher import fetch_weather


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class WeatherFetcherTests(unittest.TestCase):
    def test_open_meteo_uses_configured_fallback_location(self):
        queries = []

        def fake_get(url, *, params, timeout):
            if "geocoding-api.open-meteo.com" in url:
                queries.append(params["name"])
                if params["name"] == "上海市浦东新区":
                    raise RuntimeError("temporary geocode failure")
                if params["name"] == "Shanghai":
                    return FakeResponse({
                        "results": [{
                            "name": "上海",
                            "latitude": 31.22222,
                            "longitude": 121.45806,
                        }]
                    })
            return FakeResponse({
                "current": {
                    "temperature_2m": 30.4,
                    "relative_humidity_2m": 70,
                    "weather_code": 3,
                    "wind_speed_10m": 10,
                    "wind_direction_10m": 90,
                },
                "daily": {
                    "weather_code": [3],
                    "temperature_2m_max": [32.2],
                    "temperature_2m_min": [27.6],
                },
            })

        with patch.dict(os.environ, {"QWEATHER_API_KEY": ""}), \
             patch("src.fetchers.weather_fetcher.requests.get", side_effect=fake_get):
            weather = fetch_weather(
                "浦东新区",
                location="上海市浦东新区",
                fallback_location="Shanghai",
            )

        self.assertEqual(queries, ["上海市浦东新区", "Shanghai"])
        self.assertEqual(weather["city"], "上海")
        self.assertEqual(weather["condition_day"], "阴")


if __name__ == "__main__":
    unittest.main()
