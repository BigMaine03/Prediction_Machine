"""Insert one metadata row for a fight."""

import json


def insert_metadata(connection, fight_id, metadata_dict):
    """Store the scraper metadata for a fight as a normalized row."""
    if not metadata_dict:
        return None

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO fight_metadata (
                    fight_id,
                    page_title,
                    source,
                    canonical_url,
                    raw_metadata
                )
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (fight_id)
                DO UPDATE SET
                    page_title = EXCLUDED.page_title,
                    source = EXCLUDED.source,
                    canonical_url = EXCLUDED.canonical_url,
                    raw_metadata = EXCLUDED.raw_metadata
                RETURNING fight_metadata_id
                """,
                (
                    fight_id,
                    metadata_dict.get("page_title"),
                    metadata_dict.get("source"),
                    metadata_dict.get("url"),
                    json.dumps(metadata_dict),
                ),
            )
            fight_metadata_id = cursor.fetchone()[0]
        connection.commit()
        return fight_metadata_id
    except Exception:
        connection.rollback()
        raise
