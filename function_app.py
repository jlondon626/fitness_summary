import logging
import azure.functions as func

from weekly_fitness_summary.weekly_telegram_summary import main

app = func.FunctionApp()

@app.timer_trigger(
    schedule="0 */5 * * * *",  # 06:17 UTC every day
    arg_name="mytimer",
    run_on_startup=False,
    use_monitor=True,
)
async def weekly_fitness_summary(mytimer: func.TimerRequest) -> None:
    logging.info("Weekly fitness summary timer triggered.")
    await main()
