import logging
from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import InlineQueryHandler, filters, MessageHandler, ApplicationBuilder, CommandHandler, ContextTypes
from dotenv import load_dotenv
from uuid import uuid4

import os

load_dotenv()

fitness_summary_bot_token = os.getenv("FITNESS_SUMMARY_BOT_TOKEN")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text="Hello! I'm your fitness summary bot. I'll send you weekly updates on your fitness progress."
    )

async def inline_caps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query
    if not query:
        return
    results = []
    results.append(
        InlineQueryResultArticle(
            id=str(uuid4()),
            title='Caps',
            input_message_content=InputTextMessageContent(query.upper())
        )
    )
    await context.bot.answer_inline_query(update.inline_query.id, results)

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text=update.message.text
    )

async def caps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text_caps = ' '.join(context.args).upper()
    await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text=text_caps
    )

if __name__ == '__main__':
    application = ApplicationBuilder().token(fitness_summary_bot_token).build()

    start_handler = CommandHandler('start', start)
    echo_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, echo)
    caps_handler = CommandHandler('caps', caps)

    application.add_handler(start_handler)
    application.add_handler(echo_handler)
    application.add_handler(caps_handler)

    inline_caps_handler = InlineQueryHandler(inline_caps)
    application.add_handler(inline_caps_handler)

    application.run_polling()