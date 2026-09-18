"""Coordinate the UFC prediction data pipeline from scraping through ingestion."""

import asyncio
import sys
import traceback
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SCRAPER_DIR = PROJECT_ROOT / "Scraper"
DATABASE_DIR = PROJECT_ROOT / "Database"



from Scraper.scrape_ufc_stats import extract_fight_stats_from_UFCstats
from Database.ingest_pipeline import ingest_events






def run_pipeline():
    """Run the full scraping and ingestion workflow for UFC event data."""
    print("===============================")
    print("Starting UFC Data Pipeline")
    print("===============================")
    print()

    print("Scraping UFCStats...")
    event_results = asyncio.run(extract_fight_stats_from_UFCstats())

    if not isinstance(event_results, dict):
        raise TypeError("Scraper did not return a dictionary of event results.")

    print("Scraping Complete.")
    print()

    print("Beginning Database Ingestion...")
    counters = ingest_events(event_results)
    print("Database Ingestion Complete.")
    print()

    print("===============================")
    print("Pipeline Summary")
    print("===============================")
    print()
    print(f"Events Processed: {counters.get('events', 0)}")
    print(f"Fights Inserted: {counters.get('fights', 0)}")
    print(f"Fighters Inserted: {counters.get('fighters', 0)}")
    print(f"Round Stats Inserted: {counters.get('round_stats', 0)}")
    print(f"Judge Scores Inserted: {counters.get('judge_scores', 0)}")
    print()
    print("Pipeline completed successfully.")

    return counters


def main():
    """Entry point for the pipeline that handles errors gracefully."""
    try:
        run_pipeline()
    except Exception as exc:
        print("An unexpected error occurred while running the pipeline.")
        print(f"Error: {exc}")
        print("Traceback:")
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
