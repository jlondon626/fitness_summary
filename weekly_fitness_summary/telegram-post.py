import asyncio
from datetime import datetime
import telegram

FITNESS_SUMMARY_BOT_TOKEN = "8587950405:AAEUGunScPbCuf59IV2AK5hXjnej5jYDqTM"

async def main():
    bot = telegram.Bot(FITNESS_SUMMARY_BOT_TOKEN)
    async with bot:
        # updates = (await bot.get_updates())[0]
        # print(updates)
        await bot.send_message(
            chat_id=8402772488, 
            text="Okay, a slight delay when it goes cold, but it works! The weekly fitness summary will be sent every Sunday at 6pm.",
        )

if __name__ == '__main__':
    asyncio.run(main())
