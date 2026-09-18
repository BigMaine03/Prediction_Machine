"""Permanent ledger of card predictions vs later fight outcomes.

One row per bout. Re-running the same card before it is scored overwrites
probabilities. Scored rows stay frozen.
"""


def create_card_pick_tables(connection):
    sql = """
    CREATE TABLE IF NOT EXISTS card_picks (
        card_pick_id SERIAL PRIMARY KEY,
        match_key TEXT NOT NULL UNIQUE,

        event_url TEXT,
        event_date DATE,
        event_headline TEXT,

        fighter_a_name TEXT NOT NULL,
        fighter_b_name TEXT NOT NULL,
        fighter_a_url TEXT,
        fighter_b_url TEXT,
        fighter_a_id INTEGER,
        fighter_b_id INTEGER,

        p_a DOUBLE PRECISION NOT NULL,
        p_b DOUBLE PRECISION NOT NULL,
        pick TEXT NOT NULL CHECK (pick IN ('A', 'B', 'TOSSUP')),
        is_tossup BOOLEAN NOT NULL,
        is_high_conf_66 BOOLEAN NOT NULL,

        predicted_at TIMESTAMP WITH TIME ZONE DEFAULT now(),

        fight_id INTEGER REFERENCES fights(fight_id) ON DELETE SET NULL,
        outcome_type TEXT,
        winner_side TEXT CHECK (winner_side IS NULL OR winner_side IN ('A', 'B')),
        pick_correct BOOLEAN,
        scored_at TIMESTAMP WITH TIME ZONE
    );
    """
    with connection.cursor() as cursor:
        cursor.execute(sql)
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_card_picks_pending ON card_picks (scored_at)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_card_picks_event_date ON card_picks (event_date)"
        )
    connection.commit()
