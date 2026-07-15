"""Insert round-by-round statistics into the round_stats table."""

import json


def _pick(mapping, *keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def insert_round_stats(connection, fight_id, round_stats_list):
    """Insert each round from the scraper payload and return the inserted IDs."""
    if not round_stats_list:
        return []

    inserted_ids = []

    try:
        with connection.cursor() as cursor:
            for round_entry in round_stats_list:
                fighter1 = round_entry.get("fighter1", {}) if isinstance(round_entry, dict) else {}
                fighter2 = round_entry.get("fighter2", {}) if isinstance(round_entry, dict) else {}
                round_number = round_entry.get("round")

                cursor.execute(
                    """
                    INSERT INTO round_stats (
                        fight_id,
                        round_number,
                        fighter1_kd,
                        fighter2_kd,
                        fighter1_sig_str,
                        fighter2_sig_str,
                        fighter1_sig_str_percent,
                        fighter2_sig_str_percent,
                        fighter1_total_str,
                        fighter2_total_str,
                        fighter1_td,
                        fighter2_td,
                        fighter1_td_percent,
                        fighter2_td_percent,
                        fighter1_sub_att,
                        fighter2_sub_att,
                        fighter1_rev,
                        fighter2_rev,
                        fighter1_ctrl,
                        fighter2_ctrl,
                        raw_round
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (fight_id, round_number)
                    DO UPDATE SET
                        fighter1_kd = EXCLUDED.fighter1_kd,
                        fighter2_kd = EXCLUDED.fighter2_kd,
                        fighter1_sig_str = EXCLUDED.fighter1_sig_str,
                        fighter2_sig_str = EXCLUDED.fighter2_sig_str,
                        fighter1_sig_str_percent = EXCLUDED.fighter1_sig_str_percent,
                        fighter2_sig_str_percent = EXCLUDED.fighter2_sig_str_percent,
                        fighter1_total_str = EXCLUDED.fighter1_total_str,
                        fighter2_total_str = EXCLUDED.fighter2_total_str,
                        fighter1_td = EXCLUDED.fighter1_td,
                        fighter2_td = EXCLUDED.fighter2_td,
                        fighter1_td_percent = EXCLUDED.fighter1_td_percent,
                        fighter2_td_percent = EXCLUDED.fighter2_td_percent,
                        fighter1_sub_att = EXCLUDED.fighter1_sub_att,
                        fighter2_sub_att = EXCLUDED.fighter2_sub_att,
                        fighter1_rev = EXCLUDED.fighter1_rev,
                        fighter2_rev = EXCLUDED.fighter2_rev,
                        fighter1_ctrl = EXCLUDED.fighter1_ctrl,
                        fighter2_ctrl = EXCLUDED.fighter2_ctrl,
                        raw_round = EXCLUDED.raw_round
                    RETURNING round_stat_id
                    """,
                    (
                        fight_id,
                        round_number,
                        _pick(fighter1, "kd"),
                        _pick(fighter2, "kd"),
                        _pick(fighter1, "sig_str"),
                        _pick(fighter2, "sig_str"),
                        _pick(fighter1, "sig_str_percent"),
                        _pick(fighter2, "sig_str_percent"),
                        _pick(fighter1, "total_str"),
                        _pick(fighter2, "total_str"),
                        _pick(fighter1, "td"),
                        _pick(fighter2, "td"),
                        _pick(fighter1, "td_percent"),
                        _pick(fighter2, "td_percent"),
                        _pick(fighter1, "sub_att"),
                        _pick(fighter2, "sub_att"),
                        _pick(fighter1, "rev"),
                        _pick(fighter2, "rev"),
                        _pick(fighter1, "ctrl"),
                        _pick(fighter2, "ctrl"),
                        json.dumps(round_entry),
                    ),
                )
                inserted_ids.append(cursor.fetchone()[0])

        connection.commit()
        return inserted_ids
    except Exception:
        connection.rollback()
        raise
