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
WITH look as (
SELECT 
  v.place_id, 
  v.name, 
  v.address,
  v.zip_code AS zip_code,
  d.total_pop,
  d.target_age_pop,
  d.target_pop_density,
  t.hour_of_day AS t_hour,
  t.day_of_week AS t_day,
  t.busy_index,
  SUM(IF(v.zip_code = t.zip_code, t.total_reviews, 0)) AS reviews,
  d.target_pop_density *(1-(busy_index)/100) AS UDI

FROM hprn_warehouse.dim_venues AS v
JOIN hprn_warehouse.dim_demographics AS d ON d.zip_code = v.zip_code
JOIN hprn_warehouse.fact_traffic AS t ON t.place_id = v.place_id
WHERE LOWER(v.name) NOT LIKE '%park%'
GROUP BY v.place_id, v.name, v.address, v.zip_code, d.total_pop, d.target_age_pop, d.target_pop_density, t.busy_index, t.total_reviews, UDI, t_hour, t_day
)

SELECT 
  busy_index,
  ROUND((UDI - MIN(UDI) OVER()) / (MAX(UDI) OVER() - MIN(UDI) OVER()) * 100) AS UDI, 
  t_hour,
  t_day,
  place_id,
  name,
  zip_code,
  target_pop_density
FROM look
ORDER BY place_id, t_day, t_hour
LIMIT 2000;