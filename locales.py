import os
import config
import io
import zipfile
import pandas as pd
import requests

OUTPUT_FILE = "locales.csv"

def generate_national_locales():
    # 1. Fetch ZIP mappings from GeoNames
    print("[1/3] Fetching US ZIP code geographic database...")
    geo_url = "http://download.geonames.org/export/zip/US.zip"
    try:
        response = requests.get(geo_url, timeout=30)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            df_geo = pd.read_csv(z.open('US.txt'), sep='\t', header=None, dtype={1: str, 5: str})
        
        df_geo.columns = ['country_code', 'zip_code', 'city', 'state_name', 'state_code',
                          'county', 'county_code', 'admin_name3', 'admin_code3',
                          'latitude', 'longitude', 'accuracy']
        df_geo = df_geo[['zip_code', 'city', 'county']]
        print(f"Loaded {len(df_geo)} geographic coordinates.")
    except Exception as e:
        print(f"[ERROR] Failed to fetch geography data: {e}")
        return

    # 2. Fetch Populations from Census API
    print("[2/3] Fetching national ZIP code population metrics...")
    
    # Use the 'zcta' alias if the long form fails, or ensure encoding
    # Use 'zip+code+tabulation+area:*' 
    census_url = "https://api.census.gov/data/2021/acs/acs5"
    params = {
        "get": "NAME,B01003_001E",
        "for": "zip code tabulation area:*",
        "key": config.CENSUS_API_KEY
    }
    
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        response = requests.get(census_url, params=params, headers=headers, timeout=30)
        
        # DEBUG: This will show you the exact error if it's not JSON
        if response.status_code != 200:
            print(f"[ERROR] Server returned status {response.status_code}")
            print(f"Response Body: {response.text}")
            return
            
        census_data = response.json() 
        headers_row = census_data[0]
        rows = census_data[1:]
        df_census = pd.DataFrame(rows, columns=headers_row)
        
        df_census.rename(columns={'B01003_001E': 'population', 'zip code tabulation area': 'zip_code'}, inplace=True)
        df_census['zip_code'] = df_census['zip_code'].astype(str).str.zfill(5)
        df_census['population'] = pd.to_numeric(df_census['population'], errors='coerce').fillna(0).astype(int)
        print(f"Retrieved population metrics for {len(df_census)} regions.")
    except Exception as e:
        print(f"[ERROR] Failed to fetch demographic metrics: {e}")
        return

    # 3. Merge and Export
    print("[3/3] Merging and sorting...")
    df_merged = pd.merge(df_geo, df_census, on='zip_code', how='inner')
    df_final = df_merged[['county', 'city', 'zip_code', 'population']].sort_values(by='population', ascending=False).dropna()
    df_final.to_csv(OUTPUT_FILE, index=False)
    print(f"[SUCCESS] Wrote {len(df_final)} records to '{OUTPUT_FILE}'!")

if __name__ == "__main__":
    generate_national_locales()