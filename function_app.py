import logging
import json
import os
from datetime import datetime
import azure.functions as func

from weekly_fitness_summary.competition_scoring import preview_score_period, score_active_challenges
from weekly_fitness_summary.competition_stats import (
    get_or_build_challenge_stats,
    refresh_active_challenge_stats,
)
from weekly_fitness_summary.raw_fitness_sync import sync_daily_fitness_raw
from weekly_fitness_summary.weekly_telegram_summary import (
    generate_competition_leaderboard_summary,
    send_food_summary,
    send_weight_summary,
    should_send_competition_leaderboard,
    should_send_routine_weekly_summary,
)

app = func.FunctionApp()


def _configured_auth_token() -> str | None:
    return (
        os.getenv("AUTH_TOKEN")
        or os.getenv("HEALTH_API_TOKEN")
        or os.getenv("FITNESS_API_TOKEN")
    )


def _is_authorized(req: func.HttpRequest) -> bool:
    expected_token = _configured_auth_token()
    if not expected_token:
        return True

    authorization = req.headers.get("Authorization", "")
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return False
    return authorization[len(prefix):].strip() == expected_token


def _json_response(body: dict, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(body),
        status_code=status_code,
        mimetype="application/json",
    )


def _parse_query_date(value: str | None):
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


@app.function_name(name="weekly_fitness_summary")
@app.timer_trigger(
    schedule="0 30 7 * * 0",  # 08:30 BST every Sunday
    arg_name="mytimer",
    run_on_startup=False,
    use_monitor=True,
)
async def weekly_fitness_summary(mytimer: func.TimerRequest) -> None:
    if not should_send_routine_weekly_summary():
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


@app.function_name(name="refresh_daily_challenge_stats")
@app.timer_trigger(
    schedule="0 0 5 * * *",  # 06:00 Europe/London during BST
    arg_name="mytimer",
    run_on_startup=False,
    use_monitor=True,
)
def refresh_daily_challenge_stats_timer(mytimer: func.TimerRequest) -> None:
    logging.info("Daily challenge stats refresh timer triggered.")
    saved_documents = refresh_active_challenge_stats()
    logging.info("Daily challenge stats refresh saved %s document(s).", len(saved_documents))


@app.function_name(name="get_challenge_stats")
@app.route(
    route="challenges/{challengeID}/stats",
    methods=["GET"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def get_challenge_stats(req: func.HttpRequest) -> func.HttpResponse:
    if not _is_authorized(req):
        return _json_response({"error": "Forbidden"}, status_code=403)

    challenge_id = req.route_params.get("challengeID")
    period = (req.params.get("period") or "week").strip().lower()

    if not challenge_id:
        return _json_response({"error": "Missing challengeID."}, status_code=400)

    try:
        stats = get_or_build_challenge_stats(challenge_id, period)
    except ValueError as exc:
        return _json_response({"error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return _json_response({"error": str(exc)}, status_code=404)
    except Exception:
        logging.exception("Failed to build challenge stats.")
        return _json_response({"error": "Failed to build challenge stats."}, status_code=500)

    return _json_response(stats)


@app.function_name(name="preview_competition_scores")
@app.route(
    route="challenges/{challengeID}/scores/preview",
    methods=["GET"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
def preview_competition_scores(req: func.HttpRequest) -> func.HttpResponse:
    if not _is_authorized(req):
        return _json_response({"error": "Forbidden"}, status_code=403)

    challenge_id = req.route_params.get("challengeID")
    if not challenge_id:
        return _json_response({"error": "Missing challengeID."}, status_code=400)

    try:
        period_start = _parse_query_date(
            req.params.get("startDate")
            or req.params.get("periodStart")
            or req.params.get("from")
        )
        period_end = _parse_query_date(
            req.params.get("endDate")
            or req.params.get("periodEnd")
            or req.params.get("to")
        )
        today = _parse_query_date(req.params.get("today"))
        scores = preview_score_period(
            period_start,
            period_end,
            challenge_id=challenge_id,
            today=today,
        )
    except ValueError as exc:
        return _json_response({"error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return _json_response({"error": str(exc)}, status_code=404)
    except Exception:
        logging.exception("Failed to preview competition scores.")
        return _json_response({"error": "Failed to preview competition scores."}, status_code=500)

    response = {
        "challengeID": challenge_id,
        "periodStartDate": scores[0]["weekStartDate"] if scores else period_start.isoformat() if period_start else None,
        "periodEndDate": scores[0]["weekEndDate"] if scores else period_end.isoformat() if period_end else None,
        "persisted": False,
        "scores": scores,
    }
    return _json_response(response)


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
