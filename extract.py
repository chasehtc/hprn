import run_ingest
import merge_venues
import run_appscrape
import run_pipeline

def run_orchestrator():
    print("Starting master pipeline execution...")
    
    # Execute each step in order
    run_ingest.run_unified_discovery()
    merge_venues.run_merge_pipeline()
    run_appscrape.run_all_ingestions()
    run_pipeline.enrich_pipeline()
    
    print("Master pipeline execution complete.")

if __name__ == "__main__":
    run_orchestrator()