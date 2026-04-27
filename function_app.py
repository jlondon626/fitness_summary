from datetime import date, timedelta, timedelta
import logging
import azure.functions as func

from weekly_fitness_summary.weekly_telegram_summary import main, send_telegram_message
from weekly_fitness_summary.fatsecret import get_food_diary_entries_for_last_7_days, get_average_daily_calories_and_protein

app = func.FunctionApp()

@app.function_name(name="weekly_fitness_summary")
@app.timer_trigger(
    schedule="0 35 21 * * *",  # Every Sunday at 7:30 AM
    arg_name="mytimer",
    run_on_startup=False,
    use_monitor=True,
)
async def weekly_fitness_summary(mytimer: func.TimerRequest) -> None:
    logging.info("Weekly fitness summary timer triggered.")
    await main()

async def log_food_diary_entries(mytimer: func.TimerRequest) -> None:
    logging.info("Food diary logging timer triggered.")
    selected_date = date.today() - timedelta(days=1)  # Log for the previous day to ensure data is available
    food_diary_entries = get_food_diary_entries_for_last_7_days(selected_date)
    averages = get_average_daily_calories_and_protein(food_diary_entries)

    await send_telegram_message(
        f"Average daily calories over the last 7 days: {averages['average_daily_calories']:.2f}\n"
        f"Average daily protein over the last 7 days: {averages['average_daily_protein']:.2f}"
    )
