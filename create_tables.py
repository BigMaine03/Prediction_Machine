"""
create_tables.py

Creates a normalized PostgreSQL schema for the UFC prediction project.
The schema mirrors the stable nested dictionary contract produced by the scraper.

Usage: pass an open psycopg2 connection object to `create_tables(connection)`.

Design goals implemented here:
- Keep the scraper → DB contract stable: DB adapts to the scraper output shape.
- Normalize entities (events, fights, fighters, rounds, totals, judges, metadata).
- Provide JSONB columns for flexible extension while keeping important columns typed.
- Use ON DELETE CASCADE for children tied to parent lifecycle (event -> fights -> rounds/totals/judges).
- Use ON DELETE SET NULL for fighter references so removing a fighter record does not cascade-delete historic fights.

Note: This module only creates tables. It intentionally avoids any INSERT logic.
"""

import psycopg2


def create_tables(connection):
    """Create all required tables if they do not already exist.

    Parameters
    - connection: a psycopg2 connection instance (open). The function will
      create a cursor, run all CREATE TABLE statements, and commit once all
      succeed. On error it will rollback and re-raise the exception.

    Mapping to scraper output (stable contract):
    - The scraper returns `event` dictionaries containing `fights` lists.
      Each fight contains `general_info`, `fighters`, `totals`, `round_stats`,
      `judges`, and `metadata`. The SQL schema below maps those top-level keys
      to normalized tables described in comments above each CREATE statement.
    """

    sql_statements = []

    # Events table
    # Maps to the top-level event dictionary returned by the scraper.
    # - `event_url` is unique so repeated scrapes of the same event do not
    #   create duplicate event rows.
    sql_statements.append("""
    CREATE TABLE IF NOT EXISTS events (
        event_id SERIAL PRIMARY KEY,
        event_url TEXT UNIQUE NOT NULL,
        headline TEXT,
        event_date DATE,
        event_location TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
    );
    """)

    # Fighters table
    # Each unique fighter (by URL when available) is stored once. The scraper
    # provides fighter names and profile URLs inside the `fighters` mapping.
    sql_statements.append("""
    CREATE TABLE IF NOT EXISTS fighters (
        fighter_id SERIAL PRIMARY KEY,
        fighter_name TEXT NOT NULL,
        fighter_url TEXT UNIQUE,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        CONSTRAINT fighters_unique_name_url UNIQUE (fighter_name, fighter_url)
    );
    """)

    # Fights table
    # Each fight belongs to one event. This table stores the normalized fields
    # extracted from `general_info` and stores references to the two fighters.
    # The `fight_url` comes from `metadata.url` and is useful to uniquely
    # identify the fight page the scraper visited.
    sql_statements.append("""
    CREATE TABLE IF NOT EXISTS fights (
        fight_id SERIAL PRIMARY KEY,
        event_id INTEGER NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
        fight_url TEXT UNIQUE,

        -- Fields mapped from `general_info`
        weight_class TEXT,
        method TEXT,
        round INTEGER,
        time TEXT,
        time_format TEXT,
        referee TEXT,
        finish_details TEXT,

        -- Fighter references (nullable so we can still store historical fights
        -- even if a fighter row is later removed). We use SET NULL to avoid
        -- accidental data loss of fight history when a fighter is deleted.
        fighter1_id INTEGER REFERENCES fighters(fighter_id) ON DELETE SET NULL,
        fighter2_id INTEGER REFERENCES fighters(fighter_id) ON DELETE SET NULL,

        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),

        -- Optional: add a uniqueness guard for the same event + fighters + time
        -- This is conservative but helps avoid obvious duplicates. It may be
        -- widened or relaxed depending on scraper behavior (e.g., order of
        -- fighters may change); keep it as a helpful constraint.
        CONSTRAINT unique_event_fighters_time UNIQUE (event_id, fighter1_id, fighter2_id, time)
    );
    """)

    # Fight metadata
    # Stores the `metadata` block produced by the scraper, e.g. page title,
    # source and canonical fight URL. Kept separate to keep `fights` focused on
    # normalized relational fields while preserving raw metadata for debugging
    # and future use.
    sql_statements.append("""
    CREATE TABLE IF NOT EXISTS fight_metadata (
        fight_metadata_id SERIAL PRIMARY KEY,
        fight_id INTEGER UNIQUE REFERENCES fights(fight_id) ON DELETE CASCADE,
        page_title TEXT,
        source TEXT,
        canonical_url TEXT,
        raw_metadata JSONB,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
    );
    """)

    # Fight totals
    # The scraper returns a `totals` mapping which currently contains nested
    # structures like `significant_strikes` with stat keys for both fighters.
    # We normalize common columns for efficient querying while also storing the
    # original nested structure in `raw_totals` so the schema can adapt to
    # additional keys without requiring migrations.
    sql_statements.append("""
    CREATE TABLE IF NOT EXISTS fight_totals (
        fight_total_id SERIAL PRIMARY KEY,
        fight_id INTEGER NOT NULL REFERENCES fights(fight_id) ON DELETE CASCADE,

        -- Normalized columns for the common significant striking totals
        fighter1_sig_str TEXT,
        fighter2_sig_str TEXT,
        fighter1_sig_str_percent TEXT,
        fighter2_sig_str_percent TEXT,
        fighter1_head TEXT,
        fighter2_head TEXT,
        fighter1_body TEXT,
        fighter2_body TEXT,
        fighter1_leg TEXT,
        fighter2_leg TEXT,
        fighter1_distance TEXT,
        fighter2_distance TEXT,
        fighter1_clinch TEXT,
        fighter2_clinch TEXT,
        fighter1_ground TEXT,
        fighter2_ground TEXT,

        -- Raw JSON backup of the entire `totals` mapping from the scraper.
        raw_totals JSONB,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
    );
    """)

    # Round stats
    # Each row corresponds to one round's statistics and maps to the items in
    # `round_stats` returned by the scraper. We include standard columns and
    # a JSONB `raw_round` for extensibility.
    sql_statements.append("""
    CREATE TABLE IF NOT EXISTS round_stats (
        round_stat_id SERIAL PRIMARY KEY,
        fight_id INTEGER NOT NULL REFERENCES fights(fight_id) ON DELETE CASCADE,
        round_number INTEGER NOT NULL,

        -- Example normalized per-round statistics for fast queries
        fighter1_kd TEXT,
        fighter2_kd TEXT,
        fighter1_sig_str TEXT,
        fighter2_sig_str TEXT,
        fighter1_sig_str_percent TEXT,
        fighter2_sig_str_percent TEXT,
        fighter1_total_str TEXT,
        fighter2_total_str TEXT,
        fighter1_td TEXT,
        fighter2_td TEXT,
        fighter1_td_percent TEXT,
        fighter2_td_percent TEXT,
        fighter1_sub_att TEXT,
        fighter2_sub_att TEXT,
        fighter1_rev TEXT,
        fighter2_rev TEXT,
        fighter1_ctrl TEXT,
        fighter2_ctrl TEXT,

        -- Nested significant strike breakdown for the round
        raw_round JSONB,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),

        CONSTRAINT unique_fight_round UNIQUE (fight_id, round_number)
    );
    """)

    # Judge scores
    # Stores the judge entries produced by the scraper under `judges`. Only
    # populated for fights that have judge information (e.g., decisions).
    sql_statements.append("""
    CREATE TABLE IF NOT EXISTS judge_scores (
        judge_score_id SERIAL PRIMARY KEY,
        fight_id INTEGER NOT NULL REFERENCES fights(fight_id) ON DELETE CASCADE,
        judge_name TEXT,
        score TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
        CONSTRAINT unique_fight_judge UNIQUE (fight_id, judge_name)
    );
    """)

    # Helpful indexes for common lookup patterns
    sql_statements.append("""
    CREATE INDEX IF NOT EXISTS idx_events_event_date ON events(event_date);
    CREATE INDEX IF NOT EXISTS idx_fighters_name ON fighters(fighter_name);
    CREATE INDEX IF NOT EXISTS idx_fights_event_id ON fights(event_id);
    """)

    cursor = connection.cursor()
    try:
        for stmt in sql_statements:
            cursor.execute(stmt)

        # Commit once all CREATE TABLE statements have run successfully.
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


if __name__ == "__main__":
    # Example quick-run when invoked directly. Not used during normal
    # application runtime but handy for local setup. Requires a valid
    # PG connection string in the environment or replace below.
    import os

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("Please set DATABASE_URL environment variable (psycopg2 DSN)")
    else:
        conn = psycopg2.connect(dsn)
        try:
            create_tables(conn)
            print("Tables created successfully.")
        finally:
            conn.close()
