"""Insert one event row into the events table."""


def insert_event(connection, event_dict):
    """Insert or update an event based on the scraper's event payload."""
    event_url = event_dict.get("url")
    headline = event_dict.get("headline")
    event_date = event_dict.get("date")
    event_location = event_dict.get("location")

    if not event_url:
        raise ValueError("event_dict must contain a non-empty 'url' field")

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO events (event_url, headline, event_date, event_location)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (event_url)
                DO UPDATE SET
                    headline = EXCLUDED.headline,
                    event_date = EXCLUDED.event_date,
                    event_location = EXCLUDED.event_location
                RETURNING event_id
                """,
                (event_url, headline, event_date, event_location),
            )
            event_id = cursor.fetchone()[0]
        connection.commit()
        return event_id
    except Exception:
        connection.rollback()
        raise
