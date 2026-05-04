import logging
import azure.functions as func

from weekly_fitness_summary.competition_scoring import score_active_challenges
from weekly_fitness_summary.raw_fitness_sync import sync_daily_fitness_raw
from weekly_fitness_summary.weekly_telegram_summary import (
    generate_competition_leaderboard_summary,
    send_food_summary,
    send_weight_summary,
    should_send_competition_leaderboard,
)

app = func.FunctionApp()

@app.function_name(name="weekly_fitness_summary")
@app.timer_trigger(
    schedule="0 30 7 * * 0",  # 08:30 BST every Sunday
    arg_name="mytimer",
    run_on_startup=False,
    use_monitor=True,
)
async def weekly_fitness_summary(mytimer: func.TimerRequest) -> None:
    if not should_send_competition_leaderboard("week"):
        logging.info("Skipping weekly fitness summary because monthly/final leaderboard is due.")
        return

    logging.info("Weekly fitness summary timer triggered.")
    await send_weight_summary()

    logging.info("Food diary logging timer triggered.")
    await send_food_summary()


@app.function_name(name="sync_daily_fitness_raw")
@app.timer_trigger(
    schedule="0 45 23 * * *",
    arg_name="mytimer",
    run_on_startup=False,
    use_monitor=True,
)
def sync_daily_fitness_raw_timer(mytimer: func.TimerRequest) -> None:
    logging.info("Daily raw fitness sync timer triggered.")
    saved_documents = sync_daily_fitness_raw()
    logging.info("Daily raw fitness sync saved %s document(s).", len(saved_documents))


@app.function_name(name="score_weekly_fitness_competition")
@app.timer_trigger(
    schedule="0 15 0 * * 0",
    arg_name="mytimer",
    run_on_startup=False,
    use_monitor=True,
)
def score_weekly_fitness_competition_timer(mytimer: func.TimerRequest) -> None:
    logging.info("Weekly fitness competition scoring timer triggered.")
    saved_documents = score_active_challenges()
    logging.info("Weekly fitness competition scoring saved %s document(s).", len(saved_documents))


@app.function_name(name="weekly_competition_leaderboard_summary")
@app.timer_trigger(
    schedule="0 30 7 * * 0",  # 08:30 Europe/London during the May-Aug BST challenge window
    arg_name="mytimer",
    run_on_startup=False,
    use_monitor=True,
)
async def weekly_competition_leaderboard_summary_timer(mytimer: func.TimerRequest) -> None:
    if not should_send_competition_leaderboard("week"):
        logging.info("Skipping weekly leaderboard; monthly/final leaderboard has priority.")
        return

    logging.info("Weekly competition leaderboard app message timer triggered.")
    await generate_competition_leaderboard_summary("week")


@app.function_name(name="monthly_competition_leaderboard_summary")
@app.timer_trigger(
    schedule="0 30 7 * * 0",  # 08:30 Europe/London during the May-Aug BST challenge window
    arg_name="mytimer",
    run_on_startup=False,
    use_monitor=True,
)
async def monthly_competition_leaderboard_summary_timer(mytimer: func.TimerRequest) -> None:
    if not should_send_competition_leaderboard("month"):
        logging.info("Skipping monthly leaderboard; it is not the first Sunday after a month end.")
        return

    logging.info("Monthly competition leaderboard app message timer triggered.")
    await generate_competition_leaderboard_summary("month")


@app.function_name(name="final_competition_leaderboard_summary")
@app.timer_trigger(
    schedule="0 30 7 * * 0",  # 08:30 Europe/London during the May-Aug BST challenge window
    arg_name="mytimer",
    run_on_startup=False,
    use_monitor=True,
)
async def final_competition_leaderboard_summary_timer(mytimer: func.TimerRequest) -> None:
    if not should_send_competition_leaderboard("final"):
        logging.info("Skipping final leaderboard; it is not the challenge's last Sunday.")
        return

    logging.info("Final competition leaderboard app message timer triggered.")
    await generate_competition_leaderboard_summary("final")
