"""Insert bio information for a fighter into the fighter_bio_stats table."""

import json


def insert_fighter_bio(connection, fighter_id, bio_dict):
    """Insert or update the bio row for a fighter and return fighter_id."""

    def parse_int(value):
        if value is None:
            return None
        text = str(value).strip()
        if text == "":
            return None
        import re
        match = re.search(r"-?\d+", text.replace(",", ""))
        return int(match.group()) if match else None

    def parse_float(value):
        if value is None:
            return None
        text = str(value).strip().replace(",", "").replace("%", "")
        if text == "":
            return None
        import re
        match = re.search(r"-?\d+(\.\d+)?", text)
        return float(match.group()) if match else None

    bio_dict = bio_dict or {}

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO fighter_bio
                (
                    fighter_id,
                    status,
                    place_of_birth,
                    trains_at,
                    fighting_style,
                    age,
                    height,
                    weight,
                    reach,
                    leg_reach,
                    octagon_debut,
                    raw_bio
                )
                VALUES
                (
                    %s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s
                )
                ON CONFLICT (fighter_id)
                DO UPDATE SET
                    status = EXCLUDED.status,
                    place_of_birth = EXCLUDED.place_of_birth,
                    trains_at = EXCLUDED.trains_at,
                    fighting_style = EXCLUDED.fighting_style,
                    age = EXCLUDED.age,
                    height = EXCLUDED.height,
                    weight = EXCLUDED.weight,
                    reach = EXCLUDED.reach,
                    leg_reach = EXCLUDED.leg_reach,
                    octagon_debut = EXCLUDED.octagon_debut,
                    raw_bio = EXCLUDED.raw_bio
                """,
                (
                    fighter_id,
                    bio_dict.get("status"),
                    bio_dict.get("place_of_birth"),
                    bio_dict.get("trains_at"),
                    bio_dict.get("fighting_style"),
                    parse_int(bio_dict.get("age")),
                    parse_float(bio_dict.get("height")),
                    parse_float(bio_dict.get("weight")),
                    parse_int(bio_dict.get("reach")),
                    parse_float(bio_dict.get("leg_reach")),
                    bio_dict.get("octagon_debut"),
                    json.dumps(bio_dict),
                ),
            )

        connection.commit()
        return fighter_id
    except Exception:
        connection.rollback()
        raise