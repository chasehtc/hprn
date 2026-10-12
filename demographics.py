import os
import config
import utils
import json
import requests
import pandas as pd
from google.cloud import storage
from google.oauth2 import service_account

# --- CONFIGURATION ---
OUTPUT_DIR = "clean"
LOCAL_DEMO_CSV = "clean/demographics.csv"

# US Census Bureau ACS 5-Year Endpoint (2022 ACS 5-Year Data)
CENSUS_API_URL = "https://api.census.gov/data/2022/acs/acs5"

# Standard query works without a key for low volume, but key is recommended for batching.
CENSUS_API_KEY = config.CENSUS_API_KEY

# Census Variables Dictionary
# B01001: Sex by Age
# B19013: Median Household Income
CENSUS_VARS = [
    "NAME",
    "B01001_001E",  # Total Population
    
    # Male 15-34 Age Cohorts
    "B01001_006E","B01001_007E","B01001_008E","B01001_009E","B01001_010E","B01001_011E", "B01001_012E",  # Male: 15 to 34 years
    
    # Female 15-34 Age Cohorts
    "B01001_030E","B01001_031E","B01001_032E","B01001_033E","B01001_034E", "B01001_035E","B01001_036E",  # Female: 15 to 34 years
    
    # Income
    "B19013_001E"   # Median Household Income
]

def get_income_tier(income):
    """Classifies median income into analytical tiers."""
    if pd.isna(income) or income <= 0:
        return "Unknown"
    elif income < 45000:
        return "Low"
    elif income < 75000:
        return "Lower-Middle"
    elif income < 110000:
        return "Upper-Middle"
    else:
        return "High"

def fetch_census_demographics(zip_codes):
    """Fetches demographic metrics from US Census API for a list of ZIP codes (ZCTAs)."""
    print(f"[1/3] Querying U.S. Census Bureau ACS API for {len(zip_codes)} ZIP codes...")
    
    var_string = ",".join(CENSUS_VARS)
    records = []
    
    # Process ZIP codes in chunks of 50 to avoid URL length issues
    chunk_size = 50
    zip_chunks = [zip_codes[i:i + chunk_size] for i in range(0, len(zip_codes), chunk_size)]
    
    for chunk in zip_chunks:
        zcta_filter = ",".join(chunk)
        params = {
            "get": var_string,
            "for": f"zip code tabulation area:{zcta_filter}"
        }
        if CENSUS_API_KEY:
            params["key"] = CENSUS_API_KEY
            
        try:
            response = requests.get(CENSUS_API_URL, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            # Census returns header row as data[0]
            headers = data[0]
            for row in data[1:]:
                row_dict = dict(zip(headers, row))
                records.append(row_dict)
                
        except Exception as e:
            print(f"  [ERROR] Census API fetch failed for chunk: {e}")

    return pd.DataFrame(records)

def transform_demographics(raw_df):
    """Parses raw census variable keys into clean, structured demographic fields."""
    print("[2/3] Processing demographic variables and income tiers...")
    if raw_df.empty:
        return pd.DataFrame()
        
    df = pd.DataFrame()
    
    # Extract ZIP Code (ZCTA)
    df["zip_code"] = raw_df["zip code tabulation area"].astype(str).str.zfill(5)
    
    # Convert numeric fields (handling Census negative error codes like -666666666)
    for col in CENSUS_VARS:
        if col in raw_df.columns and col != "NAME":
            raw_df[col] = pd.to_numeric(raw_df[col], errors="coerce")
            raw_df[col] = raw_df[col].apply(lambda x: x if x >= 0 else None)
            
    # Total Population
    df["total_population"] = raw_df["B01001_001E"]
    
    # Target Male Cohort (Ages 15-34)
    male_cols = [f"B01001_{str(i).zfill(3)}E" for i in range(6, 13)]
    df["male_15_34"] = raw_df[male_cols].sum(axis=1)
    
    # Target Female Cohort (Ages 15-34)
    female_cols = [f"B01001_{str(i).zfill(3)}E" for i in range(30, 37)]
    df["female_15_34"] = raw_df[female_cols].sum(axis=1)
    
    # Total Target Basketball Cohort (Ages 15-34)
    df["target_demo_15_34"] = df["male_15_34"] + df["female_15_34"]
    
    # Percentage of total population in target demographic cohort
    df["pct_target_demo"] = (df["target_demo_15_34"] / df["total_population"]).round(4)
    
    # Median Household Income & Tier
    df["median_household_income"] = raw_df["B19013_001E"]
    df["median_income_tier"] = df["median_household_income"].apply(get_income_tier)
    
    return df

def run_demographics_pipeline():
    if hasattr(utils, "can_execute"):
        if not utils.can_execute("demographics"):
            print("[SKIP] Demographics interval not met. Skipping API fetch.")
            return
        
    if not os.path.exists(config.INPUT_CSV):
        print(f"[ERROR] Could not find {config.INPUT_CSV}.")
        return

    # Read target ZIP codes
    df_locales = pd.read_csv(config.INPUT_CSV, dtype={'zip_code': str})
    zip_codes = df_locales['zip_code'].astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(5).unique().tolist()

    # 1. Fetch raw data from Census API
    raw_df = fetch_census_demographics(zip_codes)
    
    # 2. Transform into clean dimension format
    clean_df = transform_demographics(raw_df)

    if not clean_df.empty:
        # 3. Save locally for inspection
        clean_df.to_csv(LOCAL_DEMO_CSV, index=False)
        print(f"[3/3] Saved clean demographics ({len(clean_df)} ZIP records) to '{LOCAL_DEMO_CSV}'")
        
        # 4. Upload to GCS staging
        utils.upload_to_gcs(LOCAL_DEMO_CSV, "staged/demographics.csv")
        print("\n[COMPLETE] Demographics pipeline execution finished successfully.")
    else:
        print("[ERROR] No demographic records were generated.")

if __name__ == "__main__":
    if hasattr(utils, "can_execute"):
        if not utils.can_execute("demographics"):
            print("[SKIP] Demographics interval (monthly) not met. Exiting.")
            exit()
            
    run_demographics_pipeline()