import asyncio
import json
import os
from datetime import date, datetime, timedelta
from typing import Any

import telegram
from azure.cosmos import CosmosClient
from dotenv import load_dotenv
from openai import AsyncAzureOpenAI

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


def _optional_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


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
        f"Total calorie {calorie_difference_label}: {abs(calorie_difference):.0f} calories\n"
        f"(Based on {nutrition_summary['logged_day_count']} logged days out of 7)"
    )


def _compact_for_prompt(value: Any, max_string_length: int = 500) -> Any:
    if isinstance(value, dict):
        return {
            key: _compact_for_prompt(item, max_string_length)
            for key, item in value.items()
            if not key.startswith("_")
        }
    if isinstance(value, list):
        return [_compact_for_prompt(item, max_string_length) for item in value[:50]]
    if isinstance(value, str) and len(value) > max_string_length:
        return f"{value[:max_string_length]}..."
    return value


def get_cosmos_fitness_records() -> list[dict[str, Any]]:
    connection_string = _required_env("COSMOS_DB_CONNECTION_STRING")
    database_name = _required_env("COSMOS_DB_DATABASE_NAME")
    container_name = _required_env("COSMOS_DB_CONTAINER_NAME")
    item_limit = max(1, min(_optional_int_env("COSMOS_DB_AI_ITEM_LIMIT", 100), 200))
    query = os.getenv(
        "COSMOS_DB_AI_QUERY",
        f"SELECT TOP {item_limit} * FROM c ORDER BY c._ts DESC",
    )

    client = CosmosClient.from_connection_string(connection_string)
    database = client.get_database_client(database_name)
    container = database.get_container_client(container_name)

    records = list(
        container.query_items(
            query=query,
            enable_cross_partition_query=True,
        )
    )
    return [_compact_for_prompt(record) for record in records[:item_limit]]


def _build_ai_prompt_payload(today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    week_end = today - timedelta(days=1)
    week_start = week_end - timedelta(days=6)

    return {
        "report_date": today.isoformat(),
        "previous_week": {
            "start_date": week_start.isoformat(),
            "end_date": week_end.isoformat(),
        },
        "targets": {
            "goal_weight_kg": goal_weight,
            "starting_weight_kg": starting_weight,
            "daily_calorie_target": phase_1_weekly_calories,
        },
        "computed_summaries": {
            "weight": build_weight_summary_message(today),
            "food": build_food_summary_message(week_end),
        },
        "cosmos_records": get_cosmos_fitness_records(),
    }


async def build_ai_feedback_message(today: date | None = None) -> str:
    deployment = _required_env("AZURE_OPENAI_DEPLOYMENT")
    payload = _build_ai_prompt_payload(today)

    async with AsyncAzureOpenAI(
        api_key=_required_env("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
        azure_endpoint=_required_env("AZURE_OPENAI_ENDPOINT"),
    ) as client:
        completion = await client.chat.completions.create(
            model=deployment,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a practical fitness coach. Use only the supplied data. "
                        "Give concise, specific feedback on the previous week and clear "
                        "advice for the week ahead. Do not diagnose medical conditions."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Create a Telegram-friendly weekly coaching note from this JSON. "
                        "Keep it under 1200 characters. Include: 1) previous week feedback, "
                        "2) week ahead advice, 3) specific aims for fitness sessions, 4) one measurable focus.\n\n"
                        f"{json.dumps(payload, default=str, ensure_ascii=False)}"
                    ),
                },
            ],
            max_tokens=450,
            temperature=0.4,
        )

    content = completion.choices[0].message.content
    if not content:
        raise RuntimeError("Azure OpenAI returned an empty response")
    return f"AI coaching feedback\n{content.strip()}"


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


async def send_ai_feedback_summary() -> None:
    await send_telegram_message(await build_ai_feedback_message())


async def main() -> None:
    await send_weight_summary()


if __name__ == "__main__":
    asyncio.run(main())
