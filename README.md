# Hoop Run Data Pipeline 🏀

An automated, cost-optimized market intelligence and ELT data pipeline designed to quantify unmet recreational basketball court demand across metropolitan areas.

### Key Capabilities - Indoor Court Demand Forecasting/Ingestion
- **Multi-Source Supply Discovery**: Aggregates athletic venues across commercial directories, municipal open data portals, public/private school databases, and geospatial mapping platforms.
- **Cost-Controlled API Architecture**: Utilizes free open-source discovery endpoints and tight field masking on Google Places API endpoints to remain strictly within free-tier limits.
- **Global Geodetic Deduplication**: Merges overlapping entities using token-based text similarity combined with Haversine distance spatial proximity checks.
- **Analytical Data Warehouse**: Stages raw JSON/CSV data in Google Cloud Storage (GCS) and builds analytical models in Google BigQuery.


## Table of Contents
- [Executive Summary/Project Intent](#executive-summaryproject-intent)
- [End-to-End System Architecture](#end-to-end-system-architecture)
- [Data Warehouse Architecture (Star Schema)](#data-warehouse-architecture-star-schema)
- [Analytical Model: Unmet Demand Index (UDI)](#analytical-model-unmet-demand-index-udi)
- [Scope & Roadmap: Completed vs. Deferred Enhancements](#scope--roadmap-completed-vs-deferred-enhancements)
- [Orchestration & Automation](#orchestration--automation)
- [Key Data Engineering Highlights for Employers](#key-data-engineering-highlights)

---
## Executive Summary/Project Intent
- **Problem Statement:** Recreational sports platforms face a cold-start problem—while recreational sports facilities often suffer from spatial dead zones, unequal court distribution, and unquantified local demand.
- **Solution:** An automated ELT pipeline that ingests foot-traffic distributions, municipal demographics, and unstructured social media intent to identify "dead-zones" (hours with gym utilization under 20% combined with high surrounding player demand).
- **Business Impact:** Demonstrates production-grade data engineering, dimensional warehouse modeling, Natural Language Processing entity extraction, and business intelligence, delivering actionable data to drive facility acquisitions for hooprun.

---

## End-to-End System Architecture
- **Multi-Source Ingestion Layer:** Combines Google Places API (Basic Text SKU), US Census ACS API, Municipal ArcGIS Hub & OpenStreetMap (OSM) Overpass APIs, NCES School Directory API, and Apify (Instagram/Facebook/Google Maps foot-traffic scrapers).
- **Staging & Storage:** Raw JSON and CSV payloads landed in Google Cloud Storage (GCS) as an immutable audit trail.
- **Cloud Data Warehouse:** Google BigQuery hosting raw staging tables, SQL-transformed star-schema dimensional models, and analytical views.
- **NLP Entity Extraction Engine:** Python (spaCy, NLTK, RegEx) extracting basketball intent, dates, time windows, and venue names from unstructured social chatter.
- **Presentation & BI:** Streamlit geospatial maps, hourly heatmaps, and automated facility pitch report generator for the hooprun team.

![End-to-End Architecture](<./resources/HPRN ELT Architecture.png>)
---
## Tech Stack
- **Programming & Processing:** Python, SQL, spaCy, NLTK, RegEx
- **Storage & Data Warehouse:** Google BigQuery, Google Cloud Storage (GCS)
- **APIs & Data Sources:** Google Places API, US Census ACS API, ArcGIS Hub, OpenStreetMap (OSM) Overpass API, NCES School Directory API, Apify (Instagram, Facebook, Google Maps)
- **Visualization & BI:** Streamlit, Power BI
- **Orchestration & CI/CD:** Python Orchestration (`extract.py`), GitHub Actions

## Data Warehouse Architecture (Star Schema)
- **Core Dimensional Models:**
  - `dim_venues`: Master venue dimension containing `place_id`, geographic coordinates, facility type, court count, and verification status.
  - `dim_demographics`: ZCTA/ZIP-level census attributes including total population, target age males (16–35), population density, and median income tiers.
  - `dim_date_time`: Temporal dimension for granular hourly and day-of-week analytics.
  - `dim_players`: User profile and intent registry.
- **Fact Tables & Historical Tracking:**
  - `fact_foot_traffic`: Hourly foot-traffic busyness indices (0–100 scale) and open-gym availability flags.
  - `fact_social_demand`: Structured social intent signals, urgency scores (1–5), engagement metrics, and location tags.
  - `fact_venue_snapshots` & `fact_venue_lifecycle`: Append-only daily snapshots for tracking facility additions, modifications, and closures over time.

![Data Warehouse Star Schema](<./resources/HPRN Schema.png>)

---

## Analytical Model: Unmet Demand Index (UDI)
- **Core Logic:** Quantifies localized court deficits by balancing surrounding target demographic density against real-time facility traffic.
- **Formula:** `UDI = Target_Demographic_Density * (1 - (Hourly_Foot_Traffic / 100))`
- **Application:** High UDI scores (80–100) highlight prime facility acquisition targets where gyms are underutilized in high-density player areas. Low scores (<25) represent saturated facilities or low-demand zones.

---

## Scope & Roadmap: Completed vs. Deferred Enhancements
- **Completed Deliverables:**
  - Fully automated, cost-controlled multi-source discovery scrapers (Google, Municipal, NCES, OSM) via [`run_ingest.py`](./run_ingest.py).
  - Deduplication engine integrating distance proximity and SequenceMatcher token similarity via [`merge_venues.py`](./merge_venues.py).
  - Foot-traffic curve scraper staging hourly popular times metrics into GCS via [`run_appscrape.py`](./run_appscrape.py) / [`scrape_traffic.py`](./scrape_traffic.py).
  - BigQuery star-schema warehouse setup with SQL-based UDI transformation views via [`run_pipeline.py`](./run_pipeline.py).
  - End-to-end Python orchestration script running extraction, staging, and transformation passes.
- **Deferred Scope & Future Enhancements:**
  - **Monetization:** Excluded from initial scope to prioritize physical supply-demand gap alignment and pipeline engineering.
  - **dbt Data Build Tool Integration:** Transformations currently execute via raw SQL scripts in BigQuery. Migration to dbt Core for automated testing, schema validation, and lineage documentation is planned as a Phase 2 enhancement.
  - **Commercial Tableau Layer:** Replaced with PowerBI dashboards during the pilot phase to reduce recurring commercial costs.

---

## Orchestration & Automation
- **Python Orchestration:** Centralized execution managed via [`run_pipeline.py`](./run_pipeline.py) and [`utils.py`](./utils.py) to enforce sequential execution across multi-source ingestion ([`run_ingest.py`](./run_ingest.py)), local staging, GCS bucket sync, and BigQuery loading passes.
- **Automated Workflows:** Scheduled batch runs designed to routinely update core dimensional models, refresh foot-traffic trends, and recalculate the Unmet Demand Index (UDI). Airflow Implementation set for Phase 2.
- **Version Control & CI/CD:** GitHub Actions integration for automated testing, schema checks, and continuous pipeline reliability.

![Orchestration & Automation Pipeline](<./resources/HPRN Pipeline Flowchart.png>)
---

## Key Data Engineering Highlights
- **Budget-Conscious Cloud Design:** Implemented strict field masking, API caching, and free/pay-as-you-go cloud tiers (GCS, BigQuery, Places API $200 credit) to maintain near-$0 operational overhead.
- **Resilient Data Quality & Deduplication:** Developed a multi-layer verification pipeline featuring negative keyword blocklists and token-similarity deduplication to merge disparate municipal, school, and commercial sources.
- **Full-Lifecycle Analytics Ownership:** Managed the complete lifecycle from raw API web scraping and NLP signal extraction to data modeling, warehouse optimization, and executive presentation.
