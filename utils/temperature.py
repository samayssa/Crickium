"""Weather presets for the /play match-ready screen."""
from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WeatherPreset:
    temperature_c: int
    humidity_percent: int
    condition: str
    icon: str

    def format(self) -> str:
        return f"{self.temperature_c}°C • 💧{self.humidity_percent}% • {self.icon} {self.condition}"


WEATHER_PRESETS = [
    WeatherPreset(27, 63, "Clear", "☀️"),
    WeatherPreset(18, 74, "Cloudy", "☁️"),
    WeatherPreset(33, 49, "Sunny", "🌤️"),
    WeatherPreset(22, 81, "Light Rain", "🌧️"),
    WeatherPreset(29, 56, "Partly Cloudy", "🌥️"),
    WeatherPreset(24, 68, "Breezy", "🍃"),
    WeatherPreset(31, 54, "Hot & Dry", "🔥"),
    WeatherPreset(20, 77, "Overcast", "☁️"),
    WeatherPreset(26, 61, "Humid", "💧"),
    WeatherPreset(28, 58, "Mild", "🌤️"),
    WeatherPreset(19, 85, "Drizzle", "🌦️"),
    WeatherPreset(34, 42, "Heatwave", "🥵"),
]


def random_weather() -> WeatherPreset:
    return random.choice(WEATHER_PRESETS)
