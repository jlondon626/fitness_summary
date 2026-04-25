import asyncio
from datetime import datetime
import telegram
from dotenv import load_dotenv
import os

load_dotenv()

fitness_summary_bot_token = os.getenv("FITNESS_SUMMARY_BOT_TOKEN")
telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

async def main():
    bot = telegram.Bot(fitness_summary_bot_token)
    async with bot:
        # updates = (await bot.get_updates())[0]
        # print(updates)
        await bot.send_message(
            chat_id=telegram_chat_id, 
            text="Okay, a slight delay when it goes cold, but it works! The weekly fitness summary will be sent every Sunday at 6pm.",
        )

if __name__ == '__main__':
    asyncio.run(main())
