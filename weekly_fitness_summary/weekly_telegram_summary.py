import asyncio
import os
from datetime import datetime, timedelta

import telegram
from dotenv import load_dotenv

try:
    from .weekly_avg import RenphoScalesData
    from .constants import goal_weight, starting_weight
except ImportError:
    from weekly_avg import RenphoScalesData
    from constants import goal_weight, starting_weight

load_dotenv()

EMAIL = os.getenv("MY_EMAIL")
PASSWORD = os.getenv("MY_PASSWORD")
BOT_TOKEN = os.getenv("FITNESS_SUMMARY_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def format_change(current, previous, units):
    change = current - previous
    direction = "down" if change < 0 else "up"
    return f"{current:.2f}{units} ({direction} {abs(change):.2f}{units} vs last week)"


async def main():
    renpho_data = RenphoScalesData(EMAIL, PASSWORD)

    
    today = datetime.now().date()
    yesterday = datetime.now().date() - timedelta(days=1) # dont include today in averaging
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

    message = "\n".join(lines)

    bot = telegram.Bot(BOT_TOKEN)
    async with bot:
        await bot.send_message(chat_id=CHAT_ID, text=message)


if __name__ == "__main__":
    asyncio.run(main())
