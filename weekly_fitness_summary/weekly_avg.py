from datetime import datetime, timedelta

from renpho import RenphoClient, save_json, save_csv, RenphoAPIError
from dotenv import load_dotenv
import os

load_dotenv()

email = os.getenv("MY_EMAIL")
password = os.getenv("MY_PASSWORD")

class RenphoScalesData:
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.client = None
        self.measurements = []
        self.login()
        self.get_all_measurements()
        
    def login(self):
        self.client = RenphoClient(self.email, self.password)
        try:
            self.client.login()
        except RenphoAPIError as e:
            print(f"Login failed: {e}")
            self.client = None

    def get_all_measurements(self):
        if not self.client:
            print("Not logged in.")
            return []
        try:
            self.measurements = self.client.get_all_measurements()
            return self.measurements
        except RenphoAPIError as e:
            print(f"Failed to fetch measurements: {e}")
            return []

    def get_rolling_weekly_avg(self,date: datetime.date, measure: str) -> float:

        # Fetch all measurements in one call
        result = ([
                {
                    "Date": datetime.strptime(m['localCreatedAt'], '%Y-%m-%d    %H:%M:%S').strftime('%Y-%m-%d'), 
                    measure: m[measure]
                } 
                for m in self.measurements
            ])

        last_7_days = [m for m in result if datetime.strptime(m["Date"], '%Y-%m-%d').date() > date - timedelta(days=7) and datetime.strptime(m["Date"], '%Y-%m-%d').date() <= date]
        
        rolling_weekly_avg = sum(m[measure] for m in last_7_days) / len(last_7_days) if last_7_days else None

        return rolling_weekly_avg

    @staticmethod
    def measure_units(measure: str) -> str:
        if measure == "weight":
            return "kg"
        elif measure == "bodyfat":
            return "%"
        else:
            return ""

if __name__ == "__main__":

    renpho_data = RenphoScalesData(email, password)

    for measure in ["weight", "bodyfat"]:
        avg = renpho_data.get_rolling_weekly_avg(datetime.now().date(), measure)
        if avg is not None:
            print(f"Rolling weekly average {measure}: {avg:.2f} {renpho_data.measure_units(measure)}")
        else:
            print(f"No measurements found in the last 7 days for {measure}.")

        avg_last_week = renpho_data.get_rolling_weekly_avg(datetime.now().date() - timedelta(days=7), measure)
        if avg_last_week is not None:
            print(f"Rolling weekly average {measure} (last week): {avg_last_week:.2f} {renpho_data.measure_units(measure)}")
        else:
            print(f"No measurements found for the previous week for {measure}.")

        if avg is not None and avg_last_week is not None:
            change = avg - avg_last_week
            print(f"{measure.capitalize()} change from last week: {change:.2f} {renpho_data.measure_units(measure)}")
            if avg_last_week != 0 and measure != "bodyfat":  # Avoid division by zero and percentage change for body fat
                print(f"Percentage change from last week: {change / avg_last_week * 100:.2f}%")
