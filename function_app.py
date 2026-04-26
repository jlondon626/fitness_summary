import logging
import azure.functions as func

from weekly_fitness_summary.weekly_telegram_summary import main

app = func.FunctionApp()

@app.function_name(name="weekly_fitness_summary")
@app.timer_trigger(
    schedule="0 30 7 * * 0",  # Every Sunday at 7:30 AM
    arg_name="mytimer",
    run_on_startup=False,
    use_monitor=True,
)
async def weekly_fitness_summary(mytimer: func.TimerRequest) -> None:
    logging.info("Weekly fitness summary timer triggered.")
    await main()
