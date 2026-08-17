"""Insert one fight row into the fights table."""

import json

from numpy import rint


def _resolve_outcome(fight_dict, fighter1_id, fighter2_id):
    """Resolve winner_id and outcome_type from fight_dict's fighters/fight_result
    payloads. Matches by fighter URL (not document order/position) against the
    already-resolved fighter1_id/fighter2_id, the same defensive pattern used
    to fix the earlier fighter-identity collision bug.

    Returns (winner_id, outcome_type). winner_id is None for draws, no
    contests, or anything that doesn't cleanly resolve; outcome_type is one
    of 'decisive', 'draw', 'no_contest', or 'unknown'.
    """
    fighters = fight_dict.get("fighters", {}) if isinstance(fight_dict, dict) else {}
    fight_result = fight_dict.get("fight_result", {}) if isinstance(fight_dict, dict) else {}

    f1_url = fighters.get("fighter1", {}).get("url")
    f2_url = fighters.get("fighter2", {}).get("url")

    f1_status = fight_result.get(f1_url) if f1_url else None
    f2_status = fight_result.get(f2_url) if f2_url else None

    if f1_status == "W" and f2_status == "L":
        return fighter1_id, "decisive"
    if f2_status == "W" and f1_status == "L":
        return fighter2_id, "decisive"
    if f1_status == "D" and f2_status == "D":
        return None, "draw"
    if f1_status == "NC" and f2_status == "NC":
        return None, "no_contest"

    # Anything else (missing status, unexpected combination, etc.) --
    # don't guess. Leave both fields describing the ambiguity rather than
    # silently assigning a winner.
    return None, "unknown"


def insert_fight(connection, fight_dict, event_id, fighter1_id, fighter2_id):
    """Insert or update a fight and return its fight_id."""
    general_info = fight_dict.get("general_info", {}) if isinstance(fight_dict, dict) else {}
    metadata = fight_dict.get("metadata", {}) if isinstance(fight_dict, dict) else {}

    fight_url = metadata.get("url") or fight_dict.get("url") if isinstance(fight_dict, dict) else None
    weight_class = general_info.get("weight_class")
    method = general_info.get("method")
    fight_round = general_info.get("round")
    time_value = general_info.get("time")
    time_format = general_info.get("time_format")
    referee = general_info.get("referee")
    finish_details = general_info.get("finish_details")

    winner_id, outcome_type = _resolve_outcome(fight_dict, fighter1_id, fighter2_id)

    if not fight_url:
        raise ValueError("fight_dict must contain a non-empty metadata['url'] value")


    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO fights (
                    event_id,
                    fight_url,
                    weight_class,
                    method,
                    round,
                    time,
                    time_format,
                    referee,
                    finish_details,
                    fighter1_id,
                    fighter2_id,
                    winner_id,
                    outcome_type
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (fight_url)
                DO UPDATE SET
                    event_id = EXCLUDED.event_id,
                    weight_class = EXCLUDED.weight_class,
                    method = EXCLUDED.method,
                    round = EXCLUDED.round,
                    time = EXCLUDED.time,
                    time_format = EXCLUDED.time_format,
                    referee = EXCLUDED.referee,
                    finish_details = EXCLUDED.finish_details,
                    fighter1_id = EXCLUDED.fighter1_id,
                    fighter2_id = EXCLUDED.fighter2_id,
                    winner_id = EXCLUDED.winner_id,
                    outcome_type = EXCLUDED.outcome_type
                RETURNING fight_id
                """,
                (
                    event_id,
                    fight_url,
                    weight_class,
                    method,
                    fight_round,
                    time_value,
                    time_format,
                    referee,
                    finish_details,
                    fighter1_id,
                    fighter2_id,
                    winner_id,
                    outcome_type,
                ),
            )
            fight_id = cursor.fetchone()[0]
        connection.commit()
        return fight_id
    except Exception:
        connection.rollback()
        raise
