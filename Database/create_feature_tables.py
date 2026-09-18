"""
create_feature_tables.py

Creates the fighter_fight_features table: one row per (fighter, fight),
holding point-in-time rolling-window features computed strictly from data
available BEFORE that fight's event_date.

Kept separate from Database/create_tables.py because this table holds derived
/ computed data, not raw scraper output. It does not touch or depend on the
raw ingestion tables' shape.

Usage: pass an open psycopg2 connection to create_feature_tables(connection).
"""

import os
import sys

import psycopg2

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Features.feature_columns import (
    DIFF_COLS,
    FEATURE_EXPORT_COLS,
    LABEL_FACT_COLS,
    MATCHUP_PRODUCTS,
    STAT_COLS,
    TREND_PAIRS,
    WINDOWS,
    trend_col,
    window_stat_col,
)


def create_fight_model_view(cursor):
    """One row per fight: pre-fight f1/f2 features + diffs + this-fight labels.

    Labels come from Layer 2 facts, not from fighter_fight_features.result.
    """
    selects = [
        "f1.fight_id AS fight_id",
        "f1.event_date AS event_date",
        "ff.weight_class_norm AS weight_class_norm",
        "ff.is_title AS is_title",
        "ff.is_women AS is_women",
        "ff.scheduled_rounds AS scheduled_rounds",
        "f1.fighter_id AS f1_fighter_id",
        "f2.fighter_id AS f2_fighter_id",
        "b1.height AS f1_height",
        "b1.reach AS f1_reach",
        "b2.height AS f2_height",
        "b2.reach AS f2_reach",
        "(b1.height - b2.height) AS diff_height",
        "(b1.reach - b2.reach) AS diff_reach",
        "(b1.reach - b1.height) AS f1_ape_index",
        "(b2.reach - b2.height) AS f2_ape_index",
        "((b1.reach - b1.height) - (b2.reach - b2.height)) AS diff_ape_index",
    ]
    for col in FEATURE_EXPORT_COLS:
        selects.append(f"f1.{col} AS f1_{col}")
        selects.append(f"f2.{col} AS f2_{col}")
    for col in DIFF_COLS:
        selects.append(f"(f1.{col} - f2.{col}) AS diff_{col}")

    selects.extend(
        [
            "ff.method_group AS y_method_group",
            "ff.outcome_type AS y_outcome_type",
            "ff.winner_id AS y_winner_id",
            """CASE
                WHEN ff.winner_id = f1.fighter_id THEN 1
                WHEN ff.winner_id = f2.fighter_id THEN 0
                ELSE NULL
            END AS y_fighter1_win""",
            "ff1.result AS y_f1_result",
            "ff2.result AS y_f2_result",
        ]
    )
    for col in LABEL_FACT_COLS:
        selects.append(f"ff1.{col} AS y_f1_{col}")
        selects.append(f"ff2.{col} AS y_f2_{col}")

    sql = f"""
    CREATE OR REPLACE VIEW fight_model_rows AS
    SELECT
        {", ".join(selects)}
    FROM fighter_fight_features f1
    JOIN fighter_fight_features f2
      ON f1.fight_id = f2.fight_id
     AND f1.is_fighter1 IS TRUE
     AND f2.is_fighter1 IS FALSE
    JOIN fight_facts ff
      ON ff.fight_id = f1.fight_id
    JOIN fighter_fight_facts ff1
      ON ff1.fight_id = f1.fight_id AND ff1.fighter_id = f1.fighter_id
    JOIN fighter_fight_facts ff2
      ON ff2.fight_id = f2.fight_id AND ff2.fighter_id = f2.fighter_id
    LEFT JOIN fighter_bio b1 ON b1.fighter_id = f1.fighter_id
    LEFT JOIN fighter_bio b2 ON b2.fighter_id = f2.fighter_id
    """
    cursor.execute(sql)


def create_fight_model_train_view(cursor):
    """Two rows per fight: page order and swapped corners.

    Slot A/B are exchangeable. y_a_win is 1 iff slot A won. Use this view
    for training so the model cannot learn UFCStats winner-first page order.
    """
    shared = [
        "fight_id",
        "event_date",
        "weight_class_norm",
        "is_title",
        "is_women",
        "scheduled_rounds",
        "y_method_group",
        "y_outcome_type",
        "y_winner_id",
    ]

    def orientation_select(a_prefix, b_prefix, orientation, win_expr, negate_diff):
        selects = list(shared)
        selects.append(f"'{orientation}'::text AS orientation")
        selects.append(f"{a_prefix}_fighter_id AS a_fighter_id")
        selects.append(f"{b_prefix}_fighter_id AS b_fighter_id")
        selects.append(f"{a_prefix}_height AS a_height")
        selects.append(f"{b_prefix}_height AS b_height")
        selects.append(f"{a_prefix}_reach AS a_reach")
        selects.append(f"{b_prefix}_reach AS b_reach")
        if negate_diff:
            selects.append(f"-diff_height AS diff_height")
            selects.append(f"-diff_reach AS diff_reach")
            selects.append(f"-diff_ape_index AS diff_ape_index")
        else:
            selects.append("diff_height AS diff_height")
            selects.append("diff_reach AS diff_reach")
            selects.append("diff_ape_index AS diff_ape_index")
        selects.append(f"{a_prefix}_ape_index AS a_ape_index")
        selects.append(f"{b_prefix}_ape_index AS b_ape_index")
        for col in FEATURE_EXPORT_COLS:
            selects.append(f"{a_prefix}_{col} AS a_{col}")
            selects.append(f"{b_prefix}_{col} AS b_{col}")
        for col in DIFF_COLS:
            if negate_diff:
                selects.append(f"-diff_{col} AS diff_{col}")
            else:
                selects.append(f"diff_{col} AS diff_{col}")
        selects.append(f"{win_expr} AS y_a_win")
        for col in LABEL_FACT_COLS:
            selects.append(f"y_{a_prefix}_{col} AS y_a_{col}")
            selects.append(f"y_{b_prefix}_{col} AS y_b_{col}")
        for alias, expr in MATCHUP_PRODUCTS:
            selects.append(expr.format(a=a_prefix, b=b_prefix) + f" AS {alias}")
        return ", ".join(selects)

    page_sql = orientation_select(
        "f1",
        "f2",
        "page",
        "y_fighter1_win",
        False,
    )
    swap_sql = orientation_select(
        "f2",
        "f1",
        "swapped",
        "CASE WHEN y_fighter1_win IS NULL THEN NULL ELSE 1 - y_fighter1_win END",
        True,
    )
    sql = f"""
    CREATE OR REPLACE VIEW fight_model_train AS
    SELECT {page_sql} FROM fight_model_rows
    UNION ALL
    SELECT {swap_sql} FROM fight_model_rows
    """
    cursor.execute(sql)


def create_feature_tables(connection):
    sql = """
    CREATE TABLE IF NOT EXISTS fighter_fight_features (
        fighter_fight_feature_id SERIAL PRIMARY KEY,

        -- identity: which fighter, in which fight, facing whom
        fighter_id INTEGER NOT NULL REFERENCES fighters(fighter_id) ON DELETE CASCADE,
        fight_id INTEGER NOT NULL REFERENCES fights(fight_id) ON DELETE CASCADE,
        opponent_id INTEGER REFERENCES fighters(fighter_id) ON DELETE SET NULL,
        event_date DATE NOT NULL,
        is_fighter1 BOOLEAN,

        -- activity / momentum window (trailing 18 months)
        days_since_last_fight INTEGER,
        fights_in_last_18mo INTEGER,
        fights_in_last_24mo INTEGER,
        stale_flag BOOLEAN,
        used_career_fallback_momentum BOOLEAN,
        used_career_fallback_style BOOLEAN,

        -- momentum-window (18mo) performance averages, computed only from
        -- fights strictly before event_date
        momentum_sig_str_landed_avg DOUBLE PRECISION,
        momentum_sig_str_attempted_avg DOUBLE PRECISION,
        momentum_sig_str_percent_avg DOUBLE PRECISION,
        momentum_td_landed_avg DOUBLE PRECISION,
        momentum_td_attempted_avg DOUBLE PRECISION,
        momentum_td_percent_avg DOUBLE PRECISION,

        -- career-to-date fallback values (used when window has 0 fights)
        career_sig_str_landed_avg DOUBLE PRECISION,
        career_sig_str_attempted_avg DOUBLE PRECISION,
        career_sig_str_percent_avg DOUBLE PRECISION,
        career_td_landed_avg DOUBLE PRECISION,
        career_td_attempted_avg DOUBLE PRECISION,
        career_td_percent_avg DOUBLE PRECISION,
        career_fights_count INTEGER,

        -- room to grow: style clusters, matchup diffs, cardio-decay, etc.
        -- land here as typed columns in later migrations, with a JSONB
        -- escape hatch for anything not yet promoted to its own column
        raw_features JSONB,

        created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),

        CONSTRAINT unique_fighter_fight UNIQUE (fighter_id, fight_id)
    );
    """

    index_statements = [
        "CREATE INDEX IF NOT EXISTS idx_fff_fighter_id ON fighter_fight_features(fighter_id)",
        "CREATE INDEX IF NOT EXISTS idx_fff_fight_id ON fighter_fight_features(fight_id)",
        "CREATE INDEX IF NOT EXISTS idx_fff_event_date ON fighter_fight_features(event_date)",
    ]

    cursor = connection.cursor()
    try:
        cursor.execute(sql)
        for stmt in index_statements:
            cursor.execute(stmt)
        # Defensive migrations for tables created by earlier versions.
        alter_statements = [
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS result TEXT",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS win_streak INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS loss_streak INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last_3_wins INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last_3_losses INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last_5_wins INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last_5_losses INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_wins INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_losses INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_draws INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_no_contests INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_finish_rate DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_decision_rate DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_ko_rate DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_sub_rate DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_method_entropy DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_ko_rate DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_sub_rate DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_decision_rate DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_method_entropy DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_n_r3 INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_n_r3_complete INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_cardio_r3_r1_att_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_cardio_r3_r1_landed_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_late_td_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_late_kd_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_late_ctrl_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_n_r3 INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_n_r3_complete INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_cardio_r3_r1_att_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_cardio_r3_r1_landed_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_late_td_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_late_kd_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_late_ctrl_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last3_n_r3_complete INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last3_cardio_r3_r1_att_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS trend_last5_vs_career_cardio_r3_r1_att DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_n_distance INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_cardio_distance_att_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_cardio_distance_landed_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_cardio_consec_att_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_n_distance INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_cardio_distance_att_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_cardio_consec_att_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last3_cardio_distance_att_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS trend_last5_vs_career_cardio_distance_att DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_n_five_stoppage INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_cardio_five_stoppage_att_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_n_observed INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_cardio_observed_att_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_cardio_observed_att_avg DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_n_observed INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS trend_last5_vs_career_cardio_observed_att DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_win_pct DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_win_pct DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS opp_asof_win_pct DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS opp_asof_last5_win_pct DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS opp_asof_finish_rate DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS opp_asof_ko_rate DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS opp_asof_n_fights INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS opp_asof_n_decided INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS opp_asof_debut BOOLEAN",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_avg_opp_win_pct DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_avg_opp_last5_win_pct DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_avg_opp_finish_rate DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_avg_opp_n_fights DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_n_opp_known INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_avg_opp_win_pct DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_avg_opp_last5_win_pct DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_avg_opp_finish_rate DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_n_opp_known INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last3_avg_opp_win_pct DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS first_time_in_class BOOLEAN",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS n_prior_in_class INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS switched_class BOOLEAN",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS class_move INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS career_mix_entropy DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_mix_entropy DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS debut_flag BOOLEAN",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS num_five_round_fights INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS avg_rounds_fought DOUBLE PRECISION",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS num_title_fights INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last3_fights_count INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS last5_fights_count INTEGER",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS used_career_fallback_last3 BOOLEAN",
            "ALTER TABLE fighter_fight_features ADD COLUMN IF NOT EXISTS used_career_fallback_last5 BOOLEAN",
        ]
        for window in WINDOWS:
            for stat in STAT_COLS:
                alter_statements.append(
                    "ALTER TABLE fighter_fight_features "
                    f"ADD COLUMN IF NOT EXISTS {window_stat_col(window, stat)} DOUBLE PRECISION"
                )
        for newer, older in TREND_PAIRS:
            for stat in STAT_COLS:
                alter_statements.append(
                    "ALTER TABLE fighter_fight_features "
                    f"ADD COLUMN IF NOT EXISTS {trend_col(newer, older, stat)} DOUBLE PRECISION"
                )
        for stmt in alter_statements:
            cursor.execute(stmt)
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name = 'fight_facts'
            )
            """
        )
        if cursor.fetchone()[0]:
            cursor.execute("DROP VIEW IF EXISTS fight_model_train")
            cursor.execute("DROP VIEW IF EXISTS fight_model_rows CASCADE")
            create_fight_model_view(cursor)
            create_fight_model_train_view(cursor)
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
            create_feature_tables(conn)
            print("fighter_fight_features table created successfully.")
        finally:
            conn.close()
    except Exception as exc:
        print(f"Failed to connect to PostgreSQL: {exc}")