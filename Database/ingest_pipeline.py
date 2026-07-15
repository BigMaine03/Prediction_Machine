"""Coordinate the end-to-end ingestion flow for scraper event payloads.

This module intentionally stays at a high level. It does not contain any SQL,
HTML parsing logic, or scraping concerns. Its only responsibility is to call the
existing insert helpers in the correct order so the database is populated from
scraper data in a readable and maintainable way.
"""

from db import get_connection
from insert_events import insert_event
from insert_fighters import insert_fighter
from insert_fights import insert_fight
from insert_judges import insert_judges
from insert_metadata import insert_metadata
from insert_round_stats import insert_round_stats
from insert_totals import insert_totals


def _event_label(event_payload, fallback_url):
    """Return a readable label for logging while keeping the code simple."""
    if isinstance(event_payload, dict):
        headline = event_payload.get("headline")
        if headline:
            return headline
    return fallback_url


def _ingest_single_fight(connection, fight_payload, event_id, counters):
    """Insert one fight and every child record owned by that fight."""
    if not isinstance(fight_payload, dict):
        raise ValueError("Each fight entry must be a dictionary")

    fight_label = fight_payload.get("general_info", {}).get("fight_name") or "Fight"
    print(f"Processing Fight: {fight_label}")

    fighters = fight_payload.get("fighters", {}) or {}
    fighter1_payload = fighters.get("fighter1")
    fighter2_payload = fighters.get("fighter2")

    fighter1_id = insert_fighter(connection, fighter1_payload)
    fighter2_id = insert_fighter(connection, fighter2_payload)
    counters["fighters"] += 2
    print("Inserted Fighters")

    fight_id = insert_fight(
        connection,
        fight_payload,
        event_id,
        fighter1_id,
        fighter2_id,
    )
    counters["fights"] += 1
    print("Inserted Fight")

    metadata = fight_payload.get("metadata") or {}
    insert_metadata(connection, fight_id, metadata)
    print("Inserted Metadata")

    totals = fight_payload.get("totals") or {}
    insert_totals(connection, fight_id, totals)
    print("Inserted Totals")

    round_stats = fight_payload.get("round_stats") or []
    inserted_round_stats = insert_round_stats(connection, fight_id, round_stats)
    counters["round_stats"] += len(inserted_round_stats)
    print("Inserted Round Stats")

    judges = fight_payload.get("judges") or []
    if judges:
        inserted_judges = insert_judges(connection, fight_id, judges)
        counters["judge_scores"] += len(inserted_judges)
        print("Inserted Judges")
    else:
        print("Skipped Judges")

    print("Finished Fight")


def ingest_events(event_results):
    """Ingest an entire scraper payload into the database.

    The orchestrator keeps the flow readable by delegating every database write
    to the dedicated insert helper modules. This makes the pipeline easy to
    maintain and avoids duplicating insert logic in one large function.
    """
    if not isinstance(event_results, dict):
        raise ValueError("event_results must be a dictionary of event payloads")

    connection = get_connection()
    counters = {
        "events": 0,
        "fights": 0,
        "fighters": 0,
        "round_stats": 0,
        "judge_scores": 0,
    }

    try:
        for event_url, event_payload in event_results.items():
            event_label = _event_label(event_payload, event_url)
            print(f"Processing Event: {event_label}")

            try:
                event_id = insert_event(connection, event_payload)
                print("Inserted Event")

                fights = event_payload.get("fights") or []
                if not isinstance(fights, list):
                    fights = []

                for fight_payload in fights:
                    _ingest_single_fight(connection, fight_payload, event_id, counters)

                connection.commit()
                counters["events"] += 1
                print("Finished Event")
            except Exception as exc:
                print(f"Error processing event {event_label}: {exc}")
                try:
                    connection.rollback()
                except Exception as rollback_error:
                    print(f"Rollback failed for event {event_label}: {rollback_error}")
                continue
    finally:
        connection.close()

    print(f"Events Processed: {counters['events']}")
    print(f"Fights Inserted: {counters['fights']}")
    print(f"Fighters Inserted: {counters['fighters']}")
    print(f"Round Stats Inserted: {counters['round_stats']}")
    print(f"Judge Scores Inserted: {counters['judge_scores']}")

    return counters
