import asyncio
import os
from datetime import datetime, timedelta

import telegram
from dotenv import load_dotenv

from weekly_avg import RenphoScalesData


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
    last_week = today - timedelta(days=7)

    weight_now = renpho_data.get_rolling_weekly_avg(today, "weight")
    weight_prev = renpho_data.get_rolling_weekly_avg(last_week, "weight")

    bf_now = renpho_data.get_rolling_weekly_avg(today, "bodyfat")
    bf_prev = renpho_data.get_rolling_weekly_avg(last_week, "bodyfat")

    lines = ["Weekly fitness summary"]
    lines.append(f"Date: {today.strftime('%d/%m/%Y')}")

    if weight_now is not None and weight_prev is not None:
        lines.append(f"Weight: {format_change(weight_now, weight_prev, 'kg')}")
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