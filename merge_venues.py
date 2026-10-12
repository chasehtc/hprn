import os
import config
import utils
import glob
import json
import re
import pandas as pd
import numpy as np
from google.cloud import storage
from google.oauth2 import service_account
from difflib import SequenceMatcher

# --- CONFIGURATION ---
LOCAL_MASTER_JSON = "clean/master_venues.json"

GEO_PROXIMITY_METERS = 100
def nces_check():
    if os.path.exists("pipeline_state.json"):
        with open("pipeline_state.json", "r", encoding="utf-8") as f:
            state = json.load(f)
            last_run = state.get("nces", {}).get("last_run")
            if last_run and last_run != "null":
                return False
    return True

def extract_venue_records_from_json(data):
    """Extracts all venue records from Google Places, ArcGIS GeoJSON, OSM, or custom wrappers."""
    extracted = []
    
    # Normalize input data to always be a list of dictionaries to inspect
    if isinstance(data, dict):
        search_dicts = [data]
    elif isinstance(data, list):
        search_dicts = data
    else:
        return []
    
    for d in search_dicts:
        if not isinstance(d, dict):
            continue
            
        # 1. Custom discovery output ('records', 'items', 'data', 'results', 'candidates')
        for key in ['records', 'items', 'data', 'results', 'candidates']:
            if key in d and isinstance(d[key], list):
                extracted.extend(d[key])
                
        # 2. Google Places API New ('places')
        if "places" in d and isinstance(d["places"], list):
            for p in d["places"]:
                if not isinstance(p, dict):
                    continue
                loc = p.get("location", {}) or {}
                disp = p.get("displayName", {}) or {}
                extracted.append({
                    "place_id": p.get("id"),
                    "name": disp.get("text") if isinstance(disp, dict) else p.get("name"),
                    "address": p.get("formattedAddress"),
                    "latitude": loc.get("latitude"),
                    "longitude": loc.get("longitude"),
                    "source": "google"
                })
                
        # 3. GeoJSON format (ArcGIS / Municipal Open Data)
        if "features" in d and isinstance(d["features"], list):
            for f in d["features"]:
                if not isinstance(f, dict):
                    continue
                p = f.get("properties", {}) or {}
                geom = f.get("geometry", {}) or {}
                coords = geom.get("coordinates", [None, None]) # [lng, lat]
                extracted.append({
                    "place_id": f"mun_{p.get('OBJECTID') or p.get('id') or p.get('FID')}",
                    "name": p.get("Center_Nam") or p.get("name") or p.get("Name") or p.get("FACNAME"),
                    "address": p.get("Address") or p.get("address") or p.get("ADDR"),
                    "latitude": coords[1] if len(coords) > 1 and coords[1] is not None else None,
                    "longitude": coords[0] if len(coords) > 0 and coords[0] is not None else None,
                    "source": "municipal"
                })
                
        # 4. OpenStreetMap (OSM) Overpass API format ('elements')
        if "elements" in d and isinstance(d["elements"], list):
            for el in d["elements"]:
                if not isinstance(el, dict):
                    continue
                tags = el.get("tags", {}) or {}
                # Only pull nodes/ways that have a name
                if "name" in tags:
                    extracted.append({
                        "place_id": f"osm_{el.get('type')}_{el.get('id')}",
                        "name": tags.get("name"),
                        "address": tags.get("addr:full") or f"{tags.get('addr:housenumber', '')} {tags.get('addr:street', '')}".strip() or None,
                        "latitude": el.get("lat") or el.get("center", {}).get("lat"),
                        "longitude": el.get("lon") or el.get("center", {}).get("lon"),
                        "source": "osm"
                    })

        # 5. NCES School Directory format
        if nces_check() and ("nces" in d or "leaid" in d or "school_id" in d):
            records = d.get("records", [d]) if isinstance(d, dict) else d
            for sch in (records if isinstance(records, list) else [records]):
                if isinstance(sch, dict):
                    extracted.append({
                        "place_id": f"nces_{sch.get('ncessch') or sch.get('school_id') or sch.get('leaid')}",
                        "name": sch.get("school_name") or sch.get("name"),
                        "address": f"{sch.get('street', '')}, {sch.get('city', '')}, {sch.get('state', '')} {sch.get('zip', '')}".strip(', '),
                        "latitude": sch.get("latitude") or sch.get("lat"),
                        "longitude": sch.get("longitude") or sch.get("lon"),
                        "zip_code": sch.get("zip_code"),
                        "source": "nces"
                    })
        elif not nces_check() and ("nces" in d or "leaid" in d or "school_id" in d):
            print("[SKIP] NCES merge already executed; skipping NCES records.")
                    
    return extracted

def load_from_local_directory():
    """Scans raw directory, extracts venue lists, standardizes schema, and ignores non-venue JSONs."""
    print(f"[1/4] Scanning: {config.RAW_DIR
}")
    if not os.path.exists(config.RAW_DIR
):
        print(f"  [WARN] Raw data directory not found: {config.RAW_DIR
    }")
        return pd.DataFrame()
        
    all_files = glob.glob(os.path.join(config.RAW_DIR
, "*.*"))
    dfs = []
    
    for file in all_files:
        if "master_venues" in file or "traffic" in file.lower(): 
            continue
        try:
            if file.endswith(".csv"):
                df = pd.read_csv(file)
            elif file.endswith(".json"):
                with open(file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                venue_data = extract_venue_records_from_json(data)
                if not venue_data:
                    continue
                df = pd.DataFrame(venue_data)
            else:
                continue
                
            if df.empty:
                continue
                
            df.columns = [str(col).lower().strip() for col in df.columns]
            
            # Map column aliases
            col_renames = {
                "id": "place_id",
                "lat": "latitude",
                "lng": "longitude",
                "lon": "longitude",
                "long": "longitude",
                "formattedaddress": "address",
                "formatted_address": "address",
                "vicinity": "address"
            }
            df.rename(columns={k: v for k, v in col_renames.items() if k in df.columns and v not in df.columns}, inplace=True)
            
            keep_cols = ['name', 'address', 'latitude', 'longitude', 'place_id', 'zip_code', 'source']
            for col in keep_cols:
                if col not in df.columns:
                    df[col] = None
                    
            df = df[keep_cols]
            dfs.append(df)
            print(f"  Processed {os.path.basename(file)}: {len(df)} rows")
        except Exception as e:
            print(f"  [ERROR] Failed to process {os.path.basename(file)}: {e}")
            
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

def clean_and_normalize(df):
    print("[2/4] Cleaning and normalizing schemas & stripping blank/null records...")
    if df.empty:
        return df
        
    # 1. Clean basic strings
    df["name"] = df["name"].astype(str).str.strip()
    df["address"] = df["address"].astype(str).str.strip()
    df["place_id"] = df["place_id"].astype(str).str.strip()
    
    # Robust ZIP code cleaning 
    if 'zip_code' in df.columns:
        df['zip_code'] = df['zip_code'].fillna('').astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(5)
        df['zip_code'] = df['zip_code'].replace({'': None, 'nan': None, 'None': None, '00nan': None, '00000': None})
      
    # 2. Convert coordinates to numeric
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    
    # 3. Filter out completely invalid/blank records
    invalid_names = ["none", "nan", "null", "", "none/null", "unknown", "osm facility"]
    is_valid_name = ~df["name"].str.lower().isin(invalid_names) & ~df["name"].str.startswith("synth_")
    is_not_empty_name = df["name"].str.len() > 1
    has_coords = df["latitude"].notna() & df["longitude"].notna()

    # Final Negative Keyword Filter (Catches false positives that slipped past ingestion)
    name_lower = df["name"].str.lower()
    is_not_negative = ~name_lower.str.contains('|'.join(utils.NEGATIVE_KEYWORDS), case=False, na=False)
    is_not_standalone_park = ~name_lower.str.contains(r'\bpark\b(?!s)', case=False, na=False, regex=True)

    # Drop nameless/generic municipal records starting with 'mun_'
    is_not_nameless_municipal = ~(
        df['source'].str.contains('municipal', na=False) & 
        (df['name'].str.lower().str.startswith('mun') | df['name'].str.lower().isin(['municipal facility']))
    )
    
    clean_df = df[is_valid_name & is_not_empty_name & has_coords & is_not_nameless_municipal & is_not_negative & is_not_standalone_park].copy()
    
    dropped_blanks = len(df) - len(clean_df)
    if dropped_blanks > 0:
        print(f"  [CLEAN] Filtered out {dropped_blanks} blank/invalid/unlocatable records.")
        
    return clean_df

def haversine_distance(lat1, lon1, lat2, lon2):
    if pd.isna(lat1) or pd.isna(lon1) or pd.isna(lat2) or pd.isna(lon2):
        return np.inf
    R = 6371000.0
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    delta_phi = np.radians(lat2 - lat1)
    delta_lambda = np.radians(lon2 - lon1)
    a = np.sin(delta_phi / 2.0) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(delta_lambda / 2.0) ** 2
    return 2.0 * R * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))

def deduplicate_venues(df):
    """Performs ID and robust proximity/similarity-based deduplication."""
    print("[3/4] Deduplicating venues (Robust Strategy)...")
    if df.empty:
        return df
        
    initial_count = len(df)
    
    # 1. Deduplicate exact Place IDs (skip 'nan' or generic placeholders)
    df_valid_ids = df[~df["place_id"].isin(["nan", "None", "", "null"])].drop_duplicates(subset=["place_id"], keep="first")
    df_no_ids = df[df["place_id"].isin(["nan", "None", "", "null"])]
    df = pd.concat([df_valid_ids, df_no_ids], ignore_index=True)
    
    records = df.to_dict("records")
    final_records = []
    merged_indices = set()
    
    for i in range(len(records)):
        if i in merged_indices:
            continue
        r1 = records[i]
        
        for j in range(i + 1, len(records)):
            if j in merged_indices:
                continue
            r2 = records[j]
            
            dist = haversine_distance(r1["latitude"], r1["longitude"], r2["latitude"], r2["longitude"])
            if dist <= GEO_PROXIMITY_METERS:
                name_sim = SequenceMatcher(None, str(r1.get("name", "")).lower(), str(r2.get("name", "")).lower()).ratio()
                if name_sim >= 0.55:
                    print(f"  [MERGE] Merging duplicate:")
                    print(f"    Primary:   {r1['name']} ({r1['source']})")
                    print(f"    Duplicate: {r2['name']} ({r2['source']})")
                    print(f"    Similarity: {name_sim:.2f}, Proximity: {dist:.1f}m")
                    
                    r1["source"] = f"{r1['source']},{r2['source']}"
                    
                    if not r1.get("address") and r2.get("address"):
                        r1["address"] = r2["address"]
                        
                    merged_indices.add(j)
                    
        final_records.append(r1)
        
    deduped_df = pd.DataFrame(final_records)
    print(f"  [RESULT] Kept {len(deduped_df)} valid records (Removed {initial_count - len(deduped_df)} total duplicates/blanks).")
    return deduped_df

def run_merge_pipeline():
    print("=== Merging Hooprun Pipeline ===")
    master_df = pd.read_csv(config.LOCAL_MASTER) if os.path.exists(config.LOCAL_MASTER) else pd.DataFrame()
    raw_df = load_from_local_directory()
    
    if not raw_df.empty:
        clean_df = clean_and_normalize(raw_df)
        combined_df = pd.concat([master_df, clean_df], ignore_index=True) if not master_df.empty else clean_df
    else:
        combined_df = master_df
        
    final_df = deduplicate_venues(combined_df)
    
    # Save locally as CSV
    final_df.to_csv(config.LOCAL_MASTER, index=False)
    #final_df.replace({np.nan: None}).to_json(LOCAL_MASTER_JSON, orient="records", indent=4)
    #utils.upload_to_gcs MASTER currently not uploading to GCS only FINAL ENRICHED.
    #(LOCAL_MASTER_JSON, f"raw/venues/{LOCAL_MASTER_JSON}")
    
    print("\n[COMPLETE] Success!")

if __name__ == "__main__":
    run_merge_pipeline()