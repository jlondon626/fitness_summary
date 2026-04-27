import os
import json
import webbrowser
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qsl

import requests
from dotenv import load_dotenv
from requests_oauthlib import OAuth1Session


load_dotenv(Path(__file__).resolve().parents[1] / ".env")

CLIENT_ID = (os.getenv("FATSECRET_CLIENT_ID") or "").strip()
CLIENT_SECRET = (os.getenv("FATSECRET_CLIENT_SECRET") or "").strip()
CONSUMER_KEY = (os.getenv("FATSECRET_CONSUMER_KEY") or "").strip()
CONSUMER_SECRET = (os.getenv("FATSECRET_CONSUMER_SECRET") or "").strip()
DIARY_ACCESS_TOKEN = (os.getenv("FATSECRET_ACCESS_TOKEN") or "").strip()
DIARY_ACCESS_SECRET = (os.getenv("FATSECRET_ACCESS_SECRET") or "").strip()

TOKEN_URL = "https://oauth.fatsecret.com/connect/token"
API_URL = "https://platform.fatsecret.com/rest/server.api"
REQUEST_TOKEN_URL = "https://authentication.fatsecret.com/oauth/request_token"
AUTHORIZE_URL = "https://authentication.fatsecret.com/oauth/authorize"
ACCESS_TOKEN_URL = "https://authentication.fatsecret.com/oauth/access_token"


def get_access_token(scope: str = "basic") -> str:
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError(
            "Missing FATSECRET_CLIENT_ID or FATSECRET_CLIENT_SECRET in the repo .env file."
        )

    response = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials", "scope": scope},
        auth=(CLIENT_ID, CLIENT_SECRET),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    response.raise_for_status()

    token_response = response.json()
    return token_response["access_token"]


def search_foods(search_expression: str, *, max_results: int = 10) -> dict:
    access_token = get_access_token()

    response = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        data={
            "method": "foods.search",
            "search_expression": search_expression,
            "max_results": max_results,
            "format": "json",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def get_diary_authorization_tokens() -> dict:
    if not CONSUMER_KEY or not CONSUMER_SECRET:
        raise RuntimeError(
            "Missing FATSECRET_CONSUMER_KEY or FATSECRET_CONSUMER_SECRET in the repo .env file."
        )

    oauth = OAuth1Session(
        CONSUMER_KEY,
        client_secret=CONSUMER_SECRET,
        callback_uri="oob",
        signature_type="body",
    )
    request_token = oauth.fetch_request_token(REQUEST_TOKEN_URL)

    authorization_url = oauth.authorization_url(AUTHORIZE_URL)
    print(f"Open this URL and approve access:\n{authorization_url}")
    webbrowser.open(authorization_url)

    verifier = input("Paste the oauth_verifier code: ").strip()

    oauth = OAuth1Session(
        CONSUMER_KEY,
        client_secret=CONSUMER_SECRET,
        resource_owner_key=request_token["oauth_token"],
        resource_owner_secret=request_token["oauth_token_secret"],
        verifier=verifier,
        signature_type="query",
    )

    response = oauth.get(ACCESS_TOKEN_URL, timeout=30)
    response.raise_for_status()

    token_response = dict(parse_qsl(response.text))
    print("Add these to your local .env file:")
    print(f"FATSECRET_ACCESS_TOKEN={token_response['oauth_token']}")
    print(f"FATSECRET_ACCESS_SECRET={token_response['oauth_token_secret']}")
    return token_response


def _diary_oauth_session() -> OAuth1Session:
    if not all([CONSUMER_KEY, CONSUMER_SECRET, DIARY_ACCESS_TOKEN, DIARY_ACCESS_SECRET]):
        raise RuntimeError(
            "Missing FatSecret OAuth 1 diary credentials. Run get_diary_authorization_tokens() "
            "once, then add FATSECRET_ACCESS_TOKEN and FATSECRET_ACCESS_SECRET to .env."
        )

    return OAuth1Session(
        CONSUMER_KEY,
        client_secret=CONSUMER_SECRET,
        resource_owner_key=DIARY_ACCESS_TOKEN,
        resource_owner_secret=DIARY_ACCESS_SECRET,
        signature_type="query",
    )


def get_food_diary_entries(entry_date: date) -> dict:
    days_since_epoch = (entry_date - date(1970, 1, 1)).days
    oauth = _diary_oauth_session()

    response = oauth.get(
        API_URL,
        params={
            "method": "food_entries.get.v2",
            "date": days_since_epoch,
            "format": "json",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()

def get_food_diary_entries_for_month(from_date: date) -> dict:
    days_since_epoch_from = (from_date - date(1970, 1, 1)).days
    oauth = _diary_oauth_session()

    response = oauth.get(
        API_URL,
        params={
            "method": "food_entries.get_month.v2",
            "date": days_since_epoch_from,
            "format": "json",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()

def get_food_diary_entries_for_last_7_days(selected_date: date) -> dict:
    entries_by_date = {}

    for days_ago in range(6, -1, -1):
        entry_date = selected_date - timedelta(days=days_ago)
        entries_by_date[entry_date.isoformat()] = get_food_diary_entries(entry_date)

    return entries_by_date


def get_average_daily_calories_and_protein(food_diary_entries: dict) -> dict:
    total_calories = 0.0
    total_protein = 0.0
    logged_day_count = 0

    for day_entries in food_diary_entries.values():
        food_entries = (day_entries or {}).get("food_entries")
        if not food_entries:
            continue

        entries = food_entries.get("food_entry", [])
        if isinstance(entries, dict):
            entries = [entries]

        total_calories += sum(float(entry.get("calories", 0)) for entry in entries)
        total_protein += sum(float(entry.get("protein", 0)) for entry in entries)
        logged_day_count += 1

    if logged_day_count == 0:
        return {"average_daily_calories": 0.0, "average_daily_protein": 0.0}

    return {
        "average_daily_calories": total_calories / logged_day_count,
        "average_daily_protein": total_protein / logged_day_count,
    }


if __name__ == "__main__":
    if not DIARY_ACCESS_TOKEN or not DIARY_ACCESS_SECRET:
        get_diary_authorization_tokens()
    else:
        food_entries = get_food_diary_entries_for_last_7_days(date.today())
        average_food_metrics = get_average_daily_calories_and_protein(food_entries)
        print(json.dumps(average_food_metrics, indent=2))
