"""Shared parsers for UFCStats text fields and canonical fight classifications.

Used by the Layer 2 fact builder. Tolerant of None / "---" / malformed input;
never raises on bad values. Percents are 0-100. Durations are seconds.
NULL/NaN means "unknown or not applicable", never a silent 0.
"""

import json
import re

import numpy as np
import pandas as pd


def parse_x_of_y(value):
    """'24 of 66' -> (24.0, 66.0). Returns (NaN, NaN) if unparseable."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return (np.nan, np.nan)
    match = re.search(r"(\d+)\s*of\s*(\d+)", str(value), re.IGNORECASE)
    if not match:
        return (np.nan, np.nan)
    return (float(match.group(1)), float(match.group(2)))


def parse_percent(value):
    """'36%' -> 36.0. '---' or None -> NaN (no attempts != 0% accuracy)."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    text = str(value).strip()
    if text in ("", "---", "--", "nan"):
        return np.nan
    match = re.search(r"-?\d+(\.\d+)?", text)
    return float(match.group()) if match else np.nan


def parse_count(value):
    """Integer-like cells (KD, sub att, rev). '---' -> NaN."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    text = str(value).strip()
    if text in ("", "---", "--", "nan"):
        return np.nan
    match = re.search(r"-?\d+", text.replace(",", ""))
    return float(match.group()) if match else np.nan


def parse_clock_to_seconds(value):
    """'2:15' -> 135.0, '1:02:03' -> 3723.0. '---' / None -> NaN."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    text = str(value).strip()
    if text in ("", "---", "--", "nan"):
        return np.nan
    if ":" in text:
        parts = text.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return np.nan
        if len(nums) == 2:
            return nums[0] * 60.0 + nums[1]
        if len(nums) == 3:
            return nums[0] * 3600.0 + nums[1] * 60.0 + nums[2]
        return np.nan
    match = re.search(r"-?\d+(\.\d+)?", text)
    return float(match.group()) if match else np.nan


def ratio_percent(landed, attempted):
    """100 * landed / attempted, or NaN if attempted is missing or 0."""
    if attempted is None or (isinstance(attempted, float) and np.isnan(attempted)):
        return np.nan
    if attempted <= 0:
        return np.nan
    if landed is None or (isinstance(landed, float) and np.isnan(landed)):
        return np.nan
    return 100.0 * float(landed) / float(attempted)


def share_percent(part, whole):
    """100 * part / whole, or NaN if whole is missing or 0."""
    return ratio_percent(part, whole)


METHOD_MIX_GROUPS = ("KO_TKO", "SUB", "DEC")


def method_mix_and_entropy(method_groups):
    """Shares of KO / SUB / DEC among classified wins, plus normalized Shannon entropy.

    Entropy is H / log2(3): 0 = specialist (all one method), 1 = equal mix.
    DQ / OTHER / missing methods are ignored. Empty mix -> NaNs.
    """
    counts = {"KO_TKO": 0, "SUB": 0, "DEC": 0}
    n = 0
    for group in method_groups:
        if group in counts:
            counts[group] += 1
            n += 1
    if n == 0:
        return {
            "ko_rate": np.nan,
            "sub_rate": np.nan,
            "dec_rate": np.nan,
            "entropy_norm": np.nan,
            "n": 0,
        }
    rates = {key: counts[key] / n for key in counts}
    entropy = 0.0
    for p in rates.values():
        if p > 0:
            entropy -= p * float(np.log2(p))
    return {
        "ko_rate": rates["KO_TKO"],
        "sub_rate": rates["SUB"],
        "dec_rate": rates["DEC"],
        "entropy_norm": entropy / float(np.log2(3.0)),
        "n": n,
    }


def style_entropy_pct(distance_pct, clinch_pct, ground_pct):
    """Normalized Shannon entropy of a distance/clinch/ground mix (0=one-note, 1=even)."""
    parts = []
    for value in (distance_pct, clinch_pct, ground_pct):
        if value is None or (isinstance(value, float) and np.isnan(value)) or value < 0:
            parts.append(0.0)
        else:
            parts.append(float(value))
    total = sum(parts)
    if total <= 0:
        return np.nan
    entropy = 0.0
    for part in parts:
        if part > 0:
            p = part / total
            entropy -= p * float(np.log2(p))
    return entropy / float(np.log2(3.0))


def style_entropy_series(distance, clinch, ground):
    """Vectorized style_entropy_pct for DataFrame columns (percents 0-100)."""
    dist = pd.to_numeric(distance, errors="coerce").fillna(0.0)
    cln = pd.to_numeric(clinch, errors="coerce").fillna(0.0)
    gnd = pd.to_numeric(ground, errors="coerce").fillna(0.0)
    total = dist + cln + gnd

    with np.errstate(divide="ignore", invalid="ignore"):
        p_dist = np.where(total > 0, dist / total, 0.0)
        p_cln = np.where(total > 0, cln / total, 0.0)
        p_gnd = np.where(total > 0, gnd / total, 0.0)
        entropy = np.zeros(len(total))
        for p in (p_dist, p_cln, p_gnd):
            entropy = entropy + np.where(p > 0, -p * np.log2(p), 0.0)
    return np.where(total > 0, entropy / np.log2(3.0), np.nan)


def per_minute(count, seconds):
    """count / (seconds/60). NaN if duration is missing or not positive."""
    if count is None or (isinstance(count, float) and np.isnan(count)):
        return np.nan
    if seconds is None or (isinstance(seconds, float) and np.isnan(seconds)):
        return np.nan
    if seconds <= 0:
        return np.nan
    return float(count) / (float(seconds) / 60.0)


def parse_time_format(time_format):
    """Parse UFCStats time_format into scheduled regulation rounds and per-slot minutes.

    '3 Rnd (5-5-5)'        -> scheduled_rounds=3, round_minutes=[5,5,5]
    '5 Rnd (5-5-5-5-5)'    -> scheduled_rounds=5, round_minutes=[5,5,5,5,5]
    '3 Rnd + OT (5-5-5-5)' -> scheduled_rounds=3, round_minutes=[5,5,5,5]
    'No Time Limit'        -> scheduled_rounds=None, round_minutes=[]
    """
    if time_format is None or (isinstance(time_format, float) and np.isnan(time_format)):
        return {"scheduled_rounds": None, "round_minutes": []}
    text = str(time_format).strip()
    if text == "" or "no time limit" in text.lower():
        return {"scheduled_rounds": None, "round_minutes": []}

    scheduled = None
    match = re.search(r"(\d+)\s*rnd", text, re.IGNORECASE)
    if match:
        scheduled = int(match.group(1))

    round_minutes = []
    paren = re.search(r"\(([^)]+)\)", text)
    if paren:
        for part in paren.group(1).split("-"):
            part = part.strip()
            if re.match(r"^\d+(\.\d+)?$", part):
                round_minutes.append(float(part))

    return {"scheduled_rounds": scheduled, "round_minutes": round_minutes}


def compute_fight_seconds(final_round, time_str, time_format):
    """Total cage time in seconds: completed prior rounds + clock in the finishing round.

    fights.time is the clock in the finishing round, not total duration.
    Missing per-slot minutes default to 5.0 (modern UFC).
    """
    last = parse_clock_to_seconds(time_str)
    info = parse_time_format(time_format)
    minutes = info["round_minutes"]

    if final_round is None or (isinstance(final_round, float) and np.isnan(final_round)):
        return last if not (isinstance(last, float) and np.isnan(last)) else None

    try:
        rounds_fought = int(final_round)
    except (TypeError, ValueError):
        return last if not (isinstance(last, float) and np.isnan(last)) else None

    if rounds_fought <= 1:
        if isinstance(last, float) and np.isnan(last):
            return None
        return last

    total = 0.0
    for i in range(1, rounds_fought):
        slot_minutes = minutes[i - 1] if (i - 1) < len(minutes) else 5.0
        total += slot_minutes * 60.0

    if isinstance(last, float) and np.isnan(last):
        return total if total > 0 else None
    total += last
    return total


def method_group(method, outcome_type=None):
    """Canonical method bucket: KO_TKO, SUB, DEC, DQ, NC, OTHER."""
    method_text = "" if method is None or (isinstance(method, float) and np.isnan(method)) else str(method).strip().lower()
    outcome_text = "" if outcome_type is None or (isinstance(outcome_type, float) and np.isnan(outcome_type)) else str(outcome_type).strip().lower()

    if (
        "overturned" in method_text
        or "could not continue" in method_text
        or "no contest" in method_text
        or outcome_text == "no_contest"
    ):
        return "NC"
    if method_text == "dq" or "disqual" in method_text:
        return "DQ"
    if method_text.startswith("decision"):
        return "DEC"
    if (
        method_text in ("ko/tko", "ko", "tko")
        or method_text.startswith("ko")
        or "tko" in method_text
        or "doctor" in method_text
    ):
        return "KO_TKO"
    if "submission" in method_text:
        return "SUB"
    if method_text == "":
        return "OTHER"
    return "OTHER"


def is_valid_for_stats(method=None, outcome_type=None, group=None):
    """NC / DQ / overturned fights are excluded from performance averages."""
    if group is None:
        group = method_group(method, outcome_type)
    return group not in ("NC", "DQ")


def normalize_weight_class(raw):
    """Return (normalized_name, is_title, is_women).

    'UFC Lightweight Title Bout' -> ('Lightweight', True, False)
    "Women's Strawweight Bout"   -> ("Women's Strawweight", False, True)
    """
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return (None, False, False)
    text = str(raw).strip()
    if text == "":
        return (None, False, False)

    is_title = "title" in text.lower()
    is_women = re.search(r"women'?s", text, re.IGNORECASE) is not None
    norm = re.sub(r"\s+bout\s*$", "", text, flags=re.IGNORECASE)
    norm = re.sub(r"^UFC\s+", "", norm, flags=re.IGNORECASE)
    norm = re.sub(r"\s+title$", "", norm, flags=re.IGNORECASE)
    norm = re.sub(r"\s+", " ", norm).strip()
    return (norm or None, is_title, is_women)


def corner_result(fighter_id, winner_id, outcome_type):
    """W / L / D / NC for one corner. None if outcome cannot be resolved."""
    outcome_text = "" if outcome_type is None or (isinstance(outcome_type, float) and np.isnan(outcome_type)) else str(outcome_type).strip().lower()
    if outcome_text == "draw":
        return "D"
    if outcome_text == "no_contest":
        return "NC"
    if winner_id is None or (isinstance(winner_id, float) and np.isnan(winner_id)):
        return None
    if fighter_id is None or (isinstance(fighter_id, float) and np.isnan(fighter_id)):
        return None
    try:
        return "W" if int(winner_id) == int(fighter_id) else "L"
    except (TypeError, ValueError):
        return None


def coerce_json(value):
    """JSONB from pandas/psycopg2 may already be a dict, or a JSON string."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None
        try:
            return json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return None


def significant_strikes_from_raw_round(raw_round, corner):
    """Pull per-round distance/clinch/ground/head/body/leg from raw_round JSON."""
    payload = coerce_json(raw_round) or {}
    sig = payload.get("significant_strikes") or {}
    if not isinstance(sig, dict):
        return {}
    corner_stats = sig.get(corner) or {}
    return corner_stats if isinstance(corner_stats, dict) else {}


def nan_to_none(value):
    if value is None:
        return None
    if isinstance(value, (float, np.floating)) and np.isnan(value):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def none_or_int(value):
    parsed = nan_to_none(value)
    if parsed is None:
        return None
    return int(parsed)


if __name__ == "__main__":
    assert parse_x_of_y("24 of 66") == (24.0, 66.0)
    assert np.isnan(parse_percent("---"))
    assert parse_percent("36%") == 36.0
    assert parse_clock_to_seconds("2:15") == 135.0
    assert parse_count("3") == 3.0
    assert method_group("Overturned", "decisive") == "NC"
    assert method_group("Could Not Continue", "no_contest") == "NC"
    assert method_group("DQ", "decisive") == "DQ"
    assert method_group("Decision - Split", "decisive") == "DEC"
    assert method_group("KO/TKO", "decisive") == "KO_TKO"
    assert method_group("TKO - Doctor's Stoppage", "decisive") == "KO_TKO"
    assert method_group("Submission", "decisive") == "SUB"
    assert not is_valid_for_stats(group="NC")
    assert not is_valid_for_stats(group="DQ")
    assert is_valid_for_stats(group="DEC")
    assert normalize_weight_class("UFC Lightweight Title Bout") == ("Lightweight", True, False)
    assert normalize_weight_class("Women's Strawweight Bout")[2] is True
    fmt = parse_time_format("3 Rnd (5-5-5)")
    assert fmt["scheduled_rounds"] == 3
    assert fmt["round_minutes"] == [5.0, 5.0, 5.0]
    # Round 2 finish at 1:40 with 5-min rounds -> 5*60 + 100 = 400
    assert compute_fight_seconds(2, "1:40", "3 Rnd (5-5-5)") == 400.0
    assert style_entropy_pct(100, 0, 0) < 0.01
    assert style_entropy_pct(40, 30, 30) > 0.95
    mix = method_mix_and_entropy(["KO_TKO"] * 18)
    assert mix["ko_rate"] == 1.0 and mix["entropy_norm"] == 0.0
    mix = method_mix_and_entropy(["KO_TKO"] * 7 + ["SUB"] * 6 + ["DEC"] * 8)
    assert abs(mix["ko_rate"] + mix["sub_rate"] + mix["dec_rate"] - 1.0) < 1e-9
    assert mix["entropy_norm"] > 0.95
    assert abs(per_minute(90, 300) - 18.0) < 1e-9
    assert np.isnan(per_minute(90, 0))
    assert corner_result(10, 10, "decisive") == "W"
    assert corner_result(11, 10, "decisive") == "L"
    assert corner_result(10, None, "draw") == "D"
    print("stat_parse self-check passed")
