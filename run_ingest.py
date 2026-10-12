import os
import time
import json
import requests
import pandas as pd
from datetime import datetime
from google.cloud import storage
from google.oauth2 import service_account
import config
import utils

# --- CONFIGURATION ---
MAX_ZIP_CODES_TO_PROCESS = 1
RESULTS_PER_ZIP = 40

SEARCH_KEYWORDS = ["indoor basketball gym", "recreation center", "fieldhouse", "sports complex"]

def find_arcgis_dataset_id(city_name, state_code):
    search_url = "https://hub.arcgis.com/api/v3/search"
    params = {"q": f"{city_name} recreation OR {city_name} parks", "page[size]": 3}
    try:
        response = requests.get(search_url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and data.get("data") and len(data["data"]) > 0:
                print(f"  [DEBUG] Found ArcGIS dataset: {data['data'][0].get('attributes', {}).get('name')}")
                return data["data"][0].get("id")
    except Exception as e:
        print(f"  [DEBUG] ArcGIS Hub error: {e}")
    return None

def fetch_municipal_geojson(dataset_id):
    url = f"https://opendata.arcgis.com/datasets/{dataset_id}.geojson"
    try:
        response = requests.get(url, headers={"User-Agent": "HooprunDemandBot/1.0"}, timeout=15)
        response.raise_for_status()
        return response.json()
    except: return {}

def fetch_osm_data(city_name):
    overpass_url = "https://overpass-api.de/api/interpreter"
    headers = {"User-Agent": "HooprunDemandBot/1.0"}
    query = f"""
    [out:json][timeout:25];
    area["name"="{city_name}"]->.searchArea;
    (
      node["sport"="basketball"](area.searchArea);
      way["sport"="basketball"](area.searchArea);
      node["leisure"="pitch"]["sport"="basketball"](area.searchArea);
      way["leisure"="pitch"]["sport"="basketball"](area.searchArea);
      node["leisure"="sports_centre"](area.searchArea);
      way["leisure"="sports_centre"](area.searchArea);
      node["building"="gymnasium"](area.searchArea);
      way["building"="gymnasium"](area.searchArea);
    );
    out center;
    """
    try:
        response = requests.post(overpass_url, data={'data': query}, headers=headers, timeout=30)
        if response.status_code == 429:
            print("  [ERROR] Overpass rate limit (429) hit. Waiting...")
            time.sleep(60)
            return {}
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"  [ERROR] OSM Query failed: {e}")
        return {}

def process_and_save(raw_data, records, zip_code, source_type):
    if not records: return
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"venues_{zip_code}_{source_type}_{timestamp}"
    json_file = os.path.join(config.RAW_DIR, f"{base_name}.json")
            
    filtered = []
    dropped_count = 0
    for r in records:
        if isinstance(r, dict):
            name = str(r.get("name", "")).strip().lower()
            # Drop if it's a generic fallback or negative keyword
            if name in ["municipal facility", "none", "nan", ""] or any(kw in name for kw in utils.NEGATIVE_KEYWORDS):
                dropped_count += 1
            else:
                filtered.append(r)
                    
    output_payload = {
        "metadata": {
            "zip_code": zip_code,
            "source_type": source_type,
            "timestamp": timestamp,
            "total_kept": len(filtered),
            "total_dropped": dropped_count
        },
        "raw_audit_data": raw_data,
        "records": filtered
    }

    print(f"  [{source_type.upper()}] Saved: {json_file} ({len(filtered)} kept, {dropped_count} dropped)")
    utils.upload_to_gcs(json_file, f"raw/venues/{base_name}.json")

def run_unified_discovery():
    if not os.path.exists(config.INPUT_CSV):
        print(f"[ERROR] {config.INPUT_CSV} not found.")
        return
    if utils and hasattr(utils, "can_execute"):
        if not utils.can_execute("venues"):
            print("[SKIP] Venue discovery interval (monthly) not met. Skipping ingestion run.")
            return

    processed_zips = utils.load_processed_zips()
    df_locales = pd.read_csv(config.INPUT_CSV)

    zips_processed_count = 0   
    for _, row in df_locales.iterrows():
        if zips_processed_count >= MAX_ZIP_CODES_TO_PROCESS:
            print(f"\n[INFO] Reached MAX_ZIP_CODES_TO_PROCESS limit ({MAX_ZIP_CODES_TO_PROCESS}). Stopping ingestion.")
            break

        zip_code = str(row['zip_code']).zfill(5)
        
        # Check if all scrapers are already done for this ZIP
        zip_status = processed_zips.get(zip_code, {})
        if zip_status.get("google") and zip_status.get("osm") and zip_status.get("municipal"):
            print(f"Skipping ZIP: {zip_code} (All scrapers completed)")
            continue
                    
        print(f"\nProcessing ZIP: {zip_code}...")
        success = True
        zip_worked = False
                        
        try:
            # --- GOOGLE SCRAPER ---
            if not zip_status.get("google"):
                google_url = "https://places.googleapis.com/v1/places:searchText"
                google_headers = {
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": config.GOOGLE_PLACES_API_KEY,
                    "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.types,places.location"
                }
                
                all_google_results = []
                all_google_raw = []
                
                for keyword in SEARCH_KEYWORDS:
                    payload = {"textQuery": f"{keyword} in {zip_code}", "maxResultCount": RESULTS_PER_ZIP}
                    response = requests.post(google_url, json=payload, headers=google_headers, timeout=15)
                    response.raise_for_status()
                    data = response.json()
                    all_google_raw.append(data)

                    for place in data.get("places", []):
                        name = place.get("displayName", {}).get("text", "")
                        loc = place.get("location", {})
                        all_google_results.append({
                            "place_id": place.get("id"),
                            "name": name,
                            "address": place.get("formattedAddress", ""),
                            "zip_code": zip_code,
                            "latitude": loc.get("latitude"),
                            "longitude": loc.get("longitude"),
                            "source": "google"
                        })

                if all_google_results:
                    process_and_save(all_google_raw, all_google_results, zip_code, "google")
                
                utils.save_processed_zip(zip_code, "google", True)
                zip_worked = True

            time.sleep(2)
            city, state = utils.resolve_zip_to_city(zip_code)
            
            if city:
                # --- OSM SCRAPER ---
                if not zip_status.get("osm"):
                    osm_raw = fetch_osm_data(city)
                    if isinstance(osm_raw, dict) and osm_raw.get("elements"):
                        osm_records = []
                        seen = set()
                        for e in osm_raw.get("elements", []):
                            lat, lon = e.get("lat") or e.get("center", {}).get("lat"), e.get("lon") or e.get("center", {}).get("lon")
                            if lat and lon:
                                r_lat, r_lon = round(float(lat), 4), round(float(lon), 4)
                                if (r_lat, r_lon) not in seen:
                                    seen.add((r_lat, r_lon))
                                    t = e.get("tags", {})
                                    name = t.get("name") or t.get("official_name") or "OSM Facility"
                                    osm_records.append({
                                        "place_id": f"osm_{e.get('id')}", 
                                        "name": name, 
                                        "zip_code": zip_code,
                                        "latitude": r_lat,
                                        "longitude": r_lon,
                                        "source": "osm"
                                    })
                        process_and_save(osm_raw, osm_records, zip_code, "osm")
                    
                    utils.save_processed_zip(zip_code, "osm", True)
                    zip_worked = True
                        
                # --- MUNICIPAL SCRAPER ---
                if not zip_status.get("municipal"):
                    dataset_id = find_arcgis_dataset_id(city, state)
                    if dataset_id:
                        geojson = fetch_municipal_geojson(dataset_id)
                        if isinstance(geojson, dict) and geojson.get("features"):
                            mun_records = []
                            for f in geojson.get("features", []):
                                p = f.get("properties", {})
                                geom = f.get("geometry", {})
                                coords = geom.get("coordinates", [None, None])
                                if isinstance(p, dict):
                                    name = p.get("Center_Nam") or p.get("name") or p.get("Name") or "Municipal Facility"
                                    mun_records.append({
                                        "place_id": f"mun_{p.get('OBJECTID') or p.get('id')}", 
                                        "name": name, 
                                        "zip_code": zip_code,
                                        "latitude": coords[1] if len(coords) > 1 else None,
                                        "longitude": coords[0] if len(coords) > 0 else None,
                                        "source": "municipal"
                                    })
                            process_and_save(geojson, mun_records, zip_code, "municipal")
                    
                    utils.save_processed_zip(zip_code, "municipal", True)
                    zip_worked = True

        except Exception as e:
            print(f"  [ERROR] Failed processing for ZIP {zip_code}: {e}")
            success = False

        if zip_worked and success:
            zips_processed_count += 1
            print(f"  [SUCCESS] ZIP {zip_code} completed.")
        
if __name__ == "__main__":
    run_unified_discovery()