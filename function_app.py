import logging
import azure.functions as func

from weekly_fitness_summary.weekly_telegram_summary import (
    send_food_summary,
    send_weight_summary,
)

app = func.FunctionApp()

@app.function_name(name="weekly_fitness_summary")
@app.timer_trigger(
    schedule="0 10 21 * * *",  # Every Sunday at 7:30 AM
    arg_name="mytimer",
    run_on_startup=False,
    use_monitor=True,
)
async def weekly_fitness_summary(mytimer: func.TimerRequest) -> None:
    logging.info("Weekly fitness summary timer triggered.")
    await send_weight_summary()

    logging.info("Food diary logging timer triggered.")
    await send_food_summary()
