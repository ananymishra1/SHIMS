"""Morning / daily briefing generator for SHIMS Personal AI.

Compiles reminders, weather (if online), notes, and memory into a
personalized briefing spoken by the AI.
"""
from __future__ import annotations

import time
from typing import Any

from shared.personal.notes import list_notes
from shared.personal.reminders import list_reminders


def generate_briefing(
    user_name: str = "",
    include_weather: bool = False,
    weather_data: dict[str, Any] | None = None,
) -> str:
    """Generate a natural-language daily briefing."""
    lines: list[str] = []
    name = f" {user_name}" if user_name else ""

    # Greeting
    hour = time.localtime().tm_hour
    if 5 <= hour < 12:
        greeting = f"Good morning{name}!"
    elif 12 <= hour < 17:
        greeting = f"Good afternoon{name}!"
    else:
        greeting = f"Good evening{name}!"
    lines.append(greeting)

    # Reminders
    reminders = list_reminders(upcoming_only=True, limit=10)
    if reminders:
        lines.append("Here is what you have coming up today:")
        for r in reminders[:5]:
            ts = time.strftime("%I:%M %p", time.localtime(r.remind_at))
            lines.append(f"  • {r.title} at {ts}")
    else:
        lines.append("You have no reminders for today. Enjoy your free time!")

    # Recent notes
    recent_notes = list_notes(limit=3)
    if recent_notes:
        lines.append("Recent notes:")
        for n in recent_notes:
            lines.append(f"  • {n.title}")

    # Weather (if provided)
    if include_weather and weather_data:
        temp = weather_data.get("temp", "?")
        condition = weather_data.get("condition", "unknown")
        lines.append(f"Weather: {condition}, {temp}°C.")

    lines.append("Have a great day!")
    return "\n".join(lines)
