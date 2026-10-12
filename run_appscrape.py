import os
import json
import hashlib
import re
import requests
import utils
import config
import pandas as pd
from datetime import datetime, timezone
from apify_client import ApifyClient
from google.cloud import storage
from google.oauth2 import service_account

# --- CONFIGURATION & FALLBACKS ---
TRAFFIC_ACTOR_ID = "compass/crawler-google-places"

# --- DIRECTORY CONFIGURATION ---
LOCAL_TRAFFIC_DIR = os.path.join(config.RAW_DIR, "traffic")
MASTER_VENUES_CSV = os.path.join(config.CLEAN_DIR, "master_venues.csv")

JSON_LOCAL_NAME = "traffic_latest.json"
JSON_LOCAL_PATH = os.path.join(LOCAL_TRAFFIC_DIR, JSON_LOCAL_NAME)

# --- REGEX HEURISTICS & SPAM/NEGATIVE FILTERS ---
FLAGS = re.IGNORECASE

ORGANIZER_PATTERN = re.compile(r"gym\s+open|come\s+hoop|open\s+run|hosting|got\s+court|need\s+[0-9]+|run\s+at|open\s+gym\s+(tonight|today)|calling\s+all\s+hoopers", FLAGS)
SEEKER_PATTERN = re.compile(r"where\s+to\s+play|lookin[g]\s+for\s+a\s+run|anyone\s+hoopin[g]?|who\s+hoopin[g]?|open\s+gym|which\s+ymca|to\s+hoop|hoop\s*run|looking\s+for|need|who\s+runs|any\s+5s|where\s+to\s+hoop", FLAGS)
URGENT_PATTERN = re.compile(r"right\s+now|tonight|asap|next\s+hour|today|this\s+evening|tomorrow|urgently|rn", FLAGS)
SPAM_PATTERN = re.compile(r"workout\s+plan|sale|discount|subscribe|personal\s+trainer|buy\s+ticket|nba|highlights|tickets|fantasy|betting|stream|2k", FLAGS)
GYM_PATTERN = re.compile(r"gym|center|hoop|fieldhouse|sports\s+complex|arena|ymca|fitness|rec\s+center|indoor\s+court|full\s+court|church\s+gym", FLAGS)

# --- 1. SOCIAL PROSPECTING PIPELINE ---
def generate_signal_id(source, author, text):
    """Generates a deterministic SHA-256 hash for deduplication across ingestion runs."""
    unique_string = f"{source}:{author}:{text}".lower().strip()
    return hashlib.sha256(unique_string.encode("utf-8")).hexdigest()[:16]    

def generate_hashtag_urls(keywords):
    """Dynamically generates Instagram hashtag explore URLs from a list of keywords."""
    urls = []
    for kw in keywords:
        slug = re.sub(r'[^a-zA-Z0-9]', '', kw.lower())
        if slug:
            urls.append(f"https://www.instagram.com/explore/tags/{slug}/")
    return urls

def classify_intent_and_urgency(text):
    """Classifies post intent and computes a weighted urgency score based on basketball relevance."""
    if not text:
        return None, 0            
    
    if SPAM_PATTERN.search(text):
        return None, 0                
    
    text_lower = text.lower()
    
    # Filter out non-basketball noise if desired, or weight them lower
    if any(kw in text_lower for kw in utils.NEGATIVE_KEYWORDS):
        return None, 0
    
    # Determine Intent Type
    if ORGANIZER_PATTERN.search(text):
        intent_type = "organizer"
    elif SEEKER_PATTERN.search(text):
        intent_type = "seeker"
    else:
        intent_type = "general_discussion"    

    # Base Urgency Score
    urgency_score = 1

    # Time Sensitivity Boost
    if URGENT_PATTERN.search(text):
        urgency_score += 2
    elif re.search(r"this\s+weekend|tomorrow", text, FLAGS):
        urgency_score += 1

    # Basketball Relevance Boost (Checks for explicit keywords or hashtags)
    has_bball_keyword = any(kw in text_lower for kw in utils.POSITIVE_KEYWORDS[14:])
  
    if has_bball_keyword:
        urgency_score += 3  # Significant boost for confirmed basketball relevance

    urgency_score = min(urgency_score, 5)

    return intent_type, urgency_score

def parse_and_enrich_posts(posts_data):
    """Parses raw posts into social growth records and discovered venue records."""
    social_facts = []
    discovered_venues = []    
    for post in posts_data:
        text = post.get("text", "")
        intent_type, urgency_score = classify_intent_and_urgency(text)    
        if intent_type is None:
            continue    
        signal_id = generate_signal_id(post.get("source"), post.get("author"), text)
        fact_record = {
            "signal_id": signal_id,
            "source": post.get("source"),
            "platform": post.get("platform"),
            "timestamp": post.get("timestamp"),
            "author": post.get("author"),
            "text": text,
            "intent_type": intent_type,
            "urgency_score": urgency_score,
            "likes": post.get("likes", 0),
            "comments": post.get("comments", 0),
            "url": post.get("url"),
            "location_name": post.get("location_name"),
            "zip_code": post.get("zip_code"), 
            "ingested_at": datetime.now(timezone.utc).isoformat()
        }
        social_facts.append(fact_record)    
        if GYM_PATTERN.search(text):
            location_name = post.get("location_name")
            if location_name:
                discovered_venues.append({
                    "place_id": f"social_{post.get('source')}_{signal_id}",
                    "name": location_name,
                    "address": post.get("location_address", ""),
                    "zip_code": post.get("zip_code", ""),
                    "source": f"social_{post.get('source')}"
                })    
    return social_facts, discovered_venues    

def scrape_instagram_demand():
    print("  [INSTAGRAM] Launching broad regional social prospecting...")
    posts = []
    if config.APIFY_TOKEN == "YOUR_APIFY_API_TOKEN" or not config.APIFY_TOKEN:
        print("  [WARNING] Apify token not configured.")
        return posts  

    client = ApifyClient(config.APIFY_TOKEN)  

    # Define your core search topics here
    search_keywords = [
        "pickup basketball", 
        "come hoop",
        "open gym", 
        "hoop run"
    ]

    try:
        run_input = {
            "search": ", ".join(search_keywords),
            "directUrls": generate_hashtag_urls(search_keywords),
            "maxItems": 50
        }                        
        
        run = client.actor(config.APIFY_IG_ACTOR).call(run_input=run_input)
        dataset_id = getattr(run, "default_dataset_id", None)    
        
        if dataset_id:
            items = list(client.dataset(dataset_id).iterate_items())
            for item in items:
                loc_name = item.get("locationName") or item.get("place", {}).get("name") or None
                loc_id = item.get("locationId") or item.get("place", {}).get("id") or None
                loc_address = item.get("locationAddress") or item.get("place", {}).get("address") or None
                lat = item.get("lat") or item.get("place", {}).get("latitude") or None
                lng = item.get("lng") or item.get("place", {}).get("longitude") or None
               
                posts.append({
                    "post_id": item.get("id") or item.get("url"),
                    "source": "instagram",
                    "platform": "instagram",
                    "timestamp": item.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                    "author": item.get("ownerUsername") or item.get("author"),
                    "text": item.get("caption", ""),
                    "likes": item.get("likesCount", 0),
                    "comments": item.get("commentsCount", 0),
                    "url": item.get("url"),
                    "location_name": loc_name,
                    "location_address": loc_address,
                    "has_explicit_location": bool(loc_name or loc_address),
                    "ingested_at": datetime.now(timezone.utc).isoformat()
                })
            print(f"  [SUCCESS] Extracted {len(items)} posts using auto-generated URLs and keywords.")
    except Exception as e:
        print(f"  [INSTAGRAM ERROR] Scraping failed: {e}")  

    return posts   

def run_social_ingestion():
    print("\n--- Running Social Media Ingestion ---")
    if utils and hasattr(utils, "can_execute"):
        if not utils.can_execute("social"):
            print("[SKIP] Social ingestion interval not met. Skipping task.")
            return

    all_raw_posts = scrape_instagram_demand()
    social_facts, discovered_venues = parse_and_enrich_posts(all_raw_posts)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if social_facts:
        social_file = os.path.join(config.RAW_DIR, f"social_signals_{timestamp}.json")
        utils.upload_to_gcs(social_file, f"raw/social/social_signals_{timestamp}.json")

    if discovered_venues:
        venues_file = os.path.join(config.RAW_DIR, f"venues_social_{timestamp}.json")
        utils.upload_to_gcs(venues_file, f"raw/venues/venues_social_{timestamp}.json")
# --- 2. FOOT-TRAFFIC INGESTION PIPELINE ---
def get_venue_place_ids():
    """Reads master_venues.csv and extracts valid Google Place IDs."""
    print(f"Reading verified venues from {MASTER_VENUES_CSV}...")
    if not os.path.exists(MASTER_VENUES_CSV):
        print(f"  [ERROR] Could not find {MASTER_VENUES_CSV}! Please run merge_venues.py first.")
        return []

    try:
        df = pd.read_csv(MASTER_VENUES_CSV)
        if "place_id" not in df.columns:
            print("  [ERROR] 'place_id' column is missing from master list!")
            return []

        valid_venues = df[df["place_id"].notna() & (df["place_id"] != "N/A")]
        venues_list = valid_venues[["name", "place_id"]].to_dict("records")
        print(f"  [INFO] Found {len(venues_list)} venues eligible for traffic scraping.")
        return venues_list
    except Exception as e:
        print(f"  [ERROR] Failed to read master venues: {e}")
        return []

def scrape_popular_times(venues):
    """Triggers Apify's Google Maps Scraper using startUrls for direct lookup."""
    print(f"Initializing Apify Client to scrape traffic for {len(venues)} facilities...")
    client = ApifyClient(config.APIFY_TOKEN)

    start_urls = [
        {"url": f"https://www.google.com/maps/place/?q=place_id:{v['place_id']}"}
        for v in venues
    ]

    run_input = {
        "startUrls": start_urls,
        "includeHistogram": True,
        "scrapeReviewsPersonalData": False,
        "includeAdditionalInfo": False, # <-- Removes detailed attributes, reviews, and extra text info
        "maxReviews": 0,               # <-- Disables reviews completely
        "language": "en",
        "skipClosedPlaces": False
    }

    try:
        run = client.actor(TRAFFIC_ACTOR_ID).call(run_input=run_input)
        dataset_id = getattr(run, "default_dataset_id", None)
        if not dataset_id:
            print(f"  [ERROR] Could not extract dataset ID. Attributes: {dir(run)}")
            return []

        print(f"  [TRAFFIC SUCCESS] Fetching dataset items from {dataset_id}...")
        results = list(client.dataset(dataset_id).iterate_items())
        return results
    except Exception as e:
        print(f"  [ERROR] Apify traffic scraping process failed: {e}")
        return []

def sanitize_keys(item):
    """Recursively replaces invalid BigQuery column characters in keys with underscores."""
    if isinstance(item, dict):
        new_dict = {}
        for k, v in item.items():
            # Replace spaces and punctuation with underscores, lowercase them
            clean_key = re.sub(r'[^a-zA-Z0-9_]', '_', k).strip('_')
            # Ensure it doesn't start with a number
            if clean_key and clean_key[0].isdigit():
                clean_key = f"col_{clean_key}"
            new_dict[clean_key] = sanitize_keys(v)
        return new_dict
    elif isinstance(item, list):
        return [sanitize_keys(x) for x in item]
    return item

def run_traffic_ingestion():
    """Executes the Foot-Traffic Ingestion Pipeline."""
    print("\n--- Running Foot-Traffic Ingestion ---")
    if utils and hasattr(utils, "can_execute"):
        if not utils.can_execute("traffic"):
            print("[SKIP] Foot traffic ingestion interval not met. Skipping task.")
            return

    target_venues = get_venue_place_ids()

    if target_venues:
        raw_traffic_payload = scrape_popular_times(target_venues)
        if raw_traffic_payload:
            sanitized_payload = [sanitize_keys(item) for item in raw_traffic_payload]
            utils.upload_to_gcs(JSON_LOCAL_PATH, f"raw/traffic/traffic_latest_{timestamp}.json")
            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        else:
            print("  [ERROR] Traffic ingestion aborted due to extraction failure.")

# --- MAIN ORCHESTRATOR ---
def run_all_ingestions():
    """Main entry point to execute both Social and Foot-Traffic pipelines."""
    print("=== Launching HoopRun Social & Foot-Traffic Ingestion Pipeline ===")
    if config.APIFY_TOKEN == "YOUR_APIFY_API_TOKEN":
        print("[WARNING] Please update config.APIFY_TOKEN in config.py before running.")
        return

    # 1. Run Social Signal Ingestion
    run_social_ingestion()

    # 2. Run Foot-Traffic Popular Times Ingestion
    run_traffic_ingestion()

    print("\n=== All Ingestion Jobs Complete! ===")

if __name__ == "__main__":
    run_all_ingestions()