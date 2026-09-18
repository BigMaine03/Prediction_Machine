"""Predict an upcoming UFCStats card with the saved split models.

Features are as-of the event date: a dummy fight is appended after each
fighter's last real bout so the last completed fight is in X, not y.
Nothing is written to the database. Odds are not used.

Usage
-----
  python Models/predict_card.py
  python Models/predict_card.py --index 1
  python Models/predict_card.py --event-url http://ufcstats.com/event-details/...
  python Models/predict_card.py --list
  python Models/predict_card.py --fighter-a "Dan Hooker" --fighter-b "Salahdine Parnasse"
  python Models/predict_card.py --ledger
"""

import argparse
import os
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Database.db import get_connection
from Features.build_fighter_features import (
    compute_pre_fight_features,
    load_fighter_fight_facts,
)
from Features.feature_columns import (
    DIFF_COLS,
    FEATURE_EXPORT_COLS,
    add_matchup_products,
)
from Features.stat_parse import normalize_weight_class
from Models.card_ledger import (
    classify_pick,
    ledger_summary,
    print_ledger,
    score_pending_picks,
    update_ledger,
)
from Models.train_baseline import attach_stack_to_page_swap, load_artifact, to_numeric_features
from Scraper.scrape_upcoming import scrape_upcoming_event, scrape_upcoming_listing

BOOL_FACT_COLS = (
    "is_valid_for_stats",
    "is_title",
    "is_women",
    "is_fighter1",
    "reached_round_3",
    "completed_round_3",
    "completed_round_5",
    "went_the_distance",
)


def _norm_url(url):
    if not url:
        return None
    return str(url).strip().replace("https://", "http://").rstrip("/")


def _sub(left, right):
    left = pd.to_numeric(pd.Series([left]), errors="coerce").iloc[0]
    right = pd.to_numeric(pd.Series([right]), errors="coerce").iloc[0]
    if pd.isna(left) or pd.isna(right):
        return np.nan
    return float(left) - float(right)


def _parse_date(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return date.today()


def load_fighter_lookup(connection):
    fighters = pd.read_sql(
        "SELECT fighter_id, fighter_name, fighter_url FROM fighters",
        connection,
    )
    by_url = {}
    by_name = {}
    for _, row in fighters.iterrows():
        fighter_id = int(row["fighter_id"])
        url = _norm_url(row["fighter_url"])
        if url:
            by_url[url] = fighter_id
        name = str(row["fighter_name"] or "").strip().lower()
        if name:
            by_name.setdefault(name, []).append(fighter_id)
    return {"by_url": by_url, "by_name": by_name}


def load_bio_map(connection):
    bio = pd.read_sql("SELECT fighter_id, height, reach FROM fighter_bio", connection)
    out = {}
    for _, row in bio.iterrows():
        out[int(row["fighter_id"])] = {
            "height": row["height"],
            "reach": row["reach"],
        }
    return out


def resolve_fighter(lookup, name, url, next_unknown_id):
    url = _norm_url(url)
    if url and url in lookup["by_url"]:
        return lookup["by_url"][url], False, next_unknown_id
    key = (name or "").strip().lower()
    ids = lookup["by_name"].get(key) or []
    if len(ids) == 1:
        return ids[0], False, next_unknown_id
    return next_unknown_id, True, next_unknown_id - 1


def resolve_bouts(bouts, lookup):
    next_unknown_id = -1000
    resolved = []
    for bout in bouts:
        a_id, a_missing, next_unknown_id = resolve_fighter(
            lookup, bout.get("fighter_a_name"), bout.get("fighter_a_url"), next_unknown_id
        )
        b_id, b_missing, next_unknown_id = resolve_fighter(
            lookup, bout.get("fighter_b_name"), bout.get("fighter_b_url"), next_unknown_id
        )
        raw = bout.get("weight_class_raw")
        if bout.get("is_main") and raw and "title" not in str(raw).lower():
            raw = f"{raw} Bout"
        norm, is_title, is_women = normalize_weight_class(raw)
        if bout.get("is_main"):
            scheduled = 5
        else:
            scheduled = 5 if is_title else 3
        item = dict(bout)
        item.update(
            {
                "fighter_a_id": a_id,
                "fighter_b_id": b_id,
                "a_missing": a_missing,
                "b_missing": b_missing,
                "weight_class_norm": norm,
                "is_title": bool(is_title),
                "is_women": bool(is_women),
                "scheduled_rounds": scheduled,
            }
        )
        resolved.append(item)
    return resolved


def append_dummy_bouts(long_df, bouts, event_date):
    cols = list(long_df.columns)
    extra = []
    dummy_ids = []
    event_ts = pd.Timestamp(event_date)
    for i, bout in enumerate(bouts):
        fight_id = -1 * (i + 1)
        dummy_ids.append(fight_id)
        for fighter_id, opponent_id, is_f1 in (
            (bout["fighter_a_id"], bout["fighter_b_id"], True),
            (bout["fighter_b_id"], bout["fighter_a_id"], False),
        ):
            row = {col: np.nan for col in cols}
            row["fighter_id"] = fighter_id
            row["fight_id"] = fight_id
            row["opponent_id"] = opponent_id
            row["is_fighter1"] = is_f1
            row["event_date"] = event_ts
            row["result"] = None
            row["is_valid_for_stats"] = False
            row["method_group"] = None
            row["weight_class_norm"] = bout.get("weight_class_norm")
            row["is_title"] = bout.get("is_title", False)
            row["is_women"] = bout.get("is_women", False)
            row["scheduled_rounds"] = bout.get("scheduled_rounds")
            row["winner_id"] = None
            row["outcome_type"] = None
            row["method"] = None
            row["time_format"] = None
            extra.append(row)
    dummy_df = pd.DataFrame(extra)
    dummy_df["event_date"] = pd.to_datetime(dummy_df["event_date"])
    history = long_df.copy()
    history["event_date"] = pd.to_datetime(history["event_date"])
    for col in BOOL_FACT_COLS:
        if col in dummy_df.columns:
            dummy_df[col] = dummy_df[col].fillna(False).astype(bool)
        if col in history.columns:
            history[col] = history[col].fillna(False).astype(bool)
    combined = pd.concat([history, dummy_df], ignore_index=True)
    combined["event_date"] = pd.to_datetime(combined["event_date"])
    return combined, dummy_ids


def build_model_row(a_row, b_row, bout, bio_map):
    a_bio = bio_map.get(int(a_row["fighter_id"]), {})
    b_bio = bio_map.get(int(b_row["fighter_id"]), {})
    a_height, a_reach = a_bio.get("height"), a_bio.get("reach")
    b_height, b_reach = b_bio.get("height"), b_bio.get("reach")
    rec = {
        "fight_id": a_row["fight_id"],
        "event_date": a_row["event_date"],
        "orientation": "page",
        "weight_class_norm": bout.get("weight_class_norm"),
        "is_title": bool(bout.get("is_title")),
        "is_women": bool(bout.get("is_women")),
        "scheduled_rounds": bout.get("scheduled_rounds"),
        "a_fighter_id": a_row["fighter_id"],
        "b_fighter_id": b_row["fighter_id"],
        "a_height": a_height,
        "b_height": b_height,
        "a_reach": a_reach,
        "b_reach": b_reach,
        "diff_height": _sub(a_height, b_height),
        "diff_reach": _sub(a_reach, b_reach),
        "a_ape_index": _sub(a_reach, a_height),
        "b_ape_index": _sub(b_reach, b_height),
    }
    rec["diff_ape_index"] = _sub(rec["a_ape_index"], rec["b_ape_index"])
    for col in FEATURE_EXPORT_COLS:
        rec[f"a_{col}"] = a_row.get(col)
        rec[f"b_{col}"] = b_row.get(col)
    for col in DIFF_COLS:
        rec[f"diff_{col}"] = _sub(a_row.get(col), b_row.get(col))
    return rec


def predict_with_artifact(bundle, frame):
    features = bundle["features"]
    x, _ = to_numeric_features(frame, features, cat_maps=bundle.get("cat_maps") or {})
    model = bundle["model"]
    if hasattr(model, "predict_proba"):
        return model.predict_proba(x)[:, 1]
    return model.predict(x)


def _fmt_prob(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "  n/a"
    return f"{100.0 * float(value):5.1f}%"


def _fmt_num(value, digits=1):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "n/a"
    return f"{float(value):.{digits}f}"


def _fmt_ctrl(seconds):
    if seconds is None or (isinstance(seconds, float) and np.isnan(seconds)):
        return "n/a"
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    rem = int(round(seconds - minutes * 60))
    if rem == 60:
        minutes += 1
        rem = 0
    return f"{minutes}:{rem:02d}"


def print_card(event, rows):
    headline = event.get("headline") or "Upcoming card"
    when = event.get("date") or ""
    where = event.get("location") or ""
    print()
    print("=" * 72)
    print(headline)
    extra = "  ·  ".join(part for part in (when, where) if part)
    if extra:
        print(extra)
    print("As-of the event date (last completed fight is in X; this bout is not).")
    print("Win sees stacked SLpM/TD/ctrl preds.  No this-fight actuals.  No odds.")
    print("=" * 72)
    for i, rec in enumerate(rows, start=1):
        a_name = rec["fighter_a_name"]
        b_name = rec["fighter_b_name"]
        klass = rec.get("weight_class_norm") or rec.get("weight_class_raw") or ""
        rnds = rec.get("scheduled_rounds")
        tag = "main · " if rec.get("is_main") else ""
        notes = []
        if rec.get("a_missing"):
            notes.append(f"{a_name} not in DB (debut features)")
        if rec.get("b_missing"):
            notes.append(f"{b_name} not in DB (debut features)")
        print()
        print(f"{i:2d}. {a_name}")
        print(f"    vs {b_name}")
        meta = "  ·  ".join(
            part
            for part in (
                klass,
                f"{tag}{rnds} rnd" if rnds else tag.strip(" ·"),
            )
            if part
        )
        if meta:
            print(f"    {meta}")
        pick, tossup, high, _, _ = classify_pick(rec["p_a"])
        if tossup:
            pick_line = "TOSSUP (50-50, not counted)"
        elif pick == "A":
            pick_line = f"PICK {a_name}" + ("  ·  66%+" if high else "")
        else:
            pick_line = f"PICK {b_name}" + ("  ·  66%+" if high else "")
        print(
            f"    P(win)  {_fmt_prob(rec['p_a']):>7}   {a_name:22s}   "
            f"{_fmt_prob(rec['p_b']):>7}   {b_name}"
        )
        print(f"    {pick_line}")
        print(
            f"    SLpM    {_fmt_num(rec['slpm_a']):>7}   {a_name:22s}   "
            f"{_fmt_num(rec['slpm_b']):>7}   {b_name}"
        )
        print(
            f"    TD      {_fmt_num(rec['td_a']):>7}   {a_name:22s}   "
            f"{_fmt_num(rec['td_b']):>7}   {b_name}"
        )
        print(
            f"    Ctrl    {_fmt_ctrl(rec['ctrl_a']):>7}   {a_name:22s}   "
            f"{_fmt_ctrl(rec['ctrl_b']):>7}   {b_name}"
        )
        if notes:
            print("    notes: " + "; ".join(notes))
    print()


def predict_event(event, connection):
    bouts = event.get("bouts") or []
    if not bouts:
        raise SystemExit("No bouts found on that event page.")
    event_date = _parse_date(event.get("date"))
    print("Loading fighters + facts ...")
    lookup = load_fighter_lookup(connection)
    bio_map = load_bio_map(connection)
    bouts = resolve_bouts(bouts, lookup)
    long_df = load_fighter_fight_facts(connection)
    print(f"  {len(long_df)} historical fighter-fight rows, {len(bouts)} upcoming bouts")
    print("Appending dummy fights at event date (not written to DB) ...")
    long_df, dummy_ids = append_dummy_bouts(long_df, bouts, event_date)
    featured = compute_pre_fight_features(long_df, verbose=True)
    dummy = featured[featured["fight_id"].isin(dummy_ids)].copy()

    print("Loading saved models ...")
    win_art = load_artifact("win")
    slpm_art = load_artifact("slpm")
    td_art = load_artifact("td")
    ctrl_art = load_artifact("ctrl")

    page_recs = []
    swap_recs = []
    used_bouts = []
    dummy["fight_id"] = pd.to_numeric(dummy["fight_id"], errors="coerce").astype("Int64")
    dummy["fighter_id"] = pd.to_numeric(dummy["fighter_id"], errors="coerce").astype("Int64")
    for bout, fight_id in zip(bouts, dummy_ids):
        pair = dummy[dummy["fight_id"] == fight_id]
        a_rows = pair[pair["fighter_id"] == bout["fighter_a_id"]]
        b_rows = pair[pair["fighter_id"] == bout["fighter_b_id"]]
        if a_rows.empty or b_rows.empty:
            print(
                f"  skip {bout.get('fighter_a_name')} vs {bout.get('fighter_b_name')}: missing dummy row"
            )
            continue
        a_row = a_rows.iloc[0]
        b_row = b_rows.iloc[0]
        used_bouts.append(bout)
        page_recs.append(build_model_row(a_row, b_row, bout, bio_map))
        swap_recs.append(build_model_row(b_row, a_row, bout, bio_map))

    if not page_recs:
        raise SystemExit("Could not build any prediction rows.")

    page_df = pd.DataFrame(page_recs)
    swap_df = pd.DataFrame(swap_recs)
    for col in page_df.columns:
        if col == "weight_class_norm" or col == "orientation":
            continue
        if page_df[col].dtype == object:
            numeric = pd.to_numeric(page_df[col], errors="coerce")
            if numeric.notna().any() or page_df[col].isna().all():
                page_df[col] = numeric
                swap_df[col] = pd.to_numeric(swap_df[col], errors="coerce")
    page_df = add_matchup_products(page_df)
    swap_df = add_matchup_products(swap_df)

    slpm_a = predict_with_artifact(slpm_art, page_df)
    slpm_b = predict_with_artifact(slpm_art, swap_df)
    td_a = predict_with_artifact(td_art, page_df)
    td_b = predict_with_artifact(td_art, swap_df)
    ctrl_a = predict_with_artifact(ctrl_art, page_df)
    ctrl_b = predict_with_artifact(ctrl_art, swap_df)
    page_df, swap_df = attach_stack_to_page_swap(
        page_df, swap_df, slpm_a, slpm_b, td_a, td_b, ctrl_a, ctrl_b
    )
    p_page = predict_with_artifact(win_art, page_df)
    p_swap = predict_with_artifact(win_art, swap_df)

    out = []
    for i, bout in enumerate(used_bouts):
        p_a = 0.5 * (float(p_page[i]) + (1.0 - float(p_swap[i])))
        out.append(
            {
                **bout,
                "p_a": p_a,
                "p_b": 1.0 - p_a,
                "slpm_a": float(slpm_a[i]),
                "slpm_b": float(slpm_b[i]),
                "td_a": max(0.0, float(td_a[i])),
                "td_b": max(0.0, float(td_b[i])),
                "ctrl_a": max(0.0, float(ctrl_a[i])),
                "ctrl_b": max(0.0, float(ctrl_b[i])),
            }
        )
    return out


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Predict an upcoming UFCStats card.")
    parser.add_argument("--event-url", help="UFCStats event-details URL")
    parser.add_argument(
        "--index",
        type=int,
        default=0,
        help="Index on the upcoming listing (0 = next event)",
    )
    parser.add_argument("--list", action="store_true", help="Print upcoming events and exit")
    parser.add_argument("--fighter-a", help="Manual fighter A name (with --fighter-b)")
    parser.add_argument("--fighter-b", help="Manual fighter B name (with --fighter-a)")
    parser.add_argument("--weight-class", default=None, help="Manual bout weight class")
    parser.add_argument("--date", default=None, help="As-of date YYYY-MM-DD (manual bout)")
    parser.add_argument(
        "--ledger",
        action="store_true",
        help="Score pending picks against the DB and print the ledger, then exit",
    )
    return parser.parse_args(argv)


def run(argv=None):
    args = parse_args(argv)
    if args.ledger:
        connection = get_connection()
        try:
            newly_scored = score_pending_picks(connection)
            summary = ledger_summary(connection)
        finally:
            connection.close()
        print_ledger(summary, newly_scored=newly_scored)
        return 0
    if args.list:
        listing = scrape_upcoming_listing()
        print("Upcoming UFCStats events:")
        for i, event in enumerate(listing):
            print(
                f"  {i:2d}  {event.get('date') or 'unknown date'}  "
                f"{event.get('headline')}  ({event.get('location')})"
            )
            print(f"      {event.get('url')}")
        return 0

    if (args.fighter_a and not args.fighter_b) or (args.fighter_b and not args.fighter_a):
        raise SystemExit("Pass both --fighter-a and --fighter-b.")

    if args.fighter_a:
        event = {
            "headline": f"{args.fighter_a} vs {args.fighter_b}",
            "date": args.date or date.today().isoformat(),
            "location": None,
            "url": None,
            "bouts": [
                {
                    "fighter_a_name": args.fighter_a,
                    "fighter_a_url": None,
                    "fighter_b_name": args.fighter_b,
                    "fighter_b_url": None,
                    "weight_class_raw": args.weight_class,
                    "is_main": True,
                    "fight_url": None,
                }
            ],
        }
    else:
        print("Scraping UFCStats upcoming card ...")
        event = scrape_upcoming_event(event_url=args.event_url, index=args.index)
        print(
            f"  {event.get('headline')}  {event.get('date')}  "
            f"({len(event.get('bouts') or [])} bouts)"
        )

    connection = get_connection()
    try:
        rows = predict_event(event, connection)
        logged, newly_scored, summary = update_ledger(connection, event, rows)
    finally:
        connection.close()
    print_card(event, logged)
    print()
    print_ledger(summary, newly_scored=newly_scored)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
