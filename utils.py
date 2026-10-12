import os
import tempfile
import requests
import json
from google.cloud import storage, bigquery
from google.oauth2 import service_account
from datetime import datetime, timedelta
import config

PIPELINE_STATES = {
    "venues": {"last_run": None, "interval_hours": 720},       # Monthly (720 hours)
    "finalize": {"last_run": None, "interval_hours": 720},       # Monthly (720 hours)
    "demographics": {"last_run": None, "interval_hours": 720}, # Monthly (720 hours)
    "traffic": {"last_run": None, "interval_hours": 24},  # Daily (24 hours)
    "social": {"last_run": None, "interval_hours": 24},        # Daily (24 hours)
    "date": {"last_run": None, "interval_hours": 0}    # Once (Manual/Static)
}
POSITIVE_KEYWORDS = [
    "basketball", "gym", "fieldhouse", "recreation", "rec", 
    "ymca", "y.m.c.a", "gymnasium", "community", "arena", 
    "sports", "complex", "court", "center", "centre",
    "pickup", "open run", "hoop", "hooping","5s", "runs tonight", 
    "need players", "scrimmage"
]
NEGATIVE_KEYWORDS = [
    "yoga", "pilates", "spin", "crossfit", "dance", "ballet", "gymnastics",
    "martial arts", "karate", "taekwondo", "boxing", "f45", "orangetheory",
    "physiotherapy", "massage", "spa", "salon", "swim", "pool", "tennis",
    "batting", "baseball", "racing", "cages", "racquet", "guns", "ice arena",
    "diving", "golf", "outdoor", "kung fu", "skating", "sluggers", "tae kwon do",
    "dojo", "jiu", "jitsu", "kendo", "fencing", "grappling", "raceway", "climbing",
    "driving range", "paintball", "shooting", "gun range", "racing", "cages"
    "race course", "racetrack", "bowling", "soccer", "softball", "aquatic", "park ",
    "fencing", "volleyball", "pickleball", "skate", "martial", "senior center"
]

def load_states():
    if os.path.exists(config.STATE_FILE):
        try:
            with open(config.STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return PIPELINE_STATES

def save_states(states):
    abs_path = os.path.abspath(config.STATE_FILE)
    print(f"[DEBUG] Writing state to absolute path: {abs_path}")
    with open(config.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(states, f, indent=2)

def can_execute(task_name):
    states = load_states()
    if task_name not in states:
        print(f"[WARN] Unknown task '{task_name}'. Defaulting to allow execution.")
        return True

    task_info = states[task_name]
    last_run_str = task_info.get("last_run")
    interval_hours = task_info.get("interval_hours", 24)
    current_time = datetime.now()

    if last_run_str:
        last_run_time = datetime.fromisoformat(last_run_str)
        next_allowed_run = last_run_time + timedelta(hours=interval_hours)
        if current_time < next_allowed_run:
            print(f"[SKIP] {task_name.upper()} ran recently ({last_run_str}).")
            return False

    # Force timestamp update and print confirmation
    timestamp_str = current_time.isoformat()
    states[task_name]["last_run"] = timestamp_str
    save_states(states)
    print(f"[SUCCESS] Timestamp for '{task_name}' successfully updated to {timestamp_str} in {config.STATE_FILE}")
    return True

def get_gcs_client():
    """Initializes and returns an authenticated Google Cloud Storage client."""
    if not os.path.exists(config.CREDENTIALS_FILE):
        raise FileNotFoundError(f"Credentials file not found at: {config.CREDENTIALS_FILE}")
    credentials = service_account.Credentials.from_service_account_file(config.CREDENTIALS_FILE)
    return storage.Client(credentials=credentials, project=config.PROJECT_ID)

def upload_to_gcs(local_filename, gcs_path):
    """Centralized function to upload any local file to Google Cloud Storage."""
    try:
        client = get_gcs_client()
        bucket = client.bucket(config.BUCKET_NAME)
        blob = bucket.blob(gcs_path)
        blob.upload_from_filename(local_filename)
        print(f"  [GCS SUCCESS] Uploaded: gs://{config.BUCKET_NAME}/{gcs_path}")
    except Exception as e:
        print(f"  [GCS ERROR] Upload failed for {gcs_path}: {e}")

def get_bigquery_client():
    """Initializes and returns an authenticated BigQuery client."""
    if not os.path.exists(config.CREDENTIALS_FILE):
        raise FileNotFoundError(f"Credentials file not found at: {config.CREDENTIALS_FILE}")
    credentials = service_account.Credentials.from_service_account_file(config.CREDENTIALS_FILE)
    return bigquery.Client(credentials=credentials, project=config.PROJECT_ID)

def resolve_zip_to_city(zip_code):
    """Centralized helper to resolve a ZIP code to a city and state abbreviation."""
    url = f"http://api.zippopotam.us/us/{zip_code}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data["places"][0]["place name"], data["places"][0]["state abbreviation"]
    except Exception as e:
        print(f"  [WARN] Failed to resolve ZIP {zip_code}: {e}")
    return None, None

def load_processed_zips():
    """Loads processed ZIP codes and their per-scraper status dictionary safely."""
    if os.path.exists(config.TRACKING_FILE):
        try:
            with open(config.TRACKING_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, Exception):
            # Fallback if file is empty or corrupted
            return {}
    return {}

def save_processed_zip(zip_code, scraper_type, is_done=True):
    os.makedirs(os.path.dirname(config.TRACKING_FILE), exist_ok=True)
    
    data = load_processed_zips()

    if zip_code not in data:
        data[zip_code] = {}
    data[zip_code][scraper_type] = is_done
    
    dir_name = os.path.dirname(config.TRACKING_FILE)
    try:
        # Create a temp file in the target directory
        with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False, encoding="utf-8") as tf:
            json.dump(data, tf, indent=2)
            temp_name = tf.name
          
        os.replace(temp_name, config.TRACKING_FILE)
        
    except Exception as e:
        print(f"  [WARN] Failed to safely update tracking file: {e}")
        # Clean up temp file if replacement failed
        if 'temp_name' in locals() and os.path.exists(temp_name):
            os.remove(temp_name)

def is_scraper_done(zip_code, scraper_type):
  """Checks if a specific scraper has completed for a ZIP code."""
  data = load_processed_zips()
  return data.get(zip_code, {}).get(scraper_type, False)

def is_zip_complete(zip_code, required_scrapers=("google", "osm", "municipal")):
  """Checks if all required scrapers have completed for a ZIP code."""
  data = load_processed_zips()
  zip_data = data.get(zip_code, {})
  return all(zip_data.get(scraper, False) for scraper in required_scrapers)