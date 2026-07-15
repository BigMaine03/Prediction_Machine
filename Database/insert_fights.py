"""Insert one fight row into the fights table."""

import json


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
                    fighter2_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    fighter2_id = EXCLUDED.fighter2_id
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
                ),
            )
            fight_id = cursor.fetchone()[0]
        connection.commit()
        return fight_id
    except Exception:
        connection.rollback()
        raise
