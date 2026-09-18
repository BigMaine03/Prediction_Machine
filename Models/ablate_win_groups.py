"""Leave-one-family-out ablation for the stacked win model.

Does not overwrite Models/artifacts/*.joblib.

Same era split and XGBoost settings as train_baseline.py. Stacked SLpM/TD/ctrl
are built once (walk-forward OOF on FIT; saved stat models on VAL/TEST), then
each recipe only retrains win.

VAL is used for early stopping, so VAL metrics are slightly optimistic.
Use VAL to rank families; TEST is the exam. Do not pick a recipe on TEST
and then quote that same TEST as the result.

Usage
-----
  python Models/ablate_win_groups.py
"""

import csv
import os
import sys

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Models.train_baseline import (
    ARTIFACT_DIR,
    STACK_FEATURE_COLS,
    apply_stack_columns,
    feature_columns,
    fit_classifier,
    load_artifact,
    load_train_table,
    page_only,
    print_era,
    production_stat_preds,
    sample_weights,
    time_slices,
    to_numeric_features,
    walk_forward_oof_preds,
    _best_trees,
)
from Database.db import get_connection

REPORT_PATH = os.path.join(ARTIFACT_DIR, "ablate_win_groups.csv")
CONF_GAP = 0.10
HIGH_CONF = 0.66

TD_PRODUCT_MARKERS = (
    "prod_expected_td",
    "prod_last5_expected_td",
    "prod_ground_x_td",
)


def _stem(col):
    for prefix in ("a_", "b_", "diff_"):
        if col.startswith(prefix):
            return col[len(prefix):]
    return col


def is_last3(col):
    stem = _stem(col)
    return (
        stem.startswith("last3_")
        or stem.startswith("last_3_")
        or "trend_last3_vs" in stem
    )


def is_last5(col):
    stem = _stem(col)
    return (
        stem.startswith("last5_")
        or stem.startswith("last_5_")
        or "trend_last5_vs" in stem
    )


def is_momentum(col):
    stem = _stem(col)
    return stem.startswith("momentum_") or stem in {
        "fights_in_last_18mo",
        "used_career_fallback_momentum",
    }


def is_cardio(col):
    stem = _stem(col)
    if "cardio" in stem:
        return True
    if "late_td" in stem or "late_kd" in stem or "late_ctrl" in stem:
        return True
    markers = (
        "n_r3",
        "n_r3_complete",
        "n_distance",
        "n_five_stoppage",
        "n_observed",
    )
    return any(marker in stem for marker in markers)


def is_stack(col):
    return col.startswith("stack_")


def is_opp_sos(col):
    stem = _stem(col)
    return "opp_asof" in stem or "avg_opp" in stem or "n_opp_known" in stem


def is_bio(col):
    return _stem(col) in {"height", "reach", "ape_index"}


FAMILIES = (
    ("last3", is_last3),
    ("momentum", is_momentum),
    ("cardio", is_cardio),
    ("last5", is_last5),
    ("stack", is_stack),
    ("opp_sos", is_opp_sos),
    ("bio", is_bio),
)


def family_members(columns, predicate):
    return [col for col in columns if predicate(col)]


def drop_families(columns, predicates):
    dropped = set()
    for pred in predicates:
        dropped.update(col for col in columns if pred(col))
    return [col for col in columns if col not in dropped], sorted(dropped)


def score_page(model, frame, x_cols, cat_maps):
    page = page_only(frame[frame["y_a_win"].notna()])
    y = page["y_a_win"].astype(int).to_numpy()
    x, _ = to_numeric_features(page, x_cols, cat_maps=cat_maps)
    p = np.clip(model.predict_proba(x)[:, 1], 1e-6, 1.0 - 1e-6)
    hat = (p >= 0.5).astype(int)
    conf = np.abs(p - 0.5)
    metrics = {
        "n": int(len(y)),
        "logloss": float(log_loss(y, p)),
        "acc": float(accuracy_score(y, hat)),
        "brier": float(brier_score_loss(y, p)),
    }
    mask_conf = conf >= CONF_GAP
    metrics["n_conf"] = int(mask_conf.sum())
    metrics["acc_conf"] = (
        float(accuracy_score(y[mask_conf], hat[mask_conf]))
        if metrics["n_conf"] >= 20
        else float("nan")
    )
    mask_high = (p >= HIGH_CONF) | (p <= 1.0 - HIGH_CONF)
    metrics["n_66"] = int(mask_high.sum())
    metrics["acc_66"] = (
        float(accuracy_score(y[mask_high], hat[mask_high]))
        if metrics["n_66"] >= 10
        else float("nan")
    )
    return metrics


def train_win(fit, val, x_cols):
    y_col = "y_a_win"
    fit_w = fit[fit[y_col].notna()]
    val_w = val[val[y_col].notna()]
    x_fit, cat_maps = to_numeric_features(fit_w, x_cols)
    x_val, _ = to_numeric_features(val_w, x_cols, cat_maps=cat_maps)
    model = fit_classifier(
        x_fit,
        fit_w[y_col].astype(int),
        sample_weights(fit_w),
        x_val,
        val_w[y_col].astype(int),
    )
    return model, cat_maps, _best_trees(model)


def _fmt(value, digits=4):
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "   n/a"
    return f"{value:.{digits}f}"


def print_table(rows):
    header = (
        f"{'recipe':<28} {'n':>5} {'trees':>5}  "
        f"{'val_ll':>7} {'val_acc':>7} {'val@10':>7} {'val66':>7}  "
        f"{'tst_ll':>7} {'tst_acc':>7} {'tst@10':>7} {'tst66':>7}  "
        f"{'d_val_ll':>8}"
    )
    print(header)
    print("-" * len(header))
    base_ll = rows[0]["val"]["logloss"] if rows else None
    for row in rows:
        val, test = row["val"], row["test"]
        delta = ""
        if base_ll is not None:
            delta = f"{val['logloss'] - base_ll:+.4f}"
        print(
            f"{row['name']:<28} {row['n_features']:5d} {str(row['trees'] or '-'):>5}  "
            f"{_fmt(val['logloss'])} {_fmt(val['acc'], 3)} {_fmt(val['acc_conf'], 3)} {_fmt(val['acc_66'], 3)}  "
            f"{_fmt(test['logloss'])} {_fmt(test['acc'], 3)} {_fmt(test['acc_conf'], 3)} {_fmt(test['acc_66'], 3)}  "
            f"{delta:>8}"
        )


def build_stacked_frames(fit, val, test, win_cols, grappling_cols):
    print("Loading saved SLpM / TD / ctrl models ...")
    slpm = load_artifact("slpm")
    td = load_artifact("td")
    ctrl = load_artifact("ctrl")
    print("Walk-forward OOF stacks on FIT (same as train_baseline) ...")
    oof_slpm = walk_forward_oof_preds(fit, win_cols, "y_a_sig_str_landed_pm", "SLpM")
    oof_td = walk_forward_oof_preds(fit, grappling_cols, "y_a_td_landed", "TD")
    oof_ctrl = walk_forward_oof_preds(fit, grappling_cols, "y_a_ctrl_seconds", "ctrl")
    fit_s = apply_stack_columns(fit, {"slpm": oof_slpm, "td": oof_td, "ctrl": oof_ctrl})
    val_s = apply_stack_columns(
        val,
        {
            "slpm": production_stat_preds(slpm, val),
            "td": production_stat_preds(td, val),
            "ctrl": production_stat_preds(ctrl, val),
        },
    )
    test_s = apply_stack_columns(
        test,
        {
            "slpm": production_stat_preds(slpm, test),
            "td": production_stat_preds(td, test),
            "ctrl": production_stat_preds(ctrl, test),
        },
    )
    return fit_s, val_s, test_s


def run_recipe(name, x_cols, fit, val, test):
    print(f"\n--- {name}  ({len(x_cols)} cols) ---")
    model, cat_maps, trees = train_win(fit, val, x_cols)
    val_m = score_page(model, val, x_cols, cat_maps)
    test_m = score_page(model, test, x_cols, cat_maps)
    print(
        f"  VAL  ll={val_m['logloss']:.4f} acc={val_m['acc']:.3f}  "
        f"|p-0.5|>={CONF_GAP:g} n={val_m['n_conf']} acc={_fmt(val_m['acc_conf'], 3)}  "
        f"66%+ n={val_m['n_66']} acc={_fmt(val_m['acc_66'], 3)}"
    )
    print(
        f"  TEST ll={test_m['logloss']:.4f} acc={test_m['acc']:.3f}  "
        f"|p-0.5|>={CONF_GAP:g} n={test_m['n_conf']} acc={_fmt(test_m['acc_conf'], 3)}  "
        f"66%+ n={test_m['n_66']} acc={_fmt(test_m['acc_66'], 3)}  trees={trees}"
    )
    return {
        "name": name,
        "n_features": len(x_cols),
        "trees": trees,
        "val": val_m,
        "test": test_m,
    }


def write_csv(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fieldnames = [
        "recipe",
        "n_features",
        "trees",
        "val_n",
        "val_logloss",
        "val_acc",
        "val_n_conf",
        "val_acc_conf",
        "val_n_66",
        "val_acc_66",
        "test_n",
        "test_logloss",
        "test_acc",
        "test_n_conf",
        "test_acc_conf",
        "test_n_66",
        "test_acc_66",
        "delta_val_logloss",
    ]
    base_ll = rows[0]["val"]["logloss"] if rows else None
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            val, test = row["val"], row["test"]
            writer.writerow(
                {
                    "recipe": row["name"],
                    "n_features": row["n_features"],
                    "trees": row["trees"],
                    "val_n": val["n"],
                    "val_logloss": val["logloss"],
                    "val_acc": val["acc"],
                    "val_n_conf": val["n_conf"],
                    "val_acc_conf": val["acc_conf"],
                    "val_n_66": val["n_66"],
                    "val_acc_66": val["acc_66"],
                    "test_n": test["n"],
                    "test_logloss": test["logloss"],
                    "test_acc": test["acc"],
                    "test_n_conf": test["n_conf"],
                    "test_acc_conf": test["acc_conf"],
                    "test_n_66": test["n_66"],
                    "test_acc_66": test["acc_66"],
                    "delta_val_logloss": (
                        None if base_ll is None else val["logloss"] - base_ll
                    ),
                }
            )


def run():
    connection = get_connection()
    try:
        print("Loading fight_model_train ...")
        df = load_train_table(connection)
    finally:
        connection.close()

    df["event_date"] = pd.to_datetime(df["event_date"])
    win_cols = feature_columns(df, recipe="win")
    grappling_cols = feature_columns(df, recipe="all")
    fit, val, test = time_slices(df)
    print_era(df, fit, val, test)

    fit_s, val_s, test_s = build_stacked_frames(fit, val, test, win_cols, grappling_cols)
    baseline_cols = list(win_cols) + [col for col in STACK_FEATURE_COLS if col not in win_cols]
    print()
    print("===============================")
    print("Win feature families (baseline stacked recipe)")
    print("===============================")
    assigned = {name: family_members(baseline_cols, pred) for name, pred in FAMILIES}
    covered = set()
    for name, cols in assigned.items():
        covered.update(cols)
        print(f"  {name:<12} {len(cols):4d} columns")
    print(f"  {'core/other':<12} {len(baseline_cols) - len(covered):4d} columns")
    print(f"  {'baseline':<12} {len(baseline_cols):4d} columns")
    print()
    print("Leave-one-family-out. Negative d_val_ll = better than baseline on VAL.")
    print("Does not save a new win.joblib.")
    print()

    results = []
    results.append(run_recipe("baseline", baseline_cols, fit_s, val_s, test_s))

    for name, pred in FAMILIES:
        kept, dropped = drop_families(baseline_cols, [pred])
        if not dropped:
            print(f"\n--- skip drop_{name}: no matching columns ---")
            continue
        print(f"\n(drop_{name}: removing {len(dropped)} cols)")
        results.append(run_recipe(f"drop_{name}", kept, fit_s, val_s, test_s))

    last3_pred = dict(FAMILIES)["last3"]
    mom_pred = dict(FAMILIES)["momentum"]
    kept, dropped = drop_families(baseline_cols, [last3_pred, mom_pred])
    results.append(
        run_recipe(f"drop_last3+momentum ({len(dropped)} gone)", kept, fit_s, val_s, test_s)
    )

    td_prod_cols = [
        col
        for col in grappling_cols
        if any(marker in col for marker in TD_PRODUCT_MARKERS)
    ]
    add_cols = list(baseline_cols)
    for col in td_prod_cols:
        if col not in add_cols:
            add_cols.append(col)
    results.append(
        run_recipe(f"add_td_products (+{len(td_prod_cols)})", add_cols, fit_s, val_s, test_s)
    )

    print()
    print("===============================")
    print("Ablation table")
    print("===============================")
    print("val@10 / tst@10 = accuracy among fights with |P-0.5| >= 0.10")
    print("val66 / tst66   = accuracy among 66%+ favorites")
    print()
    print_table(results)
    write_csv(results, REPORT_PATH)
    print()
    print(f"Wrote {REPORT_PATH}")
    print("Rank on VAL logloss. Confirm on TEST. Do not cherry-pick TEST.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
