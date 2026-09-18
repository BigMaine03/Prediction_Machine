"""
create_fact_tables.py

Layer 2: cleaned numeric facts derived from scraper TEXT/JSONB tables.

  fight_facts            1 row per fight (bout context + labels)
  fighter_fight_facts    1 row per corner (this fight's actual stats + result)
  fighter_round_facts    1 row per corner per round

These tables are computed, not scraped. Raw ingest tables stay as the archive.
Feature engineering should read facts, not fighter1_sig_str strings.

Usage: pass an open psycopg2 connection to create_fact_tables(connection).
"""

import psycopg2


def create_fact_tables(connection):
    statements = [
        """
        CREATE TABLE IF NOT EXISTS fight_facts (
            fight_id INTEGER PRIMARY KEY REFERENCES fights(fight_id) ON DELETE CASCADE,
            event_id INTEGER REFERENCES events(event_id) ON DELETE CASCADE,
            event_date DATE NOT NULL,

            weight_class_raw TEXT,
            weight_class_norm TEXT,
            is_title BOOLEAN,
            is_women BOOLEAN,

            time_format_raw TEXT,
            scheduled_rounds INTEGER,
            final_round INTEGER,
            fight_seconds DOUBLE PRECISION,

            method_raw TEXT,
            method_group TEXT,
            outcome_type TEXT,
            winner_id INTEGER REFERENCES fighters(fighter_id) ON DELETE SET NULL,
            is_valid_for_stats BOOLEAN,

            computed_at TIMESTAMP WITH TIME ZONE DEFAULT now(),

            CONSTRAINT fight_facts_method_group_check
                CHECK (method_group IS NULL OR method_group IN
                    ('KO_TKO', 'SUB', 'DEC', 'DQ', 'NC', 'OTHER')),
            CONSTRAINT fight_facts_outcome_type_check
                CHECK (outcome_type IS NULL OR outcome_type IN
                    ('decisive', 'draw', 'no_contest', 'unknown'))
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS fighter_fight_facts (
            fighter_id INTEGER NOT NULL REFERENCES fighters(fighter_id) ON DELETE CASCADE,
            fight_id INTEGER NOT NULL REFERENCES fights(fight_id) ON DELETE CASCADE,
            opponent_id INTEGER REFERENCES fighters(fighter_id) ON DELETE SET NULL,
            is_fighter1 BOOLEAN,
            event_date DATE NOT NULL,

            result TEXT,
            is_valid_for_stats BOOLEAN,
            method_group TEXT,
            final_round INTEGER,
            fight_seconds DOUBLE PRECISION,

            sig_str_landed DOUBLE PRECISION,
            sig_str_attempted DOUBLE PRECISION,
            sig_str_percent DOUBLE PRECISION,
            total_str_landed DOUBLE PRECISION,
            total_str_attempted DOUBLE PRECISION,

            td_landed DOUBLE PRECISION,
            td_attempted DOUBLE PRECISION,
            td_percent DOUBLE PRECISION,

            kd DOUBLE PRECISION,
            sub_att DOUBLE PRECISION,
            rev DOUBLE PRECISION,
            ctrl_seconds DOUBLE PRECISION,

            distance_landed DOUBLE PRECISION,
            distance_attempted DOUBLE PRECISION,
            clinch_landed DOUBLE PRECISION,
            clinch_attempted DOUBLE PRECISION,
            ground_landed DOUBLE PRECISION,
            ground_attempted DOUBLE PRECISION,

            head_landed DOUBLE PRECISION,
            head_attempted DOUBLE PRECISION,
            body_landed DOUBLE PRECISION,
            body_attempted DOUBLE PRECISION,
            leg_landed DOUBLE PRECISION,
            leg_attempted DOUBLE PRECISION,

            distance_pct DOUBLE PRECISION,
            clinch_pct DOUBLE PRECISION,
            ground_pct DOUBLE PRECISION,

            -- opponent-linked (filled after both corners exist for the fight)
            sig_str_absorbed DOUBLE PRECISION,
            sig_str_opp_attempted DOUBLE PRECISION,
            strike_defense DOUBLE PRECISION,
            td_absorbed DOUBLE PRECISION,
            td_opp_attempted DOUBLE PRECISION,
            td_defense DOUBLE PRECISION,
            kd_absorbed DOUBLE PRECISION,
            ctrl_against_seconds DOUBLE PRECISION,
            sig_str_diff DOUBLE PRECISION,
            td_diff DOUBLE PRECISION,
            ctrl_diff DOUBLE PRECISION,

            -- per-minute rates (fight_seconds is the denominator)
            fight_minutes DOUBLE PRECISION,
            sig_str_landed_pm DOUBLE PRECISION,
            sig_str_absorbed_pm DOUBLE PRECISION,
            sig_str_attempted_pm DOUBLE PRECISION,
            td_landed_pm DOUBLE PRECISION,
            td_attempted_pm DOUBLE PRECISION,
            kd_pm DOUBLE PRECISION,
            sub_att_pm DOUBLE PRECISION,
            ctrl_seconds_pm DOUBLE PRECISION,
            pace DOUBLE PRECISION,

            -- cardio (this-fight; R3/R1 only defined if round 3 exists)
            r1_sig_str_attempted DOUBLE PRECISION,
            r1_sig_str_landed DOUBLE PRECISION,
            r3_sig_str_attempted DOUBLE PRECISION,
            r3_sig_str_landed DOUBLE PRECISION,
            reached_round_3 BOOLEAN,
            completed_round_3 BOOLEAN,
            cardio_r3_r1_att DOUBLE PRECISION,
            cardio_r3_r1_landed DOUBLE PRECISION,
            late_td_landed DOUBLE PRECISION,
            late_td_attempted DOUBLE PRECISION,
            late_kd DOUBLE PRECISION,
            late_ctrl_seconds DOUBLE PRECISION,
            r5_sig_str_attempted DOUBLE PRECISION,
            r5_sig_str_landed DOUBLE PRECISION,
            reached_round_5 BOOLEAN,
            completed_round_5 BOOLEAN,
            went_the_distance BOOLEAN,
            cardio_r5_r1_att DOUBLE PRECISION,
            cardio_r5_r1_landed DOUBLE PRECISION,
            cardio_distance_att DOUBLE PRECISION,
            cardio_distance_landed DOUBLE PRECISION,
            cardio_consec_att DOUBLE PRECISION,
            cardio_consec_landed DOUBLE PRECISION,
            last_completed_round INTEGER,
            cardio_last_full_r1_att DOUBLE PRECISION,
            cardio_last_full_r1_landed DOUBLE PRECISION,
            cardio_five_stoppage_att DOUBLE PRECISION,
            cardio_five_stoppage_landed DOUBLE PRECISION,
            cardio_observed_att DOUBLE PRECISION,
            cardio_observed_landed DOUBLE PRECISION,

            computed_at TIMESTAMP WITH TIME ZONE DEFAULT now(),

            PRIMARY KEY (fighter_id, fight_id),
            CONSTRAINT fighter_fight_facts_result_check
                CHECK (result IS NULL OR result IN ('W', 'L', 'D', 'NC'))
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS fighter_round_facts (
            fight_id INTEGER NOT NULL REFERENCES fights(fight_id) ON DELETE CASCADE,
            fighter_id INTEGER NOT NULL REFERENCES fighters(fighter_id) ON DELETE CASCADE,
            round_number INTEGER NOT NULL,
            is_fighter1 BOOLEAN,

            sig_str_landed DOUBLE PRECISION,
            sig_str_attempted DOUBLE PRECISION,
            total_str_landed DOUBLE PRECISION,
            total_str_attempted DOUBLE PRECISION,
            td_landed DOUBLE PRECISION,
            td_attempted DOUBLE PRECISION,
            kd DOUBLE PRECISION,
            sub_att DOUBLE PRECISION,
            rev DOUBLE PRECISION,
            ctrl_seconds DOUBLE PRECISION,

            distance_landed DOUBLE PRECISION,
            distance_attempted DOUBLE PRECISION,
            clinch_landed DOUBLE PRECISION,
            clinch_attempted DOUBLE PRECISION,
            ground_landed DOUBLE PRECISION,
            ground_attempted DOUBLE PRECISION,

            head_landed DOUBLE PRECISION,
            head_attempted DOUBLE PRECISION,
            body_landed DOUBLE PRECISION,
            body_attempted DOUBLE PRECISION,
            leg_landed DOUBLE PRECISION,
            leg_attempted DOUBLE PRECISION,

            computed_at TIMESTAMP WITH TIME ZONE DEFAULT now(),

            PRIMARY KEY (fight_id, fighter_id, round_number)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_fight_facts_event_date ON fight_facts(event_date)",
        "CREATE INDEX IF NOT EXISTS idx_fight_facts_method_group ON fight_facts(method_group)",
        "CREATE INDEX IF NOT EXISTS idx_fffacts_fighter_date ON fighter_fight_facts(fighter_id, event_date)",
        "CREATE INDEX IF NOT EXISTS idx_fffacts_fight_id ON fighter_fight_facts(fight_id)",
        "CREATE INDEX IF NOT EXISTS idx_frfacts_fighter ON fighter_round_facts(fighter_id, fight_id)",
    ]

    extra_fact_columns = [
        "sig_str_absorbed DOUBLE PRECISION",
        "sig_str_opp_attempted DOUBLE PRECISION",
        "strike_defense DOUBLE PRECISION",
        "td_absorbed DOUBLE PRECISION",
        "td_opp_attempted DOUBLE PRECISION",
        "td_defense DOUBLE PRECISION",
        "kd_absorbed DOUBLE PRECISION",
        "ctrl_against_seconds DOUBLE PRECISION",
        "sig_str_diff DOUBLE PRECISION",
        "td_diff DOUBLE PRECISION",
        "ctrl_diff DOUBLE PRECISION",
        "fight_minutes DOUBLE PRECISION",
        "sig_str_landed_pm DOUBLE PRECISION",
        "sig_str_absorbed_pm DOUBLE PRECISION",
        "sig_str_attempted_pm DOUBLE PRECISION",
        "td_landed_pm DOUBLE PRECISION",
        "td_attempted_pm DOUBLE PRECISION",
        "kd_pm DOUBLE PRECISION",
        "sub_att_pm DOUBLE PRECISION",
        "ctrl_seconds_pm DOUBLE PRECISION",
        "pace DOUBLE PRECISION",
        "r1_sig_str_attempted DOUBLE PRECISION",
        "r1_sig_str_landed DOUBLE PRECISION",
        "r3_sig_str_attempted DOUBLE PRECISION",
        "r3_sig_str_landed DOUBLE PRECISION",
        "reached_round_3 BOOLEAN",
        "completed_round_3 BOOLEAN",
        "cardio_r3_r1_att DOUBLE PRECISION",
        "cardio_r3_r1_landed DOUBLE PRECISION",
        "late_td_landed DOUBLE PRECISION",
        "late_td_attempted DOUBLE PRECISION",
        "late_kd DOUBLE PRECISION",
        "late_ctrl_seconds DOUBLE PRECISION",
        "r5_sig_str_attempted DOUBLE PRECISION",
        "r5_sig_str_landed DOUBLE PRECISION",
        "reached_round_5 BOOLEAN",
        "completed_round_5 BOOLEAN",
        "went_the_distance BOOLEAN",
        "cardio_r5_r1_att DOUBLE PRECISION",
        "cardio_r5_r1_landed DOUBLE PRECISION",
        "cardio_distance_att DOUBLE PRECISION",
        "cardio_distance_landed DOUBLE PRECISION",
        "cardio_consec_att DOUBLE PRECISION",
        "cardio_consec_landed DOUBLE PRECISION",
        "last_completed_round INTEGER",
        "cardio_last_full_r1_att DOUBLE PRECISION",
        "cardio_last_full_r1_landed DOUBLE PRECISION",
        "cardio_five_stoppage_att DOUBLE PRECISION",
        "cardio_five_stoppage_landed DOUBLE PRECISION",
        "cardio_observed_att DOUBLE PRECISION",
        "cardio_observed_landed DOUBLE PRECISION",
    ]

    cursor = connection.cursor()
    try:
        for stmt in statements:
            cursor.execute(stmt)
        for coldef in extra_fact_columns:
            cursor.execute(
                f"ALTER TABLE fighter_fight_facts ADD COLUMN IF NOT EXISTS {coldef}"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()


if __name__ == "__main__":
    dsn = "postgresql://Manas@localhost:5432/manas_ufc_pred_machine"
    try:
        conn = psycopg2.connect(dsn)
        try:
            create_fact_tables(conn)
            print("Fact tables created successfully.")
        finally:
            conn.close()
    except Exception as exc:
        print(f"Failed to connect to PostgreSQL: {exc}")
