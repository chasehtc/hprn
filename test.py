import os
import pandas as pd
import config
from google.cloud import bigquery
from google.oauth2 import service_account

doc = os.path.join(config.BASE_DIR, "nces", "venues_48219_nces_20260909_175635.csv")
new_doc = os.path.join(config.BASE_DIR, "nces", "venues_nces_fixed.csv")

credentials = service_account.Credentials.from_service_account_file(config.CREDENTIALS_FILE)
client = bigquery.Client(credentials=credentials, project=config.PROJECT_ID)

# 1. Load CSV into pandas DataFrame
df = pd.read_csv(doc, dtype=str)

# 2. Push DataFrame to a temporary BigQuery table for bulk processing
table_id = f"{config.PROJECT_ID}.{config.DATASET_ID}.temp_nces_venues"
job_config = bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE, autodetect=True)
job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
job.result()

# 3. Execute a single bulk spatial join query across all rows instantly
query = f"""
SELECT 
    v.* EXCEPT(zip_code),
    IFNULL(z.zip_code, v.zip_code) AS zip_code
FROM 
    `{table_id}` v
LEFT JOIN 
    `bigquery-public-data.geo_us_boundaries.zip_codes` z
ON 
    ST_INTERSECTS(z.zip_code_geom, ST_GEOGPOINT(SAFE_CAST(v.longitude AS FLOAT64), SAFE_CAST(v.latitude AS FLOAT64)))
"""

df_fixed = client.query(query).to_dataframe()

# 4. Save corrected results to new CSV
df_fixed.to_csv(new_doc, index=False)
print("Bulk spatial ZIP correction complete. Saved to:", new_doc)