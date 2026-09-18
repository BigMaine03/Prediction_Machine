"""Insert fight total statistics into the fight_totals table."""

import json


def _pick(mapping, *keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def insert_totals(connection, fight_id, totals_dict):
    """Store a fight's totals in a normalized row."""
    if not totals_dict:
        return None

    significant_strikes = totals_dict.get("significant_strikes", {}) if isinstance(totals_dict, dict) else {}
    fighter1 = significant_strikes.get("fighter1", {}) if isinstance(significant_strikes, dict) else {}
    fighter2 = significant_strikes.get("fighter2", {}) if isinstance(significant_strikes, dict) else {}

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO fight_totals (
                    fight_id,
                    fighter1_sig_str,
                    fighter2_sig_str,
                    fighter1_sig_str_percent,
                    fighter2_sig_str_percent,
                    fighter1_head,
                    fighter2_head,
                    fighter1_body,
                    fighter2_body,
                    fighter1_leg,
                    fighter2_leg,
                    fighter1_distance,
                    fighter2_distance,
                    fighter1_clinch,
                    fighter2_clinch,
                    fighter1_ground,
                    fighter2_ground,
                    raw_totals
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (fight_id)
                DO UPDATE SET
                    fighter1_sig_str = EXCLUDED.fighter1_sig_str,
                    fighter2_sig_str = EXCLUDED.fighter2_sig_str,
                    fighter1_sig_str_percent = EXCLUDED.fighter1_sig_str_percent,
                    fighter2_sig_str_percent = EXCLUDED.fighter2_sig_str_percent,
                    fighter1_head = EXCLUDED.fighter1_head,
                    fighter2_head = EXCLUDED.fighter2_head,
                    fighter1_body = EXCLUDED.fighter1_body,
                    fighter2_body = EXCLUDED.fighter2_body,
                    fighter1_leg = EXCLUDED.fighter1_leg,
                    fighter2_leg = EXCLUDED.fighter2_leg,
                    fighter1_distance = EXCLUDED.fighter1_distance,
                    fighter2_distance = EXCLUDED.fighter2_distance,
                    fighter1_clinch = EXCLUDED.fighter1_clinch,
                    fighter2_clinch = EXCLUDED.fighter2_clinch,
                    fighter1_ground = EXCLUDED.fighter1_ground,
                    fighter2_ground = EXCLUDED.fighter2_ground,
                    raw_totals = EXCLUDED.raw_totals
                RETURNING fight_total_id
                """,
                (
                    fight_id,
                    _pick(fighter1, "sig_str"),
                    _pick(fighter2, "sig_str"),
                    _pick(fighter1, "sig_str_percent"),
                    _pick(fighter2, "sig_str_percent"),
                    _pick(fighter1, "head"),
                    _pick(fighter2, "head"),
                    _pick(fighter1, "body"),
                    _pick(fighter2, "body"),
                    _pick(fighter1, "leg"),
                    _pick(fighter2, "leg"),
                    _pick(fighter1, "distance"),
                    _pick(fighter2, "distance"),
                    _pick(fighter1, "clinch"),
                    _pick(fighter2, "clinch"),
                    _pick(fighter1, "ground"),
                    _pick(fighter2, "ground"),
                    json.dumps(totals_dict),
                ),
            )
            fight_total_id = cursor.fetchone()[0]
        connection.commit()
        return fight_total_id
    except Exception:
        connection.rollback()
        raise
