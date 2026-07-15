"""Insert judge score rows for a fight."""


def insert_judges(connection, fight_id, judges_list):
    """Insert judge rows when the scraper has decision information."""
    if not judges_list:
        return []

    inserted_ids = []

    try:
        with connection.cursor() as cursor:
            for judge_entry in judges_list:
                if not isinstance(judge_entry, dict):
                    continue

                judge_name = judge_entry.get("judge") or judge_entry.get("judge_name")
                score = judge_entry.get("score")

                if not judge_name:
                    continue

                cursor.execute(
                    """
                    INSERT INTO judge_scores (fight_id, judge_name, score)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (fight_id, judge_name)
                    DO UPDATE SET score = EXCLUDED.score
                    RETURNING judge_score_id
                    """,
                    (fight_id, judge_name, score),
                )
                inserted_ids.append(cursor.fetchone()[0])

        connection.commit()
        return inserted_ids
    except Exception:
        connection.rollback()
        raise
