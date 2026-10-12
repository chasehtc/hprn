import datetime
import os
import config
import utils
import pandas as pd
import numpy as np
from google.cloud import storage
from google.oauth2 import service_account

CALENDAR_JSON = "clean/datemap.json"

start = datetime.datetime(2000, 1, 1, 0, 0)
end = datetime.datetime(2050, 1, 1, 0, 0)

def table_create():
    timespan = pd.date_range(start=start, end=end, freq='h', inclusive='left')

    cal = pd.DataFrame({
        "year": timespan.year,
        "month": timespan.month,
        "month_num": timespan.strftime('%B'),
        "qtr": timespan.quarter,
        "day": timespan.day,
        "day_name": timespan.strftime('%A'),
        "day_week": timespan.weekday + 1,
        "wknd": timespan.weekday > 5,
        "hour": timespan.hour
    })

    conditions = [
        (cal["hour"] > 5) & (cal["hour"] < 12),
        (cal["hour"] > 11) & (cal["hour"] < 19)
    ]

    choices = ["Morning", "Evening"]
    cal["time_of_day"] = np.select(conditions, choices, default="Night")

    n = len(cal)
    cal["time_id"] = [f"T{i:08}" for i in range(1, n + 1)]

    columns = ["year", "month", "month_num", "qtr", "day", "day_name", "day_week", "wknd", "time_of_day", "hour", "time_id"]
    cal = cal[columns]

    cal.to_json("clean/datemap.json", orient="records", lines=True)
    print(cal)

    utils.upload_to_gcs(CALENDAR_JSON, "staged/datemap.json")
    print("\n[COMPLETE] Calendar execution exported successfully.")

if __name__ == "__main__":
    if not utils.can_execute("datetime_dim"):
        exit()

    table_create()