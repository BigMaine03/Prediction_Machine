"""
build_fighter_features.py

Point-in-time pre-fight features from Layer 2 facts.

Pipeline:
  load_fighter_fight_facts(connection) -> long DataFrame (1 row per fighter per fight)
  compute_momentum_features(long_df)   -> career / 18mo / last-3 / last-5 + trends
  compute_tier2_features(long_df)      -> streaks, records, finish rates
  upsert_features(connection, long_df) -> fighter_fight_features

Rules:
- Every feature for fight T uses only facts with event_date strictly before T.
- NC / DQ count toward fight frequency but are excluded from performance averages
  (is_valid_for_stats on facts, driven by method_group / outcome_type).
- 18mo window with career fallback; stale_flag if last fight > 365 days ago.
- result on this table is a this-fight LABEL for debugging; the model view
  (fight_model_rows) uses facts for y_* and does not treat result as X.
- Writes always ON CONFLICT (fighter_id, fight_id) DO UPDATE.
"""

import os
import sys

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Database.create_feature_tables import create_feature_tables
from Database.db import get_connection
from Features.feature_columns import (
    HISTORY_FEATURE_COLS,
    STAT_COLS,
    TREND_PAIRS,
    WINDOWS,
    trend_col,
    window_stat_col,
)
from Features.stat_parse import (
    method_mix_and_entropy,
    nan_to_none,
    none_or_int,
    style_entropy_series,
)

INT_FEATURE_COLS = {
    "fighter_id",
    "fight_id",
    "opponent_id",
    "days_since_last_fight",
    "fights_in_last_18mo",
    "fights_in_last_24mo",
    "career_fights_count",
    "last3_fights_count",
    "last5_fights_count",
    "win_streak",
    "loss_streak",
    "last_3_wins",
    "last_3_losses",
    "last_5_wins",
    "last_5_losses",
    "career_wins",
    "career_losses",
    "career_draws",
    "career_no_contests",
    "num_five_round_fights",
    "num_title_fights",
    "career_n_r3",
    "career_n_r3_complete",
    "last5_n_r3",
    "last5_n_r3_complete",
    "last3_n_r3_complete",
    "career_n_distance",
    "last5_n_distance",
    "career_n_five_stoppage",
    "career_n_observed",
    "last5_n_observed",
    "opp_asof_n_fights",
    "opp_asof_n_decided",
    "career_n_opp_known",
    "last5_n_opp_known",
    "n_prior_in_class",
    "class_move",
}


MOMENTUM_WINDOW_DAYS = 18 * 30
STALE_THRESHOLD_DAYS = 365

UPSERT_IDENTITY_COLS = [
    "fighter_id",
    "fight_id",
    "opponent_id",
    "event_date",
    "is_fighter1",
    "result",
] + [c for c in HISTORY_FEATURE_COLS]


def _upsert_columns():
    cols = list(UPSERT_IDENTITY_COLS)
    for window in WINDOWS:
        for stat in STAT_COLS:
            cols.append(window_stat_col(window, stat))
    for newer, older in TREND_PAIRS:
        for stat in STAT_COLS:
            cols.append(trend_col(newer, older, stat))
    # unique, preserve order
    seen = set()
    ordered = []
    for col in cols:
        if col not in seen:
            seen.add(col)
            ordered.append(col)
    return ordered


def load_fighter_fight_facts(connection):
    """Long-format history: one row per fighter per fight, already numeric."""
    query = """
    SELECT
        fff.fighter_id,
        fff.fight_id,
        fff.opponent_id,
        fff.is_fighter1,
        fff.event_date,
        fff.result,
        fff.is_valid_for_stats,
        fff.method_group,
        fff.final_round,
        fff.fight_seconds,
        ff.winner_id,
        ff.outcome_type,
        ff.method_raw AS method,
        ff.scheduled_rounds,
        ff.is_title,
        ff.weight_class_norm,
        ff.time_format_raw AS time_format,
        fff.sig_str_landed,
        fff.sig_str_attempted,
        fff.sig_str_percent,
        fff.td_landed,
        fff.td_attempted,
        fff.td_percent,
        fff.kd,
        fff.sub_att,
        fff.rev,
        fff.ctrl_seconds,
        fff.distance_landed,
        fff.clinch_landed,
        fff.ground_landed,
        fff.distance_pct,
        fff.clinch_pct,
        fff.ground_pct,
        fff.sig_str_absorbed,
        fff.strike_defense,
        fff.td_absorbed,
        fff.td_defense,
        fff.kd_absorbed,
        fff.ctrl_against_seconds,
        fff.sig_str_diff,
        fff.td_diff,
        fff.ctrl_diff,
        fff.sig_str_landed_pm,
        fff.sig_str_absorbed_pm,
        fff.sig_str_attempted_pm,
        fff.td_landed_pm,
        fff.td_attempted_pm,
        fff.kd_pm,
        fff.sub_att_pm,
        fff.ctrl_seconds_pm,
        fff.pace,
        fff.r1_sig_str_attempted,
        fff.r3_sig_str_attempted,
        fff.reached_round_3,
        fff.completed_round_3,
        fff.cardio_r3_r1_att,
        fff.cardio_r3_r1_landed,
        fff.late_td_landed,
        fff.late_kd,
        fff.late_ctrl_seconds,
        fff.went_the_distance,
        fff.cardio_distance_att,
        fff.cardio_distance_landed,
        fff.cardio_consec_att,
        fff.completed_round_5,
        fff.last_completed_round,
        fff.cardio_last_full_r1_att,
        fff.cardio_five_stoppage_att,
        fff.cardio_observed_att
    FROM fighter_fight_facts fff
    JOIN fight_facts ff ON ff.fight_id = fff.fight_id
    WHERE fff.event_date IS NOT NULL
    """
    df = pd.read_sql(query, connection)
    if df.empty:
        raise RuntimeError(
            "fighter_fight_facts is empty. Run Features/build_fight_facts.py first."
        )
    df["event_date"] = pd.to_datetime(df["event_date"])
    df["is_valid_for_stats"] = df["is_valid_for_stats"].fillna(False).astype(bool)
    df["is_title"] = df["is_title"].fillna(False).astype(bool)
    if "reached_round_3" in df.columns:
        df["reached_round_3"] = df["reached_round_3"].fillna(False).astype(bool)
    if "completed_round_3" in df.columns:
        df["completed_round_3"] = df["completed_round_3"].fillna(False).astype(bool)
    if "went_the_distance" in df.columns:
        df["went_the_distance"] = df["went_the_distance"].fillna(False).astype(bool)
    df = add_derived_stat_cols(df)
    return df.sort_values(["fighter_id", "event_date"]).reset_index(drop=True)


def add_derived_stat_cols(df):
    """Per-fight style entropy and TD funnel, then rolled by compute_momentum_features."""
    out = df.copy()
    out["style_entropy"] = style_entropy_series(
        out.get("distance_pct"), out.get("clinch_pct"), out.get("ground_pct")
    )
    td = pd.to_numeric(out.get("td_landed"), errors="coerce")
    ctrl = pd.to_numeric(out.get("ctrl_seconds"), errors="coerce")
    ground = pd.to_numeric(out.get("ground_landed"), errors="coerce")
    sub = pd.to_numeric(out.get("sub_att"), errors="coerce")
    out["ctrl_per_td"] = np.where(td > 0, ctrl / td, np.nan)
    out["ground_per_ctrl_min"] = np.where(ctrl > 0, ground / (ctrl / 60.0), np.nan)
    out["sub_per_td"] = np.where(td > 0, sub / td, np.nan)
    return out


CLASS_RANK = {
    "Flyweight": 0,
    "Bantamweight": 1,
    "Featherweight": 2,
    "Lightweight": 3,
    "Welterweight": 4,
    "Middleweight": 5,
    "Light Heavyweight": 6,
    "Heavyweight": 7,
    "Women's Strawweight": 0,
    "Women's Flyweight": 1,
    "Women's Bantamweight": 2,
    "Women's Featherweight": 3,
}


def compute_class_and_mix_features(long_df):
    """First-time-in-class (pre-fight) and entropy of career/last-5 style mix."""
    out_frames = []
    has_class = "weight_class_norm" in long_df.columns
    for fighter_id, group in long_df.groupby("fighter_id", sort=False):
        group = group.sort_values("event_date").reset_index(drop=True)
        n = len(group)
        first_time = [False] * n
        n_prior_in = [0] * n
        switched = [False] * n
        class_move = [np.nan] * n
        if has_class:
            for i in range(n):
                this_class = group.loc[i, "weight_class_norm"]
                prior = group.loc[: i - 1] if i > 0 else group.iloc[0:0]
                if pd.isna(this_class) or this_class is None or str(this_class).strip() == "":
                    first_time[i] = True
                    continue
                in_class = prior["weight_class_norm"] == this_class
                n_prior_in[i] = int(in_class.sum())
                first_time[i] = n_prior_in[i] == 0
                if len(prior) == 0:
                    continue
                last_class = prior.iloc[-1]["weight_class_norm"]
                if pd.isna(last_class) or last_class is None:
                    continue
                switched[i] = str(last_class) != str(this_class)
                r_this = CLASS_RANK.get(str(this_class))
                r_last = CLASS_RANK.get(str(last_class))
                if r_this is not None and r_last is not None:
                    if r_this > r_last:
                        class_move[i] = 1
                    elif r_this < r_last:
                        class_move[i] = -1
                    else:
                        class_move[i] = 0
        extra = {
            "first_time_in_class": first_time,
            "n_prior_in_class": n_prior_in,
            "switched_class": switched,
            "class_move": class_move,
        }
        if "career_distance_pct_avg" in group.columns:
            extra["career_mix_entropy"] = style_entropy_series(
                group["career_distance_pct_avg"],
                group["career_clinch_pct_avg"],
                group["career_ground_pct_avg"],
            )
            extra["last5_mix_entropy"] = style_entropy_series(
                group["last5_distance_pct_avg"],
                group["last5_clinch_pct_avg"],
                group["last5_ground_pct_avg"],
            )
        extra_df = pd.DataFrame(extra, index=group.index)
        group = pd.concat([group, extra_df], axis=1)
        out_frames.append(group)
    return pd.concat(out_frames, ignore_index=True)


def _window_means(valid_df):
    if len(valid_df) == 0:
        return {c: np.nan for c in STAT_COLS}
    return {c: valid_df[c].mean() if c in valid_df.columns else np.nan for c in STAT_COLS}


def compute_momentum_features(long_df):
    """For every (fighter, fight) row, averages from fights strictly before event_date.

    Windows: career, 18-month momentum, last 3 valid fights, last 5 valid fights.
    Last-N empty -> copy career and set used_career_fallback_lastN.
    Trends are last5-career and last3-last5 (can be NaN if either side is NaN).
    """
    out_frames = []

    for fighter_id, group in long_df.groupby("fighter_id", sort=False):
        group = group.sort_values("event_date").reset_index(drop=True)
        n = len(group)

        days_since_last_fight = [np.nan] * n
        fights_18mo = [0] * n
        fights_24mo = [0] * n
        stale_flag = [False] * n
        used_fallback_momentum = [False] * n
        used_fallback_last3 = [False] * n
        used_fallback_last5 = [False] * n
        career_count = [0] * n
        last3_count = [0] * n
        last5_count = [0] * n
        window_store = {w: {c: [np.nan] * n for c in STAT_COLS} for w in WINDOWS}

        for i in range(n):
            current_date = group.loc[i, "event_date"]
            prior = group.loc[: i - 1] if i > 0 else group.iloc[0:0]

            if len(prior) > 0:
                last_date = prior["event_date"].max()
                days_since_last_fight[i] = (current_date - last_date).days
                stale_flag[i] = days_since_last_fight[i] > STALE_THRESHOLD_DAYS

            window_18 = prior[prior["event_date"] >= current_date - pd.Timedelta(days=MOMENTUM_WINDOW_DAYS)]
            window_24 = prior[prior["event_date"] >= current_date - pd.Timedelta(days=24 * 30)]
            fights_18mo[i] = len(window_18)
            fights_24mo[i] = len(window_24)

            valid_prior = prior[prior["is_valid_for_stats"]]
            valid_window_18 = window_18[window_18["is_valid_for_stats"]]
            valid_last3 = valid_prior.tail(3)
            valid_last5 = valid_prior.tail(5)

            career_count[i] = len(valid_prior)
            last3_count[i] = len(valid_last3)
            last5_count[i] = len(valid_last5)

            career_means = _window_means(valid_prior)
            last3_means = _window_means(valid_last3)
            last5_means = _window_means(valid_last5)
            mom_means = _window_means(valid_window_18)

            if len(valid_window_18) == 0:
                used_fallback_momentum[i] = True
                mom_means = dict(career_means)
            if len(valid_last3) == 0:
                used_fallback_last3[i] = True
                last3_means = dict(career_means)
            if len(valid_last5) == 0:
                used_fallback_last5[i] = True
                last5_means = dict(career_means)

            for c in STAT_COLS:
                window_store["career"][c][i] = career_means[c]
                window_store["momentum"][c][i] = mom_means[c]
                window_store["last3"][c][i] = last3_means[c]
                window_store["last5"][c][i] = last5_means[c]

        extra = {
            "days_since_last_fight": days_since_last_fight,
            "fights_in_last_18mo": fights_18mo,
            "fights_in_last_24mo": fights_24mo,
            "stale_flag": stale_flag,
            "used_career_fallback_momentum": used_fallback_momentum,
            "used_career_fallback_last3": used_fallback_last3,
            "used_career_fallback_last5": used_fallback_last5,
            "career_fights_count": career_count,
            "last3_fights_count": last3_count,
            "last5_fights_count": last5_count,
        }
        for window in WINDOWS:
            for c in STAT_COLS:
                extra[window_stat_col(window, c)] = window_store[window][c]
        for newer, older in TREND_PAIRS:
            for c in STAT_COLS:
                extra[trend_col(newer, older, c)] = (
                    np.asarray(window_store[newer][c], dtype=float)
                    - np.asarray(window_store[older][c], dtype=float)
                )
        extra_df = pd.DataFrame(extra, index=group.index)
        group = pd.concat([group, extra_df], axis=1)
        out_frames.append(group)

    return pd.concat(out_frames, ignore_index=True)


def _row_result(row):
    value = row.get("result")
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    text = str(value).strip().upper()
    return text if text in ("W", "L", "D", "NC") else None


def compute_tier2_features(long_df):
    """History features from prior fights only. result on the current row is this fight's label."""
    out_frames = []

    for fighter_id, group in long_df.groupby("fighter_id", sort=False):
        group = group.sort_values("event_date").reset_index(drop=True)
        n = len(group)

        win_streak = [0] * n
        loss_streak = [0] * n
        last_3_wins = [0] * n
        last_3_losses = [0] * n
        last_5_wins = [0] * n
        last_5_losses = [0] * n
        career_wins = [0] * n
        career_losses = [0] * n
        career_draws = [0] * n
        career_no_contests = [0] * n
        career_finish_rate = [None] * n
        career_decision_rate = [None] * n
        career_ko_rate = [None] * n
        career_sub_rate = [None] * n
        career_method_entropy = [None] * n
        last5_ko_rate = [None] * n
        last5_sub_rate = [None] * n
        last5_decision_rate = [None] * n
        last5_method_entropy = [None] * n
        debut_flag = [False] * n
        num_five_round_fights = [0] * n
        num_title_fights = [0] * n
        avg_rounds_fought = [None] * n

        for i in range(n):
            prior = group.loc[: i - 1] if i > 0 else group.iloc[0:0]

            if len(prior) == 0:
                debut_flag[i] = True
                continue

            prior_results = [_row_result(pr) for _, pr in prior.iterrows()]

            career_wins[i] = sum(1 for r in prior_results if r == "W")
            career_losses[i] = sum(1 for r in prior_results if r == "L")
            career_draws[i] = sum(1 for r in prior_results if r == "D")
            career_no_contests[i] = sum(1 for r in prior_results if r == "NC")

            ws = 0
            for r in reversed(prior_results):
                if r == "W":
                    ws += 1
                else:
                    break
            win_streak[i] = ws

            ls = 0
            for r in reversed(prior_results):
                if r == "L":
                    ls += 1
                else:
                    break
            loss_streak[i] = ls

            last_3 = prior_results[-3:]
            last_5 = prior_results[-5:]
            last_3_wins[i] = sum(1 for r in last_3 if r == "W")
            last_3_losses[i] = sum(1 for r in last_3 if r == "L")
            last_5_wins[i] = sum(1 for r in last_5 if r == "W")
            last_5_losses[i] = sum(1 for r in last_5 if r == "L")

            win_mask = [r == "W" for r in prior_results]
            win_methods = []
            finishes = 0
            decisions = 0
            win_count = 0
            for idx, is_win in enumerate(win_mask):
                if not is_win:
                    continue
                win_count += 1
                group_name = prior.iloc[idx].get("method_group")
                win_methods.append(group_name)
                if group_name == "DEC":
                    decisions += 1
                elif group_name in ("KO_TKO", "SUB"):
                    finishes += 1
            if win_count == 0:
                career_finish_rate[i] = None
                career_decision_rate[i] = None
            else:
                career_finish_rate[i] = finishes / win_count
                career_decision_rate[i] = decisions / win_count

            mix = method_mix_and_entropy(win_methods)
            career_ko_rate[i] = mix["ko_rate"]
            career_sub_rate[i] = mix["sub_rate"]
            career_method_entropy[i] = mix["entropy_norm"]

            last5_win_methods = [
                prior.iloc[idx].get("method_group")
                for idx, r in enumerate(prior_results)
                if r == "W" and idx >= len(prior_results) - 5
            ]
            last5_mix = method_mix_and_entropy(last5_win_methods)
            last5_ko_rate[i] = last5_mix["ko_rate"]
            last5_sub_rate[i] = last5_mix["sub_rate"]
            last5_decision_rate[i] = last5_mix["dec_rate"]
            last5_method_entropy[i] = last5_mix["entropy_norm"]

            scheduled = pd.to_numeric(prior["scheduled_rounds"], errors="coerce")
            num_five_round_fights[i] = int((scheduled == 5).sum())
            num_title_fights[i] = int(prior["is_title"].sum())
            rounds_list = pd.to_numeric(prior["final_round"], errors="coerce").dropna()
            avg_rounds_fought[i] = float(rounds_list.mean()) if len(rounds_list) else None

        group["win_streak"] = win_streak
        group["loss_streak"] = loss_streak
        group["last_3_wins"] = last_3_wins
        group["last_3_losses"] = last_3_losses
        group["last_5_wins"] = last_5_wins
        group["last_5_losses"] = last_5_losses
        group["career_wins"] = career_wins
        group["career_losses"] = career_losses
        group["career_draws"] = career_draws
        group["career_no_contests"] = career_no_contests
        group["career_finish_rate"] = career_finish_rate
        group["career_decision_rate"] = career_decision_rate
        group["career_ko_rate"] = career_ko_rate
        group["career_sub_rate"] = career_sub_rate
        group["career_method_entropy"] = career_method_entropy
        group["last5_ko_rate"] = last5_ko_rate
        group["last5_sub_rate"] = last5_sub_rate
        group["last5_decision_rate"] = last5_decision_rate
        group["last5_method_entropy"] = last5_method_entropy
        group["debut_flag"] = debut_flag
        group["num_five_round_fights"] = num_five_round_fights
        group["num_title_fights"] = num_title_fights
        group["avg_rounds_fought"] = avg_rounds_fought

        out_frames.append(group)

    return pd.concat(out_frames, ignore_index=True)


def _mean_or_nan(series):
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.mean()) if len(values) else np.nan


def compute_cardio_features(long_df):
    """Pre-fight cardio from prior fights only.

    Ratios use completed round-3 fights (full or nearly full R3) so a 20-second
    R3 finish is not treated as a gas-tank collapse. n_r3 counts fights that
    at least started round 3. Late TD/KD/ctrl use any fight that reached R3.
    """
    extra_names = [
        "career_n_r3",
        "career_n_r3_complete",
        "career_cardio_r3_r1_att_avg",
        "career_cardio_r3_r1_landed_avg",
        "career_late_td_avg",
        "career_late_kd_avg",
        "career_late_ctrl_avg",
        "last5_n_r3",
        "last5_n_r3_complete",
        "last5_cardio_r3_r1_att_avg",
        "last5_cardio_r3_r1_landed_avg",
        "last5_late_td_avg",
        "last5_late_kd_avg",
        "last5_late_ctrl_avg",
        "last3_n_r3_complete",
        "last3_cardio_r3_r1_att_avg",
        "trend_last5_vs_career_cardio_r3_r1_att",
        "career_n_distance",
        "career_cardio_distance_att_avg",
        "career_cardio_distance_landed_avg",
        "career_cardio_consec_att_avg",
        "last5_n_distance",
        "last5_cardio_distance_att_avg",
        "last5_cardio_consec_att_avg",
        "last3_cardio_distance_att_avg",
        "trend_last5_vs_career_cardio_distance_att",
        "career_n_five_stoppage",
        "career_cardio_five_stoppage_att_avg",
        "career_n_observed",
        "career_cardio_observed_att_avg",
        "last5_cardio_observed_att_avg",
        "last5_n_observed",
        "trend_last5_vs_career_cardio_observed_att",
    ]
    out_frames = []
    has_cardio = "completed_round_3" in long_df.columns
    for fighter_id, group in long_df.groupby("fighter_id", sort=False):
        group = group.sort_values("event_date").reset_index(drop=True)
        n = len(group)
        extra = {name: [np.nan] * n for name in extra_names}
        extra["career_n_r3"] = [0] * n
        extra["career_n_r3_complete"] = [0] * n
        extra["last5_n_r3"] = [0] * n
        extra["last5_n_r3_complete"] = [0] * n
        extra["last3_n_r3_complete"] = [0] * n
        extra["career_n_distance"] = [0] * n
        extra["last5_n_distance"] = [0] * n
        extra["career_n_five_stoppage"] = [0] * n
        extra["career_n_observed"] = [0] * n
        extra["last5_n_observed"] = [0] * n

        if has_cardio:
            for i in range(n):
                prior = group.loc[: i - 1] if i > 0 else group.iloc[0:0]
                if len(prior) == 0:
                    continue
                valid = prior[prior["is_valid_for_stats"]]
                reached = valid[valid["reached_round_3"]]
                complete = valid[valid["completed_round_3"]]

                extra["career_n_r3"][i] = int(len(reached))
                extra["career_n_r3_complete"][i] = int(len(complete))
                career_att = _mean_or_nan(complete["cardio_r3_r1_att"])
                extra["career_cardio_r3_r1_att_avg"][i] = career_att
                extra["career_cardio_r3_r1_landed_avg"][i] = _mean_or_nan(complete["cardio_r3_r1_landed"])
                extra["career_late_td_avg"][i] = _mean_or_nan(reached["late_td_landed"])
                extra["career_late_kd_avg"][i] = _mean_or_nan(reached["late_kd"])
                extra["career_late_ctrl_avg"][i] = _mean_or_nan(reached["late_ctrl_seconds"])

                last5_reached = reached.tail(5)
                last5_complete = complete.tail(5)
                last3_complete = complete.tail(3)
                extra["last5_n_r3"][i] = int(len(last5_reached))
                extra["last5_n_r3_complete"][i] = int(len(last5_complete))
                last5_att = _mean_or_nan(last5_complete["cardio_r3_r1_att"])
                extra["last5_cardio_r3_r1_att_avg"][i] = last5_att
                extra["last5_cardio_r3_r1_landed_avg"][i] = _mean_or_nan(last5_complete["cardio_r3_r1_landed"])
                extra["last5_late_td_avg"][i] = _mean_or_nan(last5_reached["late_td_landed"])
                extra["last5_late_kd_avg"][i] = _mean_or_nan(last5_reached["late_kd"])
                extra["last5_late_ctrl_avg"][i] = _mean_or_nan(last5_reached["late_ctrl_seconds"])
                extra["last3_n_r3_complete"][i] = int(len(last3_complete))
                extra["last3_cardio_r3_r1_att_avg"][i] = _mean_or_nan(last3_complete["cardio_r3_r1_att"])
                extra["trend_last5_vs_career_cardio_r3_r1_att"][i] = (
                    last5_att - career_att
                    if (last5_att == last5_att and career_att == career_att)
                    else np.nan
                )

                distance = valid[valid["went_the_distance"]] if "went_the_distance" in valid.columns else complete
                extra["career_n_distance"][i] = int(len(distance))
                career_dist = _mean_or_nan(distance["cardio_distance_att"]) if "cardio_distance_att" in distance.columns else np.nan
                extra["career_cardio_distance_att_avg"][i] = career_dist
                extra["career_cardio_distance_landed_avg"][i] = (
                    _mean_or_nan(distance["cardio_distance_landed"])
                    if "cardio_distance_landed" in distance.columns
                    else np.nan
                )
                extra["career_cardio_consec_att_avg"][i] = (
                    _mean_or_nan(distance["cardio_consec_att"])
                    if "cardio_consec_att" in distance.columns
                    else np.nan
                )
                last5_distance = distance.tail(5)
                last3_distance = distance.tail(3)
                extra["last5_n_distance"][i] = int(len(last5_distance))
                last5_dist = (
                    _mean_or_nan(last5_distance["cardio_distance_att"])
                    if "cardio_distance_att" in last5_distance.columns
                    else np.nan
                )
                extra["last5_cardio_distance_att_avg"][i] = last5_dist
                extra["last5_cardio_consec_att_avg"][i] = (
                    _mean_or_nan(last5_distance["cardio_consec_att"])
                    if "cardio_consec_att" in last5_distance.columns
                    else np.nan
                )
                extra["last3_cardio_distance_att_avg"][i] = (
                    _mean_or_nan(last3_distance["cardio_distance_att"])
                    if "cardio_distance_att" in last3_distance.columns
                    else np.nan
                )
                extra["trend_last5_vs_career_cardio_distance_att"][i] = (
                    last5_dist - career_dist
                    if (last5_dist == last5_dist and career_dist == career_dist)
                    else np.nan
                )

                stoppage = valid[valid["cardio_five_stoppage_att"].notna()] if "cardio_five_stoppage_att" in valid.columns else valid.iloc[0:0]
                extra["career_n_five_stoppage"][i] = int(len(stoppage))
                extra["career_cardio_five_stoppage_att_avg"][i] = (
                    _mean_or_nan(stoppage["cardio_five_stoppage_att"]) if len(stoppage) else np.nan
                )
                observed = valid[valid["cardio_observed_att"].notna()] if "cardio_observed_att" in valid.columns else valid.iloc[0:0]
                extra["career_n_observed"][i] = int(len(observed))
                career_obs = _mean_or_nan(observed["cardio_observed_att"]) if len(observed) else np.nan
                extra["career_cardio_observed_att_avg"][i] = career_obs
                last5_obs = observed.tail(5)
                extra["last5_n_observed"][i] = int(len(last5_obs))
                last5_obs_avg = _mean_or_nan(last5_obs["cardio_observed_att"]) if len(last5_obs) else np.nan
                extra["last5_cardio_observed_att_avg"][i] = last5_obs_avg
                extra["trend_last5_vs_career_cardio_observed_att"][i] = (
                    last5_obs_avg - career_obs
                    if (last5_obs_avg == last5_obs_avg and career_obs == career_obs)
                    else np.nan
                )

        extra_df = pd.DataFrame(extra, index=group.index)
        group = pd.concat([group, extra_df], axis=1)
        out_frames.append(group)
    return pd.concat(out_frames, ignore_index=True)


def _win_pct(wins, losses):
    wins = pd.to_numeric(wins, errors="coerce")
    losses = pd.to_numeric(losses, errors="coerce")
    decided = wins + losses
    return np.where(decided > 0, wins / decided, np.nan)


def attach_opponent_asof(long_df):
    """Join the opponent's pre-fight row on the same fight_id.

    Opponent quality is their career/last-5 *before this fight* — no look-ahead.
    A debut opponent has opp_asof_win_pct NULL and opp_asof_debut True.
    """
    out = long_df.copy()
    out["career_win_pct"] = _win_pct(out["career_wins"], out["career_losses"])
    out["last5_win_pct"] = _win_pct(out["last_5_wins"], out["last_5_losses"])

    opp = out[
        [
            "fight_id",
            "fighter_id",
            "career_win_pct",
            "last5_win_pct",
            "career_wins",
            "career_losses",
            "career_draws",
            "career_no_contests",
            "career_finish_rate",
            "career_ko_rate",
            "debut_flag",
        ]
    ].rename(
        columns={
            "fighter_id": "_opp_id",
            "career_win_pct": "opp_asof_win_pct",
            "last5_win_pct": "opp_asof_last5_win_pct",
            "career_wins": "_opp_wins",
            "career_losses": "_opp_losses",
            "career_draws": "_opp_draws",
            "career_no_contests": "_opp_nc",
            "career_finish_rate": "opp_asof_finish_rate",
            "career_ko_rate": "opp_asof_ko_rate",
            "debut_flag": "opp_asof_debut",
        }
    )
    out = out.merge(
        opp,
        left_on=["fight_id", "opponent_id"],
        right_on=["fight_id", "_opp_id"],
        how="left",
    )
    wins = pd.to_numeric(out["_opp_wins"], errors="coerce").fillna(0)
    losses = pd.to_numeric(out["_opp_losses"], errors="coerce").fillna(0)
    draws = pd.to_numeric(out["_opp_draws"], errors="coerce").fillna(0)
    ncs = pd.to_numeric(out["_opp_nc"], errors="coerce").fillna(0)
    out["opp_asof_n_decided"] = (wins + losses).astype(int)
    out["opp_asof_n_fights"] = (wins + losses + draws + ncs).astype(int)
    if "opp_asof_debut" in out.columns:
        out["opp_asof_debut"] = out["opp_asof_debut"].fillna(False).astype(bool)
    return out.drop(columns=["_opp_id", "_opp_wins", "_opp_losses", "_opp_draws", "_opp_nc"], errors="ignore")


def compute_sos_features(long_df):
    """Strength of schedule: average opponent-as-of quality over *prior* fights."""
    extra_names = [
        "career_avg_opp_win_pct",
        "career_avg_opp_last5_win_pct",
        "career_avg_opp_finish_rate",
        "career_avg_opp_n_fights",
        "career_n_opp_known",
        "last5_avg_opp_win_pct",
        "last5_avg_opp_last5_win_pct",
        "last5_avg_opp_finish_rate",
        "last5_n_opp_known",
        "last3_avg_opp_win_pct",
    ]
    out_frames = []
    has_opp = "opp_asof_win_pct" in long_df.columns
    for fighter_id, group in long_df.groupby("fighter_id", sort=False):
        group = group.sort_values("event_date").reset_index(drop=True)
        n = len(group)
        extra = {name: [np.nan] * n for name in extra_names}
        extra["career_n_opp_known"] = [0] * n
        extra["last5_n_opp_known"] = [0] * n
        if has_opp:
            for i in range(n):
                prior = group.loc[: i - 1] if i > 0 else group.iloc[0:0]
                if len(prior) == 0:
                    continue
                last5 = prior.tail(5)
                last3 = prior.tail(3)
                extra["career_avg_opp_win_pct"][i] = _mean_or_nan(prior["opp_asof_win_pct"])
                extra["career_avg_opp_last5_win_pct"][i] = _mean_or_nan(prior["opp_asof_last5_win_pct"])
                extra["career_avg_opp_finish_rate"][i] = _mean_or_nan(prior["opp_asof_finish_rate"])
                extra["career_avg_opp_n_fights"][i] = _mean_or_nan(prior["opp_asof_n_fights"])
                extra["career_n_opp_known"][i] = int(prior["opp_asof_win_pct"].notna().sum())
                extra["last5_avg_opp_win_pct"][i] = _mean_or_nan(last5["opp_asof_win_pct"])
                extra["last5_avg_opp_last5_win_pct"][i] = _mean_or_nan(last5["opp_asof_last5_win_pct"])
                extra["last5_avg_opp_finish_rate"][i] = _mean_or_nan(last5["opp_asof_finish_rate"])
                extra["last5_n_opp_known"][i] = int(last5["opp_asof_win_pct"].notna().sum())
                extra["last3_avg_opp_win_pct"][i] = _mean_or_nan(last3["opp_asof_win_pct"])
        extra_df = pd.DataFrame(extra, index=group.index)
        group = pd.concat([group, extra_df], axis=1)
        out_frames.append(group)
    return pd.concat(out_frames, ignore_index=True)


def upsert_features(connection, features_df):
    columns = _upsert_columns()
    records = []
    for _, row in features_df.iterrows():
        values = []
        for col in columns:
            value = row.get(col)
            if col == "event_date" and pd.notnull(value):
                values.append(pd.Timestamp(value).date())
            elif col in INT_FEATURE_COLS:
                values.append(none_or_int(value))
            else:
                values.append(nan_to_none(value))
        records.append(tuple(values))

    assignments = ", ".join(f"{col} = EXCLUDED.{col}" for col in columns if col not in ("fighter_id", "fight_id"))
    sql = f"""
    INSERT INTO fighter_fight_features ({", ".join(columns)})
    VALUES %s
    ON CONFLICT (fighter_id, fight_id)
    DO UPDATE SET {assignments}
    """

    try:
        with connection.cursor() as cursor:
            execute_values(cursor, sql, records, page_size=500)
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def compute_pre_fight_features(long_df, verbose=True):
    """Point-in-time features for every row. Does not write to the database."""
    def _log(message):
        if verbose:
            print(message)

    _log("Computing momentum + career features (no look-ahead)...")
    featured = compute_momentum_features(long_df)
    _log("Computing class-change and style-mix entropy...")
    featured = compute_class_and_mix_features(featured)
    _log("Computing Tier 2 history features...")
    featured = compute_tier2_features(featured)
    _log("Attaching opponent quality as-of this fight...")
    featured = attach_opponent_asof(featured)
    _log("Computing strength-of-schedule (prior opponents)...")
    featured = compute_sos_features(featured)
    _log("Computing cardio features (censoring-aware R3/R1)...")
    featured = compute_cardio_features(featured)
    return featured


def run(connection=None):
    owns_connection = connection is None
    if owns_connection:
        connection = get_connection()
    try:
        print("Ensuring feature tables exist...")
        create_feature_tables(connection)

        print("Loading fighter-fight facts...")
        long_df = load_fighter_fight_facts(connection)
        print(f"  {len(long_df)} fighter-fight rows")

        featured = compute_pre_fight_features(long_df)

        print("Upserting into fighter_fight_features...")
        upsert_features(connection, featured)
        print("Done.")
        return len(featured)
    finally:
        if owns_connection:
            connection.close()


if __name__ == "__main__":
    run()
