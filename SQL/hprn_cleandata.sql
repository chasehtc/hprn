/* @datacloud.settings
{
  "version": 1,
  "service": "BIG_QUERY",
  "connectionInfo": {
    "billingProjectId": "INHERIT"
  },
  "dialect": "GOOGLE_SQL"
}
*/

CREATE OR REPLACE TABLE hprn_warehouse.dim_venues AS  
SELECT      
    v.place_id,      
    TRIM(v.name) AS name,      
    REGEXP_EXTRACT(v.address, r'^.*?\b[A-Z]{2}\b') AS address,      
    v.zip_code AS zip_code,        
    v.latitude as lat,      
    v.longitude as lng,      
    t.categoryName AS facility_type,         
    v.source,      
    v.enriched,      
    v.phone,      
    v.website  
FROM hprn_warehouse.raw_venues v
LEFT JOIN hprn_warehouse.raw_traffic t
    ON v.place_id = t.placeId
WHERE v.name IS NOT NULL AND TRIM(v.name) != '';


CREATE OR REPLACE TABLE hprn_warehouse.dim_demographics AS  
WITH raw_data AS (
    SELECT        
        zip_code,   
        total_population AS total_pop,        
        target_demo_15_34 AS target_age_pop,       
        pct_target_demo,  
        median_household_income,  
        median_income_tier  
    FROM hprn_warehouse.raw_demographics
    WHERE total_population != 0
),
min_max_calc AS (
    SELECT 
        MAX(target_age_pop) AS max_target,
        MIN(target_age_pop) AS min_target
    FROM raw_data
)
SELECT 
    r.zip_code,   
    r.total_pop,        
    r.target_age_pop,     
    r.pct_target_demo,  
    r.median_household_income,  
    r.median_income_tier,
    SAFE_DIVIDE(r.target_age_pop - m.min_target, m.max_target - m.min_target) AS target_pop_density,
FROM raw_data as r
CROSS JOIN min_max_calc as m;

CREATE OR REPLACE TABLE hprn_warehouse.fact_traffic AS  
WITH flattened_traffic AS (
  SELECT            
    FARM_FINGERPRINT(CONCAT(t.placeId, '_', CAST(t.scrapedAt AS STRING), '_', day_key, '_', CAST(JSON_VALUE(hour_item, '$.hour') AS STRING))) AS traffic_id,    
    t.placeId AS place_id,    
    t.scrapedAt AS time_stamp,        

    REGEXP_CONTAINS(LOWER(t.title), r'gym|rec|basketball|court|sports|fitness|ymca')         
        OR REGEXP_CONTAINS(LOWER(COALESCE(t.categoryName, '')), r'gym|sports|court') AS is_gym,        
    day_key AS day_of_week,    
    CAST(JSON_VALUE(hour_item, '$.hour') AS INT64) AS hour_of_day,    
    CAST(JSON_VALUE(hour_item, '$.occupancyPercent') AS INT64) AS busy_index,                  
    
    -- Calculated busy_delta (difference from the daily average busyness for that venue)
    CAST(JSON_VALUE(hour_item, '$.occupancyPercent') AS FLOAT64) -         
        AVG(CAST(JSON_VALUE(hour_item, '$.occupancyPercent') AS FLOAT64)) OVER(PARTITION BY t.placeId, t.scrapedAt, day_key) AS busy_delta,                  
    
    t.postalCode AS zip_code,    
    t.locatedIn,    
    t.reviewsCount AS total_reviews,
    
    -- Assigns a row number to exact duplicates sharing the same traffic_id
    ROW_NUMBER() OVER (
        PARTITION BY FARM_FINGERPRINT(CONCAT(t.placeId, '_', CAST(t.scrapedAt AS STRING), '_', day_key, '_', CAST(JSON_VALUE(hour_item, '$.hour') AS STRING))) 
        ORDER BY t.scrapedAt DESC
    ) AS row_num
  FROM hprn_warehouse.raw_traffic AS t
  CROSS JOIN UNNEST(JSON_KEYS(TO_JSON(t.popularTimesHistogram))) AS day_key
  CROSS JOIN UNNEST(JSON_QUERY_ARRAY(TO_JSON(t.popularTimesHistogram)[day_key])) AS hour_item
)
SELECT *
FROM flattened_traffic
WHERE row_num = 1;


CREATE OR REPLACE TABLE hprn_warehouse.fact_social AS  
 SELECT        
  signal_id,
  zip_code,
  timestamp AS posted,
  ingested_at AS ingested,
  platform AS source,
  urgency_score,
  likes + comments AS eng_count,

  (        -- 1. Keyword Relevance (0.4 max weight)
    CASE 
      WHEN REGEXP_CONTAINS(LOWER(text), r'basketball|hoop|pickup|court|run|5s') THEN 0.4
      ELSE 0.1 
    END
    + 
        -- 2. Location Metadata Present (0.3 max weight)
    CASE 
      WHEN location_name IS NOT NULL AND location_name != '' THEN 0.3
      ELSE 0.0 
    END 
    + 
        -- 3. Engagement Activity Present (0.3 max weight)
    CASE 
      WHEN (likes + comments) > 3 THEN 0.3
      else 0.0
    END
  ) AS confidence_score,
  author,
  intent_type
    
FROM 
  hprn_warehouse.raw_social;



