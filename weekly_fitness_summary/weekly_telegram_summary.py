import asyncio
import os
from datetime import date, datetime, timedelta

import telegram
from dotenv import load_dotenv

try:
    from .weekly_avg import RenphoScalesData
    from .constants import goal_weight, phase_1_weekly_calories, starting_weight
    from .fatsecret import (
        get_calories_and_protein_summary,
        get_food_diary_entries_for_last_7_days,
    )
except ImportError:
    from weekly_avg import RenphoScalesData
    from constants import goal_weight, phase_1_weekly_calories, starting_weight
    from fatsecret import (
        get_calories_and_protein_summary,
        get_food_diary_entries_for_last_7_days,
    )

load_dotenv()


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def format_change(current, previous, units):
    change = current - previous
    direction = "down" if change < 0 else "up"
    return f"{current:.2f}{units} ({direction} {abs(change):.2f}{units} vs last week)"


def build_weight_summary_message(today: date | None = None) -> str:
    email = _required_env("MY_EMAIL")
    password = _required_env("MY_PASSWORD")
    renpho_data = RenphoScalesData(email, password)

    today = today or datetime.now().date()
    yesterday = today - timedelta(days=1)
    last_week = today - timedelta(days=8)

    weight_now = renpho_data.get_rolling_weekly_avg(yesterday, "weight")
    weight_prev = renpho_data.get_rolling_weekly_avg(last_week, "weight")

    bf_now = renpho_data.get_rolling_weekly_avg(yesterday, "bodyfat")
    bf_prev = renpho_data.get_rolling_weekly_avg(last_week, "bodyfat")

    lines = ["Weekly fitness summary"]
    lines.append(f"Date: {today.strftime('%d/%m/%Y')}")

    if weight_now is not None and weight_prev is not None:
        lines.append(f"Weight: {format_change(weight_now, weight_prev, 'kg')}")
        cumulative_change = weight_now - starting_weight
        target_change_since_start = goal_weight - starting_weight
        proportion_of_goal = (cumulative_change / target_change_since_start) * 100 if target_change_since_start != 0 else 0
        lines.append(f"Cumulative change since start: {cumulative_change:.2f}kg ({proportion_of_goal:.1f}% of the way to goal)")
    else:
        lines.append("Weight: insufficient Renpho data")

    if bf_now is not None and bf_prev is not None:
        lines.append(f"Body fat: {format_change(bf_now, bf_prev, '%')}")
    else:
        lines.append("Body fat: insufficient Renpho data")

    return "\n".join(lines)


def build_food_summary_message(selected_date: date | None = None) -> str:
    selected_date = selected_date or (date.today() - timedelta(days=1))
    food_diary_entries = get_food_diary_entries_for_last_7_days(selected_date)
    nutrition_summary = get_calories_and_protein_summary(food_diary_entries)
    calorie_difference = (
        nutrition_summary["total_calories"]
        - phase_1_weekly_calories * nutrition_summary["logged_day_count"]
    )
    calorie_difference_label = "surplus" if calorie_difference > 0 else "deficit"

    return (
        "Food diary summary\n"
        f"Average daily calories over the last 7 days: {nutrition_summary['average_daily_calories']:.2f}\n"
        f"Average daily protein over the last 7 days: {nutrition_summary['average_daily_protein']:.2f}g\n"
        f"Total calorie {calorie_difference_label}: {abs(calorie_difference):.0f} calories"
    )


async def send_telegram_message(message: str) -> None:
    bot_token = _required_env("FITNESS_SUMMARY_BOT_TOKEN")
    chat_id = _required_env("TELEGRAM_CHAT_ID")

    bot = telegram.Bot(bot_token)
    async with bot:
        await bot.send_message(chat_id=chat_id, text=message)


async def send_weight_summary() -> None:
    await send_telegram_message(build_weight_summary_message())


async def send_food_summary() -> None:
    await send_telegram_message(build_food_summary_message())


async def main() -> None:
    await send_weight_summary()


if __name__ == "__main__":
    asyncio.run(main())
