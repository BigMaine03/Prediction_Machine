"""Insert one fighter row into the fighters table without creating duplicates."""


def insert_fighter(connection, fighter_dict):
    """Insert a fighter if it does not already exist and return its fighter_id."""
    fighter_name = None
    fighter_url = None

    if isinstance(fighter_dict, dict):
        fighter_name = fighter_dict.get("fighter_name") or fighter_dict.get("name")
        fighter_url = fighter_dict.get("fighter_url") or fighter_dict.get("url")

    if not fighter_name:
        raise ValueError("fighter_dict must contain a non-empty 'fighter_name' or 'name' field")

    try:
        with connection.cursor() as cursor:
            if fighter_url:
                cursor.execute(
                    "SELECT fighter_id FROM fighters WHERE fighter_url = %s",
                    (fighter_url,),
                )
                existing = cursor.fetchone()
                if existing:
                    connection.commit()
                    return existing[0]

            cursor.execute(
                "SELECT fighter_id FROM fighters WHERE fighter_name = %s",
                (fighter_name,),
            )
            existing = cursor.fetchone()
            if existing:
                connection.commit()
                return existing[0]

            cursor.execute(
                """
                INSERT INTO fighters (fighter_name, fighter_url)
                VALUES (%s, %s)
                RETURNING fighter_id
                """,
                (fighter_name, fighter_url),
            )
            fighter_id = cursor.fetchone()[0]

        connection.commit()
        return fighter_id
    except Exception:
        connection.rollback()
        raise
