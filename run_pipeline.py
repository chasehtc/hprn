import os
import config
import utils
import glob
import json
import requests
import pandas as pd
import numpy as np
from datetime import datetime
from difflib import SequenceMatcher
from google.cloud import storage 
from google.cloud import bigquery
from google.oauth2 import service_account  

# --- CONFIGURATION & SAFETY CEILINGS ---
LOCAL_ENRICHED = "clean/final_venues.csv"
LOCAL_ENRICHED_JSON = "clean/final_venues.json"
 
MAX_ENRICHMENTS_PER_RUN = 2000
# Map your local/GCS files to their target BigQuery table names and formats
BQ_TABLE_MAPPINGS = {    
    "raw_venues": {        
        "table_name": "raw_venues",        
        "format": bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,        
        "gcs_path": "raw/venues/final_venues.json",       
        "write_disposition": bigquery.WriteDisposition.WRITE_TRUNCATE # Master list is fully recompiled each run    
    },    
    "raw_traffic": {        
        "table_name": "raw_traffic",        
        "format": bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,        
        "gcs_path": "raw/traffic/", # Prefix pattern or latest batch (use WRITE_APPEND)        
        "write_disposition": bigquery.WriteDisposition.WRITE_APPEND # Accumulate historical traffic snapshots    
    },    
    "raw_demographics": {        
        "table_name": "raw_demographics",        
        "format": bigquery.SourceFormat.CSV,        
        "gcs_path": "staged/demographics.csv",        
        "write_disposition": bigquery.WriteDisposition.WRITE_TRUNCATE # Static reference table    
    },    
    "raw_social": {        
        "table_name": "raw_social",        
        "format": bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,        
        "gcs_path": "raw/social/", # Target folder for timestamped batch files        
        "write_disposition": bigquery.WriteDisposition.WRITE_APPEND # Accumulate social growth signals safely over time 
              },    
    "dim_datetime": {        
        "table_name": "dim_datetime",        
        "format": bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,        
        "gcs_path": "staged/datemap.json",        
        "write_disposition": bigquery.WriteDisposition.WRITE_TRUNCATE # Static date mapping table    
    }
}

def load_master_and_enriched():    
    print("[1/3] Loading local master and existing cache context...")    
    if not os.path.exists(config.LOCAL_MASTER):        
        raise FileNotFoundError(f"Master file '{config.LOCAL_MASTER}' missing. Please run merge_venues.py first.")    
    df_master = pd.read_csv(config.LOCAL_MASTER, dtype={'place_id': str})    
    df_existing = pd.DataFrame()    
    if os.path.exists(LOCAL_ENRICHED):        
        print("  Sourced local directory cache.")        
        df_existing = pd.read_csv(LOCAL_ENRICHED, dtype={'place_id': str})    
    return df_master, df_existing    

def fetch_google_place_details(place_id):    
    url = f"https://places.googleapis.com/v1/places/{place_id}"    
    headers = {        
        "Content-Type": "application/json",        
        "X-Goog-Api-Key": config.GOOGLE_PLACES_API_KEY,        
        "X-Goog-FieldMask": "id,regularOpeningHours,websiteUri,nationalPhoneNumber,location"    
    }    
    try:        
        response = requests.get(url, headers=headers, timeout=15)        
        response.raise_for_status()        
        return response.json()    
    except Exception as e:        
        print(f"    [API ERROR] Place Details failed for {place_id}: {e}")        
        return None    

def find_place_id_by_location(name, address, lat, lon, zip_code):    
    """Uses Google Places Text Search with available OSM/Municipal info to resolve missing fields and find a place_id and full details."""    
    url = "https://places.googleapis.com/v1/places:searchText"    
    headers = {        
        "Content-Type": "application/json",        
        "X-Goog-Api-Key": config.GOOGLE_PLACES_API_KEY,        
        "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location,places.regularOpeningHours,places.websiteUri,places.nationalPhoneNumber"    }        
    
    if pd.notna(address) and str(address).strip() != "":        
        query = f"{name} at {address}"    
    elif pd.notna(zip_code):        
        query = f"{name} in {zip_code}"    
    else:        
        query = f"{name} near {lat}, {lon}"            
    payload = {"textQuery": query}        
    try:        
        response = requests.post(url, json=payload, headers=headers, timeout=15)        
        response.raise_for_status()        
        data = response.json()        
        places = data.get("places", [])        
        if places:            
            return places[0]    
    except Exception as e:        
        print(f"    [SEARCH ERROR] Failed to resolve place for {name}: {e}")    
    return None  

def enrich_pipeline():
    if config.GOOGLE_PLACES_API_KEY == "YOUR_ACTUAL_config.GOOGLE_PLACES_API_KEY_HERE" or not config.GOOGLE_PLACES_API_KEY:
        print("[ERROR] Please replace 'YOUR_ACTUAL_config.GOOGLE_PLACES_API_KEY_HERE' with your real Places key.")
        return

    run_enrichment = True
    if hasattr(utils, "can_execute"):
        if not utils.can_execute("enrich"):
            print("[INFO] Venue enrichment interval not met. Skipping API enrichment and fallback processes.")
            run_enrichment = False

    if run_enrichment:
        df_master, df_existing = load_master_and_enriched()
        cached_ids = set()
        enriched_records = []

        if not df_existing.empty:
            df_existing_clean = df_existing.dropna(subset=["place_id"])
            df_existing_clean["place_id"] = df_existing_clean["place_id"].astype(str)
            enriched_records = df_existing_clean.to_dict("records")
            cached_ids = set(df_existing_clean[df_existing_clean["enriched"] == True]["place_id"].astype(str))
            print(f"  Bypassing {len(cached_ids)} already enriched cached registers.")

        print("\n[2/3] Parsing and filtering targets for API Details...")
        for col in ["enriched", "phone", "website", "opening_hours"]:
            if col not in df_master.columns:
                df_master[col] = None

        api_calls_made = 0
        existing_place_ids = {str(r["place_id"]): r for r in enriched_records}
        enrichment_count = 0
        
        for index, row in df_master.iterrows():
            if enrichment_count >= MAX_ENRICHMENTS_PER_RUN:
                print(f"\n[INFO] Reached MAX_ENRICHMENTS_PER_RUN limit ({MAX_ENRICHMENTS_PER_RUN}). Stopping API enrichment.")
                break

            place_id = str(row["place_id"])

            # If already cached and enriched, skip making a new API call
            if place_id in cached_ids:
                enrichment_count += 1
                continue

            # Process non-Google IDs: attempt lookup once, then cache the result permanently
            if place_id.startswith("osm_") or place_id.startswith("mun_") or place_id.startswith("social_"):
                record = row.to_dict()
                
                # Only attempt lookup if it hasn't already been processed/cached as checked
                if not record.get("enriched"):
                    matched_place = find_place_id_by_location(
                        row.get('name'), 
                        row.get('address'), 
                        row.get('latitude'), 
                        row.get('longitude'), 
                        row.get('zip_code')
                    )
                    api_calls_made += 1
                    
                    if matched_place:
                        record["place_id"] = matched_place.get("id")
                        record["phone"] = matched_place.get("nationalPhoneNumber")
                        record["website"] = matched_place.get("websiteUri")
                        record["opening_hours"] = json.dumps(matched_place.get("regularOpeningHours"))
                        print(f"    [UPGRADED] Matched '{row.get('name')}' to Google ID: {matched_place.get('id')}")
                    else:
                        print(f"    [NO GOOGLE MATCH] Keeping original ID for: {row.get('name')}")
                
                # Mark as enriched so it skips searching for this record on all future runs
                record["enriched"] = True
                enriched_records.append(record)
                enrichment_count += 1
                continue

            # Fetch details for Google Places IDs
            details = fetch_google_place_details(place_id)
            api_calls_made += 1

            record = row.to_dict()
            if details:
                record["phone"] = details.get("nationalPhoneNumber")
                record["website"] = details.get("websiteUri")
                record["opening_hours"] = json.dumps(details.get("regularOpeningHours"))
                record["enriched"] = True
            else:
                record["enriched"] = False

            enriched_records.append(record)
            enrichment_count += 1

        print(f"\n[3/3] Packaging structured records. API Calls: {api_calls_made}")
        df_final = pd.DataFrame(enriched_records)

        has_changes = api_calls_made > 0 or len(df_existing) != len(df_final)
        
        if not df_final.empty:
            df_final["place_id"] = df_final["place_id"].astype(str)
            df_final = df_final.drop_duplicates(subset=["place_id"], keep="first")

            # Save locally as CSV
            df_final.to_csv(LOCAL_ENRICHED, index=False)
            records = df_final.replace({np.nan: None}).to_dict(orient="records")

            # Upload NDJSON to GCS staging
            if has_changes or not os.path.exists(LOCAL_ENRICHED_JSON):
                utils.upload_to_gcs(LOCAL_ENRICHED_JSON, f"raw/venues/{config.CLOUD_MASTER}")
                load_all_tables_to_bigquery()
            else:
                print("  [INFO] No new venue changes detected. Skipping GCS upload and BigQuery sync.")
        else:
            print("  [WARN] No enriched records to package.") 

def load_all_tables_to_bigquery():
    if not os.path.exists(config.CREDENTIALS_FILE):
        print(f"[ERROR] Credentials file '{config.CREDENTIALS_FILE}' not found.")
        return

    try:
        credentials = service_account.Credentials.from_service_account_file(config.CREDENTIALS_FILE)
        client = bigquery.Client(credentials=credentials, project=config.PROJECT_ID)

        print("\n[BigQuery] Starting smart batch upload...")
        for file_key, config_map in BQ_TABLE_MAPPINGS.items():
            table_name = config_map['table_name']
            table_ref = f"{config.PROJECT_ID}.{config.DATASET_ID}.{table_name}"
            path_val = config_map['gcs_path']
            
            if file_key == "dim_datetime":
                states = utils.load_states()
                states["date"]["last_run"] = datetime.now().isoformat()
                utils.save_states(states)
                if states.get("date", {}).get("last_run"):
                    print(f"[INFO] '{table_name}' already exists. Skipping upload.")
                    continue

            if path_val.endswith('/'):
                gcs_uri = f"gs://{config.BUCKET_NAME}/{path_val}*.json"
            else:
                gcs_uri = f"gs://{config.BUCKET_NAME}/{path_val}"

            if config_map["format"] == bigquery.SourceFormat.CSV:
                job_config = bigquery.LoadJobConfig(
                    source_format=config_map["format"],
                    autodetect=True,
                    write_disposition=config_map["write_disposition"],
                    ignore_unknown_values=True,
                    skip_leading_rows=1
                )
            else:
                job_config = bigquery.LoadJobConfig(
                    source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                    autodetect=True,
                    write_disposition=config_map["write_disposition"]
                )

            try:
                print(f"  Loading {gcs_uri} -> {table_ref} ({config_map['write_disposition']})...")
                load_job = client.load_table_from_uri(gcs_uri, table_ref, job_config=job_config)
                load_job.result()

                table = client.get_table(table_ref)
                print(f"    [SUCCESS] Table '{table_name}' now contains {table.num_rows} total rows.")
                
            except Exception as table_err:
                print(f"    [WARN] Skipped '{table_name}' (Source files may not exist in GCS yet): {table_err}")

        print("[BigQuery] Batch upload process finished safely without data loss.")

    except Exception as e:
        print(f"[ERROR] BigQuery client initialization failed: {e}")
        
if __name__ == "__main__":    
    enrich_pipeline()