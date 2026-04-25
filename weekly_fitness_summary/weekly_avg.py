from datetime import datetime, timedelta

from renpho import RenphoClient, save_json, save_csv, RenphoAPIError
from dotenv import load_dotenv
import os

load_dotenv()

email = os.getenv("MY_EMAIL")
password = os.getenv("MY_PASSWORD")

def get_rolling_weekly_avg(date: datetime.date) -> float:
    client = RenphoClient(email, password)
    try:
        client.login()
    except RenphoAPIError as e:
        print(f"Login failed: {e}")

    # Fetch all measurements in one call
    measurements = client.get_all_measurements()

    result = ([
            {
                "Date": datetime.strptime(m['localCreatedAt'], '%Y-%m-%d    %H:%M:%S').strftime('%Y-%m-%d'), 
                "Weight": m['weight']
            } 
            for m in measurements
        ])

    last_7_days = [m for m in result if datetime.strptime(m["Date"], '%Y-%m-%d').date() > date - timedelta(days=7) and datetime.strptime(m["Date"], '%Y-%m-%d').date() <= date]
    rolling_weekly_avg = sum(m["Weight"] for m in last_7_days) / len(last_7_days) if last_7_days else 0

    return rolling_weekly_avg

if __name__ == "__main__":
    avg = get_rolling_weekly_avg(datetime.now().date())
    print(f"Rolling weekly average weight: {avg:.2f} kg")