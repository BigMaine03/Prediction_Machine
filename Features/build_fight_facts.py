"""
build_fight_facts.py

Rebuild Layer 2 numeric facts from raw ingest tables.

  load raw fights + totals + rounds
  parse TEXT stats once
  write fight_facts / fighter_fight_facts / fighter_round_facts

Full rebuild (TRUNCATE + INSERT) because this layer is derived and small.
"""

import os
import sys

import numpy as np
import pandas as pd
from psycopg2.extras import execute_values

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Database.create_fact_tables import create_fact_tables
from Database.db import get_connection
from Features.feature_columns import CARDIO_FACT_COLS, FACT_OPP_AND_RATE_COLS
from Features.stat_parse import (
    compute_fight_seconds,
    corner_result,
    is_valid_for_stats,
    method_group,
    nan_to_none,
    none_or_int,
    normalize_weight_class,
    parse_clock_to_seconds,
    parse_count,
    parse_time_format,
    parse_x_of_y,
    ratio_percent,
    share_percent,
    significant_strikes_from_raw_round,
)

INT_FACT_COLS = {
    "fight_id",
    "event_id",
    "winner_id",
    "fighter_id",
    "opponent_id",
    "scheduled_rounds",
    "final_round",
    "round_number",
    "last_completed_round",
}


X_OF_Y_TOTAL_COLS = [
    "sig_str",
    "distance",
    "clinch",
    "ground",
    "head",
    "body",
    "leg",
]

FIGHTER_FIGHT_FACT_COLS = [
    "fighter_id",
    "fight_id",
    "opponent_id",
    "is_fighter1",
    "event_date",
    "result",
    "is_valid_for_stats",
    "method_group",
    "final_round",
    "fight_seconds",
    "sig_str_landed",
    "sig_str_attempted",
    "sig_str_percent",
    "total_str_landed",
    "total_str_attempted",
    "td_landed",
    "td_attempted",
    "td_percent",
    "kd",
    "sub_att",
    "rev",
    "ctrl_seconds",
    "distance_landed",
    "distance_attempted",
    "clinch_landed",
    "clinch_attempted",
    "ground_landed",
    "ground_attempted",
    "head_landed",
    "head_attempted",
    "body_landed",
    "body_attempted",
    "leg_landed",
    "leg_attempted",
    "distance_pct",
    "clinch_pct",
    "ground_pct",
] + list(FACT_OPP_AND_RATE_COLS) + list(CARDIO_FACT_COLS)

ROUND_FACT_COLS = [
    "fight_id",
    "fighter_id",
    "round_number",
    "is_fighter1",
    "sig_str_landed",
    "sig_str_attempted",
    "total_str_landed",
    "total_str_attempted",
    "td_landed",
    "td_attempted",
    "kd",
    "sub_att",
    "rev",
    "ctrl_seconds",
    "distance_landed",
    "distance_attempted",
    "clinch_landed",
    "clinch_attempted",
    "ground_landed",
    "ground_attempted",
    "head_landed",
    "head_attempted",
    "body_landed",
    "body_attempted",
    "leg_landed",
    "leg_attempted",
]


def load_raw(connection):
    fights = pd.read_sql(
        """
        SELECT
            f.fight_id,
            f.event_id,
            e.event_date,
            f.weight_class,
            f.method,
            f.round AS final_round,
            f.time,
            f.time_format,
            f.fighter1_id,
            f.fighter2_id,
            f.winner_id,
            f.outcome_type,
            ft.fighter1_sig_str,
            ft.fighter2_sig_str,
            ft.fighter1_sig_str_percent,
            ft.fighter2_sig_str_percent,
            ft.fighter1_distance,
            ft.fighter2_distance,
            ft.fighter1_clinch,
            ft.fighter2_clinch,
            ft.fighter1_ground,
            ft.fighter2_ground,
            ft.fighter1_head,
            ft.fighter2_head,
            ft.fighter1_body,
            ft.fighter2_body,
            ft.fighter1_leg,
            ft.fighter2_leg
        FROM fights f
        JOIN events e ON f.event_id = e.event_id
        LEFT JOIN fight_totals ft ON f.fight_id = ft.fight_id
        WHERE e.event_date IS NOT NULL
          AND f.fighter1_id IS NOT NULL
          AND f.fighter2_id IS NOT NULL
        """,
        connection,
    )

    rounds = pd.read_sql(
        """
        SELECT
            fight_id,
            round_number,
            fighter1_kd, fighter2_kd,
            fighter1_sig_str, fighter2_sig_str,
            fighter1_total_str, fighter2_total_str,
            fighter1_td, fighter2_td,
            fighter1_sub_att, fighter2_sub_att,
            fighter1_rev, fighter2_rev,
            fighter1_ctrl, fighter2_ctrl,
            raw_round
        FROM round_stats
        """,
        connection,
    )
    return fights, rounds


def _sum_x_of_y(series):
    landed_total = 0.0
    attempted_total = 0.0
    any_valid = False
    for value in series:
        landed, attempted = parse_x_of_y(value)
        if not np.isnan(landed):
            landed_total += landed
            attempted_total += attempted
            any_valid = True
    if not any_valid:
        return (np.nan, np.nan)
    return (landed_total, attempted_total)


def _sum_count(series):
    total = 0.0
    any_valid = False
    for value in series:
        parsed = parse_count(value)
        if not np.isnan(parsed):
            total += parsed
            any_valid = True
    return total if any_valid else np.nan


def _sum_clock(series):
    total = 0.0
    any_valid = False
    for value in series:
        parsed = parse_clock_to_seconds(value)
        if not np.isnan(parsed):
            total += parsed
            any_valid = True
    return total if any_valid else np.nan


def aggregate_round_totals(rounds):
    """One row per fight with summed KD/TD/ctrl/sub/rev/total_str per corner."""
    if rounds.empty:
        return pd.DataFrame(columns=["fight_id"])

    rows = []
    for fight_id, group in rounds.groupby("fight_id", sort=False):
        row = {"fight_id": fight_id}
        for side in (1, 2):
            prefix = f"fighter{side}"
            row[f"{prefix}_kd_sum"] = _sum_count(group[f"{prefix}_kd"])
            row[f"{prefix}_sub_att_sum"] = _sum_count(group[f"{prefix}_sub_att"])
            row[f"{prefix}_rev_sum"] = _sum_count(group[f"{prefix}_rev"])
            row[f"{prefix}_ctrl_seconds_sum"] = _sum_clock(group[f"{prefix}_ctrl"])
            td_l, td_a = _sum_x_of_y(group[f"{prefix}_td"])
            row[f"{prefix}_td_landed_sum"] = td_l
            row[f"{prefix}_td_attempted_sum"] = td_a
            tot_l, tot_a = _sum_x_of_y(group[f"{prefix}_total_str"])
            row[f"{prefix}_total_str_landed_sum"] = tot_l
            row[f"{prefix}_total_str_attempted_sum"] = tot_a
            sig_l, sig_a = _sum_x_of_y(group[f"{prefix}_sig_str"])
            row[f"{prefix}_sig_str_landed_sum"] = sig_l
            row[f"{prefix}_sig_str_attempted_sum"] = sig_a
        rows.append(row)
    return pd.DataFrame(rows)


def build_fight_facts_frame(fights):
    records = []
    for _, row in fights.iterrows():
        norm, is_title, is_women = normalize_weight_class(row.get("weight_class"))
        group = method_group(row.get("method"), row.get("outcome_type"))
        valid = is_valid_for_stats(group=group)
        fmt = parse_time_format(row.get("time_format"))
        seconds = compute_fight_seconds(row.get("final_round"), row.get("time"), row.get("time_format"))
        records.append(
            {
                "fight_id": row["fight_id"],
                "event_id": row["event_id"],
                "event_date": row["event_date"],
                "weight_class_raw": row.get("weight_class"),
                "weight_class_norm": norm,
                "is_title": bool(is_title),
                "is_women": bool(is_women),
                "time_format_raw": row.get("time_format"),
                "scheduled_rounds": fmt["scheduled_rounds"],
                "final_round": row.get("final_round"),
                "fight_seconds": seconds,
                "method_raw": row.get("method"),
                "method_group": group,
                "outcome_type": row.get("outcome_type"),
                "winner_id": row.get("winner_id"),
                "is_valid_for_stats": bool(valid),
            }
        )
    frame = pd.DataFrame(records)
    frame["event_date"] = pd.to_datetime(frame["event_date"])
    return frame


def _corner_from_totals(fight_row, side):
    prefix = f"fighter{side}"
    parsed = {}
    for name in X_OF_Y_TOTAL_COLS:
        landed, attempted = parse_x_of_y(fight_row.get(f"{prefix}_{name}"))
        parsed[f"{name}_landed"] = landed
        parsed[f"{name}_attempted"] = attempted
    parsed["sig_str_percent"] = ratio_percent(parsed["sig_str_landed"], parsed["sig_str_attempted"])
    return parsed


def _corner_from_round_sums(sum_row, side):
    if sum_row is None:
        return {}
    prefix = f"fighter{side}"
    td_l = sum_row.get(f"{prefix}_td_landed_sum", np.nan)
    td_a = sum_row.get(f"{prefix}_td_attempted_sum", np.nan)
    return {
        "td_landed": td_l,
        "td_attempted": td_a,
        "td_percent": ratio_percent(td_l, td_a),
        "kd": sum_row.get(f"{prefix}_kd_sum", np.nan),
        "sub_att": sum_row.get(f"{prefix}_sub_att_sum", np.nan),
        "rev": sum_row.get(f"{prefix}_rev_sum", np.nan),
        "ctrl_seconds": sum_row.get(f"{prefix}_ctrl_seconds_sum", np.nan),
        "total_str_landed": sum_row.get(f"{prefix}_total_str_landed_sum", np.nan),
        "total_str_attempted": sum_row.get(f"{prefix}_total_str_attempted_sum", np.nan),
        "sig_str_landed_sum": sum_row.get(f"{prefix}_sig_str_landed_sum", np.nan),
        "sig_str_attempted_sum": sum_row.get(f"{prefix}_sig_str_attempted_sum", np.nan),
    }


def build_fighter_fight_facts_frame(fights, fight_facts, round_sums):
    sums_by_fight = {}
    if not round_sums.empty:
        sums_by_fight = round_sums.set_index("fight_id").to_dict(orient="index")

    facts_by_fight = fight_facts.set_index("fight_id").to_dict(orient="index")
    rows = []

    for _, fight in fights.iterrows():
        fight_id = fight["fight_id"]
        fact = facts_by_fight.get(fight_id)
        if fact is None:
            continue
        sum_row = sums_by_fight.get(fight_id)

        for side, fighter_id, opponent_id, is_fighter1 in (
            (1, fight["fighter1_id"], fight["fighter2_id"], True),
            (2, fight["fighter2_id"], fight["fighter1_id"], False),
        ):
            totals = _corner_from_totals(fight, side)
            from_rounds = _corner_from_round_sums(sum_row, side)

            sig_landed = totals["sig_str_landed"]
            sig_attempted = totals["sig_str_attempted"]
            if np.isnan(sig_landed):
                sig_landed = from_rounds.get("sig_str_landed_sum", np.nan)
                sig_attempted = from_rounds.get("sig_str_attempted_sum", np.nan)

            distance_landed = totals["distance_landed"]
            clinch_landed = totals["clinch_landed"]
            ground_landed = totals["ground_landed"]

            rows.append(
                {
                    "fighter_id": fighter_id,
                    "fight_id": fight_id,
                    "opponent_id": opponent_id,
                    "is_fighter1": is_fighter1,
                    "event_date": fact["event_date"],
                    "result": corner_result(fighter_id, fact.get("winner_id"), fact.get("outcome_type")),
                    "is_valid_for_stats": fact["is_valid_for_stats"],
                    "method_group": fact["method_group"],
                    "final_round": fact["final_round"],
                    "fight_seconds": fact["fight_seconds"],
                    "sig_str_landed": sig_landed,
                    "sig_str_attempted": sig_attempted,
                    "sig_str_percent": ratio_percent(sig_landed, sig_attempted),
                    "total_str_landed": from_rounds.get("total_str_landed", np.nan),
                    "total_str_attempted": from_rounds.get("total_str_attempted", np.nan),
                    "td_landed": from_rounds.get("td_landed", np.nan),
                    "td_attempted": from_rounds.get("td_attempted", np.nan),
                    "td_percent": from_rounds.get("td_percent", np.nan),
                    "kd": from_rounds.get("kd", np.nan),
                    "sub_att": from_rounds.get("sub_att", np.nan),
                    "rev": from_rounds.get("rev", np.nan),
                    "ctrl_seconds": from_rounds.get("ctrl_seconds", np.nan),
                    "distance_landed": distance_landed,
                    "distance_attempted": totals["distance_attempted"],
                    "clinch_landed": clinch_landed,
                    "clinch_attempted": totals["clinch_attempted"],
                    "ground_landed": ground_landed,
                    "ground_attempted": totals["ground_attempted"],
                    "head_landed": totals["head_landed"],
                    "head_attempted": totals["head_attempted"],
                    "body_landed": totals["body_landed"],
                    "body_attempted": totals["body_attempted"],
                    "leg_landed": totals["leg_landed"],
                    "leg_attempted": totals["leg_attempted"],
                    "distance_pct": share_percent(distance_landed, sig_landed),
                    "clinch_pct": share_percent(clinch_landed, sig_landed),
                    "ground_pct": share_percent(ground_landed, sig_landed),
                }
            )

    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["event_date"] = pd.to_datetime(frame["event_date"])
    return add_opponent_linked_and_rates(frame)


def _series_per_minute(count, seconds):
    count = pd.to_numeric(count, errors="coerce")
    seconds = pd.to_numeric(seconds, errors="coerce")
    minutes = seconds / 60.0
    return np.where((minutes > 0) & count.notna(), count / minutes, np.nan)


def add_opponent_linked_and_rates(frame):
    """Fill defense / absorbed / diffs from the other corner, then per-minute rates.

    Strike defense = 100 - opponent accuracy, NULL if opponent attempted 0.
    """
    if frame.empty:
        for col in FACT_OPP_AND_RATE_COLS:
            frame[col] = np.nan
        return frame

    opp = frame[
        [
            "fight_id",
            "fighter_id",
            "sig_str_landed",
            "sig_str_attempted",
            "td_landed",
            "td_attempted",
            "kd",
            "ctrl_seconds",
        ]
    ].rename(
        columns={
            "fighter_id": "opp_join_id",
            "sig_str_landed": "opp_sig_str_landed",
            "sig_str_attempted": "opp_sig_str_attempted",
            "td_landed": "opp_td_landed",
            "td_attempted": "opp_td_attempted",
            "kd": "opp_kd",
            "ctrl_seconds": "opp_ctrl_seconds",
        }
    )
    out = frame.merge(
        opp,
        left_on=["fight_id", "opponent_id"],
        right_on=["fight_id", "opp_join_id"],
        how="left",
    )

    out["sig_str_absorbed"] = pd.to_numeric(out["opp_sig_str_landed"], errors="coerce")
    out["sig_str_opp_attempted"] = pd.to_numeric(out["opp_sig_str_attempted"], errors="coerce")
    opp_acc = np.where(
        (out["sig_str_opp_attempted"] > 0) & out["sig_str_absorbed"].notna(),
        100.0 * out["sig_str_absorbed"] / out["sig_str_opp_attempted"],
        np.nan,
    )
    out["strike_defense"] = 100.0 - opp_acc

    out["td_absorbed"] = pd.to_numeric(out["opp_td_landed"], errors="coerce")
    out["td_opp_attempted"] = pd.to_numeric(out["opp_td_attempted"], errors="coerce")
    td_acc = np.where(
        (out["td_opp_attempted"] > 0) & out["td_absorbed"].notna(),
        100.0 * out["td_absorbed"] / out["td_opp_attempted"],
        np.nan,
    )
    out["td_defense"] = 100.0 - td_acc

    out["kd_absorbed"] = pd.to_numeric(out["opp_kd"], errors="coerce")
    out["ctrl_against_seconds"] = pd.to_numeric(out["opp_ctrl_seconds"], errors="coerce")

    landed = pd.to_numeric(out["sig_str_landed"], errors="coerce")
    td_l = pd.to_numeric(out["td_landed"], errors="coerce")
    ctrl = pd.to_numeric(out["ctrl_seconds"], errors="coerce")
    out["sig_str_diff"] = landed - out["sig_str_absorbed"]
    out["td_diff"] = td_l - out["td_absorbed"]
    out["ctrl_diff"] = ctrl - out["ctrl_against_seconds"]

    seconds = pd.to_numeric(out["fight_seconds"], errors="coerce")
    out["fight_minutes"] = np.where(seconds > 0, seconds / 60.0, np.nan)
    out["sig_str_landed_pm"] = _series_per_minute(out["sig_str_landed"], seconds)
    out["sig_str_absorbed_pm"] = _series_per_minute(out["sig_str_absorbed"], seconds)
    out["sig_str_attempted_pm"] = _series_per_minute(out["sig_str_attempted"], seconds)
    out["td_landed_pm"] = _series_per_minute(out["td_landed"], seconds)
    out["td_attempted_pm"] = _series_per_minute(out["td_attempted"], seconds)
    out["kd_pm"] = _series_per_minute(out["kd"], seconds)
    out["sub_att_pm"] = _series_per_minute(out["sub_att"], seconds)
    out["ctrl_seconds_pm"] = _series_per_minute(out["ctrl_seconds"], seconds)

    sig_att = pd.to_numeric(out["sig_str_attempted"], errors="coerce")
    td_att = pd.to_numeric(out["td_attempted"], errors="coerce")
    volume = sig_att.add(td_att, fill_value=0)
    both_missing = sig_att.isna() & td_att.isna()
    volume = volume.where(~both_missing, np.nan)
    out["pace"] = _series_per_minute(volume, seconds)

    return out.drop(columns=["opp_join_id"], errors="ignore")


def _round_corner_stats(round_row, side):
    prefix = f"fighter{side}"
    corner = f"fighter{side}"
    sig = significant_strikes_from_raw_round(round_row.get("raw_round"), corner)

    sig_l, sig_a = parse_x_of_y(round_row.get(f"{prefix}_sig_str"))
    tot_l, tot_a = parse_x_of_y(round_row.get(f"{prefix}_total_str"))
    td_l, td_a = parse_x_of_y(round_row.get(f"{prefix}_td"))

    def from_sig(name):
        return parse_x_of_y(sig.get(name))

    dist_l, dist_a = from_sig("distance")
    cl_l, cl_a = from_sig("clinch")
    gr_l, gr_a = from_sig("ground")
    hd_l, hd_a = from_sig("head")
    bd_l, bd_a = from_sig("body")
    lg_l, lg_a = from_sig("leg")

    return {
        "sig_str_landed": sig_l,
        "sig_str_attempted": sig_a,
        "total_str_landed": tot_l,
        "total_str_attempted": tot_a,
        "td_landed": td_l,
        "td_attempted": td_a,
        "kd": parse_count(round_row.get(f"{prefix}_kd")),
        "sub_att": parse_count(round_row.get(f"{prefix}_sub_att")),
        "rev": parse_count(round_row.get(f"{prefix}_rev")),
        "ctrl_seconds": parse_clock_to_seconds(round_row.get(f"{prefix}_ctrl")),
        "distance_landed": dist_l,
        "distance_attempted": dist_a,
        "clinch_landed": cl_l,
        "clinch_attempted": cl_a,
        "ground_landed": gr_l,
        "ground_attempted": gr_a,
        "head_landed": hd_l,
        "head_attempted": hd_a,
        "body_landed": bd_l,
        "body_attempted": bd_a,
        "leg_landed": lg_l,
        "leg_attempted": lg_a,
    }


def _safe_ratio(num, den):
    num = pd.to_numeric(num, errors="coerce")
    den = pd.to_numeric(den, errors="coerce")
    return np.where((den > 0) & num.notna(), num / den, np.nan)


def _round_output_wide(round_facts, keys):
    """Pivot rounds 1-5 strike output onto one row per fighter-fight."""
    sub = round_facts.loc[
        round_facts["round_number"].between(1, 5),
        keys + ["round_number", "sig_str_attempted", "sig_str_landed"],
    ].copy()
    if sub.empty:
        return pd.DataFrame(columns=keys)
    att = sub.pivot_table(
        index=keys, columns="round_number", values="sig_str_attempted", aggfunc="first"
    )
    landed = sub.pivot_table(
        index=keys, columns="round_number", values="sig_str_landed", aggfunc="first"
    )
    att = att.rename(columns={i: f"r{int(i)}_sig_str_attempted" for i in att.columns})
    landed = landed.rename(columns={i: f"r{int(i)}_sig_str_landed" for i in landed.columns})
    wide = att.join(landed, how="outer")
    return wide.reset_index()


def _consec_mean(frame, kind, is_five):
    """Mean of consecutive round ratios: (R2/R1 + R3/R2 + ...) / n_pairs.

    3-round fights use two pairs; 5-round fights use four. Does NOT include
    R3/R1 in the average (that product is already implied by R2/R1 * R3/R2).
    """
    ratios = []
    for rnd in range(1, 5):
        num = frame.get(f"r{rnd + 1}_sig_str_{kind}")
        den = frame.get(f"r{rnd}_sig_str_{kind}")
        if num is None or den is None:
            ratios.append(np.full(len(frame), np.nan))
        else:
            ratios.append(_safe_ratio(num, den))
    stacked = np.vstack(ratios)
    five_mask = np.asarray(is_five, dtype=bool)
    pair_ok = np.ones(stacked.shape, dtype=bool)
    pair_ok[2:, ~five_mask] = False
    stacked = np.where(pair_ok, stacked, np.nan)
    with np.errstate(all="ignore"):
        return np.nanmean(stacked, axis=0)


def _last_completed_round(final_round, fight_seconds, round_minutes=5.0):
    """Last round that was (nearly) a full slot. Stoppage round is excluded unless ≥ 4:50."""
    fr = pd.to_numeric(final_round, errors="coerce")
    sec = pd.to_numeric(fight_seconds, errors="coerce")
    slot = float(round_minutes) * 60.0
    this_round_sec = sec - (fr - 1) * slot
    complete_this = this_round_sec >= 290
    last = np.where(complete_this, fr, fr - 1)
    last = np.where((last < 1) | np.isnan(last), np.nan, last)
    last = np.where(last > 5, 5, last)
    return last


def _output_at_round(frame, last_round, kind):
    """Strike attempted/landed in last_round (1-5) as a 1-d array."""
    n = len(frame)
    picked = np.full(n, np.nan)
    last = np.asarray(last_round, dtype=float)
    for rnd in range(1, 6):
        col = f"r{rnd}_sig_str_{kind}"
        if col not in frame.columns:
            continue
        picked = np.where(last == rnd, pd.to_numeric(frame[col], errors="coerce"), picked)
    return picked


def add_cardio_columns(fighter_facts, round_facts, fight_facts=None):
    """Attach this-fight cardio fields from per-round facts.

    Distance cardio:
      5-round fight that completes R5 -> R5/R1
      otherwise a 3-round fight that completes R3 -> R3/R1
      else NULL (a title fight ending in R3 is not championship gas)

    Consecutive cardio: mean of R(k+1)/R(k) over completed scheduled rounds.
    Late TD/KD/ctrl: rounds 4-5 on 5-rounders, round 3+ otherwise.
    """
    empty = {col: np.nan for col in CARDIO_FACT_COLS}
    for flag in (
        "reached_round_3",
        "completed_round_3",
        "reached_round_5",
        "completed_round_5",
        "went_the_distance",
    ):
        empty[flag] = False
    if fighter_facts.empty or round_facts.empty:
        for col, value in empty.items():
            fighter_facts[col] = value
        return fighter_facts

    keys = ["fight_id", "fighter_id"]
    wide = _round_output_wide(round_facts, keys)
    out = fighter_facts.merge(wide, on=keys, how="left")
    if fight_facts is not None and "scheduled_rounds" in fight_facts.columns:
        sched = fight_facts[["fight_id", "scheduled_rounds"]].drop_duplicates("fight_id")
        if "scheduled_rounds" in out.columns:
            out = out.drop(columns=["scheduled_rounds"])
        out = out.merge(sched, on="fight_id", how="left")

    for rnd in range(1, 6):
        for kind in ("attempted", "landed"):
            col = f"r{rnd}_sig_str_{kind}"
            if col not in out.columns:
                out[col] = np.nan

    has_r3 = out["r3_sig_str_attempted"].notna() | out["r3_sig_str_landed"].notna()
    has_r5 = out["r5_sig_str_attempted"].notna() | out["r5_sig_str_landed"].notna()
    final_round = pd.to_numeric(out["final_round"], errors="coerce")
    fight_seconds = pd.to_numeric(out["fight_seconds"], errors="coerce")
    scheduled = pd.to_numeric(out.get("scheduled_rounds"), errors="coerce")
    is_five = (scheduled == 5).fillna(False)

    completed_r3 = has_r3 & (
        (final_round > 3) | ((final_round == 3) & ((fight_seconds - 600.0) >= 290))
    )
    completed_r5 = has_r5 & (
        (final_round > 5) | ((final_round == 5) & ((fight_seconds - 1200.0) >= 290))
    )
    is_five_np = is_five.to_numpy()
    went_distance = np.where(
        is_five_np,
        completed_r5.fillna(False).to_numpy(),
        completed_r3.fillna(False).to_numpy(),
    )

    out["reached_round_3"] = has_r3.fillna(False).astype(bool)
    out["completed_round_3"] = completed_r3.fillna(False).astype(bool)
    out["reached_round_5"] = has_r5.fillna(False).astype(bool)
    out["completed_round_5"] = completed_r5.fillna(False).astype(bool)
    out["went_the_distance"] = went_distance

    out["cardio_r3_r1_att"] = _safe_ratio(out["r3_sig_str_attempted"], out["r1_sig_str_attempted"])
    out["cardio_r3_r1_landed"] = _safe_ratio(out["r3_sig_str_landed"], out["r1_sig_str_landed"])
    out.loc[~out["reached_round_3"], ["cardio_r3_r1_att", "cardio_r3_r1_landed"]] = np.nan

    out["cardio_r5_r1_att"] = _safe_ratio(out["r5_sig_str_attempted"], out["r1_sig_str_attempted"])
    out["cardio_r5_r1_landed"] = _safe_ratio(out["r5_sig_str_landed"], out["r1_sig_str_landed"])
    out.loc[~out["reached_round_5"], ["cardio_r5_r1_att", "cardio_r5_r1_landed"]] = np.nan

    dist_att = np.where(is_five_np, out["cardio_r5_r1_att"].to_numpy(), out["cardio_r3_r1_att"].to_numpy())
    dist_landed = np.where(is_five_np, out["cardio_r5_r1_landed"].to_numpy(), out["cardio_r3_r1_landed"].to_numpy())
    out["cardio_distance_att"] = np.where(went_distance, dist_att, np.nan)
    out["cardio_distance_landed"] = np.where(went_distance, dist_landed, np.nan)

    consec_att = _consec_mean(out, "attempted", is_five)
    consec_landed = _consec_mean(out, "landed", is_five)
    out["cardio_consec_att"] = np.where(went_distance, consec_att, np.nan)
    out["cardio_consec_landed"] = np.where(went_distance, consec_landed, np.nan)

    # 5-round stoppage: tank can still be there. Rx = last *completed* round, not
    # the truncated stoppage round (a 0:40 R4 KO is not a gas-tank reading).
    last_full = _last_completed_round(final_round, fight_seconds)
    out["last_completed_round"] = last_full
    last_att = _output_at_round(out, last_full, "attempted")
    last_landed = _output_at_round(out, last_full, "landed")
    r1_att = pd.to_numeric(out["r1_sig_str_attempted"], errors="coerce").to_numpy()
    r1_landed = pd.to_numeric(out["r1_sig_str_landed"], errors="coerce").to_numpy()
    last_full_att = np.where((r1_att > 0) & (last_full >= 2), last_att / r1_att, np.nan)
    last_full_landed = np.where((r1_landed > 0) & (last_full >= 2), last_landed / r1_landed, np.nan)
    out["cardio_last_full_r1_att"] = last_full_att
    out["cardio_last_full_r1_landed"] = last_full_landed

    five_stoppage = is_five_np & (~went_distance) & (last_full >= 3)
    out["cardio_five_stoppage_att"] = np.where(five_stoppage, last_full_att, np.nan)
    out["cardio_five_stoppage_landed"] = np.where(five_stoppage, last_full_landed, np.nan)
    out["cardio_observed_att"] = np.where(went_distance, dist_att, np.where(five_stoppage, last_full_att, np.nan))
    out["cardio_observed_landed"] = np.where(
        went_distance, dist_landed, np.where(five_stoppage, last_full_landed, np.nan)
    )

    late3 = (
        round_facts.loc[round_facts["round_number"] >= 3]
        .groupby(keys, as_index=False)
        .agg(
            late3_td_landed=("td_landed", "sum"),
            late3_td_attempted=("td_attempted", "sum"),
            late3_kd=("kd", "sum"),
            late3_ctrl=("ctrl_seconds", "sum"),
        )
    )
    late4 = (
        round_facts.loc[round_facts["round_number"] >= 4]
        .groupby(keys, as_index=False)
        .agg(
            late4_td_landed=("td_landed", "sum"),
            late4_td_attempted=("td_attempted", "sum"),
            late4_kd=("kd", "sum"),
            late4_ctrl=("ctrl_seconds", "sum"),
        )
    )
    out = out.merge(late3, on=keys, how="left").merge(late4, on=keys, how="left")
    use_five_late = is_five.fillna(False) & out["reached_round_5"].fillna(False)
    use_three_late = (~is_five.fillna(False)) & out["reached_round_3"].fillna(False)
    out["late_td_landed"] = np.where(
        use_five_late, out["late4_td_landed"], np.where(use_three_late, out["late3_td_landed"], np.nan)
    )
    out["late_td_attempted"] = np.where(
        use_five_late, out["late4_td_attempted"], np.where(use_three_late, out["late3_td_attempted"], np.nan)
    )
    out["late_kd"] = np.where(
        use_five_late, out["late4_kd"], np.where(use_three_late, out["late3_kd"], np.nan)
    )
    out["late_ctrl_seconds"] = np.where(
        use_five_late, out["late4_ctrl"], np.where(use_three_late, out["late3_ctrl"], np.nan)
    )

    drop_cols = [c for c in out.columns if c.startswith("late3_") or c.startswith("late4_")]
    drop_cols += [f"r{i}_sig_str_{k}" for i in (2, 4) for k in ("attempted", "landed")]
    if "scheduled_rounds" in drop_cols or "scheduled_rounds" in out.columns:
        # scheduled_rounds is not a fighter_fight_facts insert column
        drop_cols.append("scheduled_rounds")
    return out.drop(columns=[c for c in drop_cols if c in out.columns], errors="ignore")


def build_round_facts_frame(fights, rounds):
    if rounds.empty or fights.empty:
        return pd.DataFrame(columns=ROUND_FACT_COLS)

    fighters = fights.set_index("fight_id")[["fighter1_id", "fighter2_id"]]
    merged = rounds.merge(fighters, left_on="fight_id", right_index=True, how="inner")

    rows = []
    for _, round_row in merged.iterrows():
        for side, fighter_col, is_fighter1 in (
            (1, "fighter1_id", True),
            (2, "fighter2_id", False),
        ):
            stats = _round_corner_stats(round_row, side)
            stats.update(
                {
                    "fight_id": round_row["fight_id"],
                    "fighter_id": round_row[fighter_col],
                    "round_number": round_row["round_number"],
                    "is_fighter1": is_fighter1,
                }
            )
            rows.append(stats)
    return pd.DataFrame(rows)


def _records(frame, columns):
    records = []
    for _, row in frame.iterrows():
        values = []
        for col in columns:
            value = row.get(col)
            if col == "event_date" and pd.notnull(value):
                values.append(pd.Timestamp(value).date())
            elif col in INT_FACT_COLS:
                values.append(none_or_int(value))
            else:
                values.append(nan_to_none(value))
        records.append(tuple(values))
    return records


def replace_facts(connection, fight_facts, fighter_facts, round_facts):
    fight_cols = [
        "fight_id",
        "event_id",
        "event_date",
        "weight_class_raw",
        "weight_class_norm",
        "is_title",
        "is_women",
        "time_format_raw",
        "scheduled_rounds",
        "final_round",
        "fight_seconds",
        "method_raw",
        "method_group",
        "outcome_type",
        "winner_id",
        "is_valid_for_stats",
    ]

    with connection.cursor() as cursor:
        cursor.execute("TRUNCATE fighter_round_facts, fighter_fight_facts, fight_facts")

        execute_values(
            cursor,
            f"INSERT INTO fight_facts ({', '.join(fight_cols)}) VALUES %s",
            _records(fight_facts, fight_cols),
            page_size=500,
        )
        execute_values(
            cursor,
            f"INSERT INTO fighter_fight_facts ({', '.join(FIGHTER_FIGHT_FACT_COLS)}) VALUES %s",
            _records(fighter_facts, FIGHTER_FIGHT_FACT_COLS),
            page_size=500,
        )
        if not round_facts.empty:
            execute_values(
                cursor,
                f"INSERT INTO fighter_round_facts ({', '.join(ROUND_FACT_COLS)}) VALUES %s",
                _records(round_facts, ROUND_FACT_COLS),
                page_size=500,
            )
    connection.commit()


def run(connection=None):
    owns_connection = connection is None
    if owns_connection:
        connection = get_connection()
    try:
        print("Ensuring fact tables exist...")
        create_fact_tables(connection)

        print("Loading raw fights / totals / rounds...")
        fights, rounds = load_raw(connection)
        print(f"  {len(fights)} fights, {len(rounds)} round rows")

        print("Building fight_facts...")
        fight_facts = build_fight_facts_frame(fights)

        print("Aggregating round totals...")
        round_sums = aggregate_round_totals(rounds)

        print("Building fighter_fight_facts...")
        fighter_facts = build_fighter_fight_facts_frame(fights, fight_facts, round_sums)

        print("Building fighter_round_facts...")
        round_facts = build_round_facts_frame(fights, rounds)

        print("Adding cardio columns (R3/R1, late TD/KD)...")
        fighter_facts = add_cardio_columns(fighter_facts, round_facts, fight_facts)

        print("Writing fact tables (full replace)...")
        replace_facts(connection, fight_facts, fighter_facts, round_facts)
        print(
            f"Done. fight_facts={len(fight_facts)} "
            f"fighter_fight_facts={len(fighter_facts)} "
            f"fighter_round_facts={len(round_facts)}"
        )
        return {
            "fight_facts": len(fight_facts),
            "fighter_fight_facts": len(fighter_facts),
            "fighter_round_facts": len(round_facts),
        }
    finally:
        if owns_connection:
            connection.close()


if __name__ == "__main__":
    run()
