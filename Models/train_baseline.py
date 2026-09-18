"""Train win / SLpM / TD / control models with a time split and split recipes.

Era (edit the three dates below to change it)
--------------------------------------------
  Fit:   2015-01-01  ≤  event_date  <  2024-01-01   (learn)
  Val:   2024-01-01  ≤  event_date  <  2025-01-01   (early stopping only)
  Test:  event_date  ≥  2025-01-01                   (held-out exam)

Pre-2015 fights are dropped (different sport / thinner roster).

Win and SLpM use the pre-732 column recipe (no matchup products / class /
entropy / TD-funnel). TD landed and control time use the full column set.

Win is a second-stage model: it sees out-of-fold predicted SLpM / TD / ctrl
for both corners (walk-forward on FIT, production stat models on VAL/TEST).
This-fight actual stats are never used as win features.

Rows are downweighted if either fighter is a debut or has < 5 UFC fights.
A second win model is also fit on "veteran" fights only (both ≥ 5 fights).

Usage
-----
  python Models/train_baseline.py
"""

import json
import os
import sys
from datetime import date, datetime

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import accuracy_score, log_loss, mean_absolute_error, brier_score_loss

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Database.db import get_connection
from Features.feature_columns import col_in_win_recipe


FIT_START = date(2015, 1, 1)
VAL_START = date(2024, 1, 1)
TEST_START = date(2025, 1, 1)
MIN_FIGHTS_VETERAN = 5
ARTIFACT_DIR = os.path.join(os.path.dirname(__file__), "artifacts")
OOF_MIN_TRAIN_ROWS = 250
OOF_MIN_INNER_FIT = 200
OOF_MIN_INNER_VAL = 50
OOF_FIXED_TREES = 150

# Slot-A stat predictions plus the swapped row's slot-A (which is B).
STACK_FEATURE_COLS = [
    "stack_a_slpm",
    "stack_b_slpm",
    "stack_diff_slpm",
    "stack_a_td",
    "stack_b_td",
    "stack_diff_td",
    "stack_a_ctrl",
    "stack_b_ctrl",
    "stack_diff_ctrl",
]

NON_FEATURE_EXACT = {
    "fight_id",
    "event_date",
    "orientation",
    "a_fighter_id",
    "b_fighter_id",
    "y_winner_id",
    "y_method_group",
    "y_outcome_type",
    "y_a_result",
    "y_b_result",
    "y_f1_result",
    "y_f2_result",
}


def load_train_table(connection):
    return pd.read_sql("SELECT * FROM fight_model_train", connection)


def feature_columns(df, recipe="all"):
    cols = [
        col
        for col in df.columns
        if col not in NON_FEATURE_EXACT and not col.startswith("y_")
    ]
    if recipe == "win":
        return [col for col in cols if col_in_win_recipe(col)]
    return cols


def to_numeric_features(df, cols, cat_maps=None):
    """Encode columns to float. Unknown categories become NaN.

    If cat_maps is None, maps are fit from df (sorted unique). Pass the
    returned maps at val/test/predict time so codes match training.
    """
    fit_maps = cat_maps is None
    maps = {} if cat_maps is None else dict(cat_maps)
    pieces = {}
    for col in cols:
        if col not in df.columns:
            pieces[col] = pd.Series(np.nan, index=df.index)
            continue
        series = df[col]
        if pd.api.types.is_bool_dtype(series):
            pieces[col] = series.astype("float64")
        elif pd.api.types.is_numeric_dtype(series):
            pieces[col] = pd.to_numeric(series, errors="coerce")
        else:
            as_str = series.astype("string")
            if fit_maps or col not in maps:
                uniques = sorted(as_str.dropna().unique().tolist())
                maps[col] = {value: float(i) for i, value in enumerate(uniques)}
            pieces[col] = as_str.map(maps[col]).astype("float64")
    return pd.DataFrame(pieces, index=df.index), maps


def time_slices(df):
    dates = pd.to_datetime(df["event_date"])
    fit = df[(dates >= pd.Timestamp(FIT_START)) & (dates < pd.Timestamp(VAL_START))].copy()
    val = df[(dates >= pd.Timestamp(VAL_START)) & (dates < pd.Timestamp(TEST_START))].copy()
    test = df[dates >= pd.Timestamp(TEST_START)].copy()
    return fit, val, test


def print_era(df, fit, val, test):
    dates = pd.to_datetime(df["event_date"])
    print("===============================")
    print("Era split")
    print("===============================")
    print(f"All data in view: {dates.min().date()}  →  {dates.max().date()}")
    print(f"Dropped:          fights before {FIT_START} (pre-unified-roster era)")
    print(
        f"FIT (learn):      {pd.to_datetime(fit['event_date']).min().date()}  →  "
        f"{pd.to_datetime(fit['event_date']).max().date()}   "
        f"({len(fit)} rows, {fit['fight_id'].nunique()} fights)"
    )
    print(
        f"VAL (stop trees): {pd.to_datetime(val['event_date']).min().date()}  →  "
        f"{pd.to_datetime(val['event_date']).max().date()}   "
        f"({len(val)} rows, {val['fight_id'].nunique()} fights)"
    )
    print(
        f"TEST (exam):      {pd.to_datetime(test['event_date']).min().date()}  →  "
        f"{pd.to_datetime(test['event_date']).max().date()}   "
        f"({len(test)} rows, {test['fight_id'].nunique()} fights)"
    )
    print("Early stopping watches VAL logloss/MAE; TEST is never used to pick trees.")
    print()


def page_only(df):
    if "orientation" in df.columns:
        return df[df["orientation"] == "page"].copy()
    return df.copy()


def _count_col(df, name):
    if name not in df.columns:
        return pd.Series(np.nan, index=df.index)
    return pd.to_numeric(df[name], errors="coerce")


def _flag_col(df, name):
    if name not in df.columns:
        return pd.Series(False, index=df.index)
    return df[name].fillna(False).astype(bool)


def sample_weights(df):
    """Debut or thin history counts less while learning, not on the exam."""
    a_n = _count_col(df, "a_career_fights_count")
    b_n = _count_col(df, "b_career_fights_count")
    a_deb = _flag_col(df, "a_debut_flag")
    b_deb = _flag_col(df, "b_debut_flag")
    known = _count_col(df, "a_career_n_opp_known")
    min_n = np.fmin(a_n.fillna(0), b_n.fillna(0))
    weights = np.ones(len(df), dtype="float64")
    weights[(min_n < MIN_FIGHTS_VETERAN) | (known.fillna(0) == 0)] = 0.55
    weights[a_deb.to_numpy() | b_deb.to_numpy()] = 0.35
    return weights


def veteran_mask(df):
    a_n = _count_col(df, "a_career_fights_count").fillna(0)
    b_n = _count_col(df, "b_career_fights_count").fillna(0)
    a_deb = _flag_col(df, "a_debut_flag")
    b_deb = _flag_col(df, "b_debut_flag")
    return (~a_deb) & (~b_deb) & (a_n >= MIN_FIGHTS_VETERAN) & (b_n >= MIN_FIGHTS_VETERAN)


def fit_classifier(x_fit, y_fit, w_fit, x_val, y_val):
    model = xgb.XGBClassifier(
        n_estimators=500,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.85,
        colsample_bytree=0.5,
        min_child_weight=12,
        tree_method="hist",
        n_jobs=-1,
        random_state=42,
        eval_metric="logloss",
        early_stopping_rounds=40,
        verbosity=0,
    )
    model.fit(
        x_fit,
        y_fit,
        sample_weight=w_fit,
        eval_set=[(x_val, y_val)],
        verbose=False,
    )
    return model


def fit_regressor(x_fit, y_fit, w_fit, x_val=None, y_val=None, early_stopping_rounds=40, n_estimators=500):
    params = dict(
        n_estimators=n_estimators,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.85,
        colsample_bytree=0.5,
        min_child_weight=12,
        tree_method="hist",
        n_jobs=-1,
        random_state=42,
        eval_metric="mae",
        verbosity=0,
    )
    if early_stopping_rounds and x_val is not None and y_val is not None and len(x_val) > 0:
        params["early_stopping_rounds"] = early_stopping_rounds
        model = xgb.XGBRegressor(**params)
        model.fit(
            x_fit,
            y_fit,
            sample_weight=w_fit,
            eval_set=[(x_val, y_val)],
            verbose=False,
        )
        return model
    model = xgb.XGBRegressor(**params)
    model.fit(x_fit, y_fit, sample_weight=w_fit, verbose=False)
    return model


def predict_regressor_frame(model, frame, x_cols, cat_maps):
    x, _ = to_numeric_features(frame, x_cols, cat_maps=cat_maps)
    return np.asarray(model.predict(x), dtype=float)


def paired_stack_columns(frame, pred_a, name):
    """pred_a is slot-A's predicted stat. Slot B is the swapped row's slot A."""
    pred_a = np.asarray(pred_a, dtype=float)
    if len(pred_a) != len(frame):
        raise ValueError(f"stack {name}: pred length {len(pred_a)} != rows {len(frame)}")
    partner = {"page": "swapped", "swapped": "page"}
    lookup = pd.Series(
        pred_a,
        index=pd.MultiIndex.from_arrays(
            [frame["fight_id"].to_numpy(), frame["orientation"].to_numpy()],
            names=["fight_id", "orientation"],
        ),
    )
    # Duplicate (fight_id, orientation) should not happen; keep first.
    lookup = lookup[~lookup.index.duplicated(keep="first")]
    partner_keys = pd.MultiIndex.from_arrays(
        [
            frame["fight_id"].to_numpy(),
            frame["orientation"].map(partner).to_numpy(),
        ]
    )
    pred_b = lookup.reindex(partner_keys).to_numpy(dtype=float)
    return pd.DataFrame(
        {
            f"stack_a_{name}": pred_a,
            f"stack_b_{name}": pred_b,
            f"stack_diff_{name}": pred_a - pred_b,
        },
        index=frame.index,
    )


def apply_stack_columns(frame, pred_map):
    """pred_map: {name: ndarray aligned to frame} for slpm / td / ctrl."""
    out = frame.copy()
    pieces = [paired_stack_columns(out, pred_map[name], name) for name in ("slpm", "td", "ctrl")]
    stacked = pd.concat(pieces, axis=1)
    for col in stacked.columns:
        out[col] = stacked[col]
    return out


def walk_forward_oof_preds(fit_df, x_cols, y_col, label):
    """Year-ahead OOF predictions. A 2019 row is never trained on 2019+ fights."""
    dates = pd.to_datetime(fit_df["event_date"])
    years = [int(y) for y in sorted(dates.dt.year.dropna().unique())]
    oof = pd.Series(np.nan, index=fit_df.index, dtype="float64")
    print(f"  OOF {label}: walk-forward by year ({years[0]}–{years[-1]})")
    for year in years:
        train_df = fit_df.loc[dates.dt.year < year]
        pred_df = fit_df.loc[dates.dt.year == year]
        if pred_df.empty:
            continue
        y_train = pd.to_numeric(train_df[y_col], errors="coerce")
        usable = train_df.loc[y_train.notna()].copy()
        if len(usable) < OOF_MIN_TRAIN_ROWS:
            print(f"    {year}: skip ({len(usable)} prior labeled rows)")
            continue
        train_dates = pd.to_datetime(usable["event_date"])
        cut = train_dates.max() - pd.DateOffset(months=6)
        inner_fit = usable.loc[train_dates < cut]
        inner_val = usable.loc[train_dates >= cut]
        y_inner = pd.to_numeric(inner_fit[y_col], errors="coerce")
        inner_fit = inner_fit.loc[y_inner.notna()]
        y_inner = y_inner.loc[y_inner.notna()]
        if len(inner_fit) < OOF_MIN_INNER_FIT or len(inner_val) < OOF_MIN_INNER_VAL:
            x_tr, maps = to_numeric_features(usable, x_cols)
            y_tr = pd.to_numeric(usable[y_col], errors="coerce")
            model = fit_regressor(
                x_tr,
                y_tr,
                sample_weights(usable),
                early_stopping_rounds=None,
                n_estimators=OOF_FIXED_TREES,
            )
            trees = OOF_FIXED_TREES
        else:
            y_val = pd.to_numeric(inner_val[y_col], errors="coerce")
            inner_val = inner_val.loc[y_val.notna()]
            y_val = y_val.loc[y_val.notna()]
            x_tr, maps = to_numeric_features(inner_fit, x_cols)
            x_va, _ = to_numeric_features(inner_val, x_cols, cat_maps=maps)
            model = fit_regressor(
                x_tr,
                y_inner,
                sample_weights(inner_fit),
                x_va,
                y_val,
            )
            trees = _best_trees(model) or OOF_FIXED_TREES
        x_pr, _ = to_numeric_features(pred_df, x_cols, cat_maps=maps)
        oof.loc[pred_df.index] = model.predict(x_pr)
        n_ok = int(np.isfinite(oof.loc[pred_df.index]).sum())
        print(f"    {year}: train={len(usable):5d}  pred={len(pred_df):4d}  filled={n_ok:4d}  trees={trees}")
    filled = int(oof.notna().sum())
    print(f"  OOF {label}: {filled}/{len(fit_df)} FIT rows filled")
    return oof.to_numpy(dtype=float)


def production_stat_preds(bundle, frame):
    return predict_regressor_frame(
        bundle["model"], frame, bundle["features"], bundle.get("cat_maps") or {}
    )


def attach_stack_to_page_swap(page_df, swap_df, slpm_a, slpm_b, td_a, td_b, ctrl_a, ctrl_b):
    """Card inference: page slot A is fighter A; swap slot A is fighter B."""
    page = page_df.copy()
    swap = swap_df.copy()
    pairs = {
        "slpm": (np.asarray(slpm_a, dtype=float), np.asarray(slpm_b, dtype=float)),
        "td": (np.asarray(td_a, dtype=float), np.asarray(td_b, dtype=float)),
        "ctrl": (np.asarray(ctrl_a, dtype=float), np.asarray(ctrl_b, dtype=float)),
    }
    for name, (pred_a, pred_b) in pairs.items():
        page[f"stack_a_{name}"] = pred_a
        page[f"stack_b_{name}"] = pred_b
        page[f"stack_diff_{name}"] = pred_a - pred_b
        swap[f"stack_a_{name}"] = pred_b
        swap[f"stack_b_{name}"] = pred_a
        swap[f"stack_diff_{name}"] = pred_b - pred_a
    return page, swap


def _best_trees(model):
    best = getattr(model, "best_iteration", None)
    if best is None:
        return None
    return int(best) + 1


def print_calibration(y_true, p_pred, title="Calibration (P(A wins) vs actual)"):
    """Among fights where we said ~65%, did ~65% actually win?"""
    y_true = np.asarray(y_true, dtype=float)
    p_pred = np.clip(np.asarray(p_pred, dtype=float), 1e-6, 1 - 1e-6)
    print(f"  {title}")
    print("    predicted bin     n    mean predicted    actual A-win rate")
    edges = [0.0, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 1.01]
    for lo, hi in zip(edges, edges[1:]):
        mask = (p_pred >= lo) & (p_pred < hi)
        n = int(mask.sum())
        if n == 0:
            continue
        print(
            f"    [{lo:0.2f}, {hi:0.2f})   {n:4d}      {p_pred[mask].mean():0.3f}            {y_true[mask].mean():0.3f}"
        )
    print(f"    Brier score (lower better): {brier_score_loss(y_true, p_pred):.4f}")


def _win_baselines(test_page, y_page):
    p_half = np.full(len(y_page), 0.5)
    a_wp = pd.to_numeric(test_page.get("a_career_win_pct"), errors="coerce")
    b_wp = pd.to_numeric(test_page.get("b_career_win_pct"), errors="coerce")
    if a_wp is None:
        a_wp = pd.Series(np.nan, index=test_page.index)
        b_wp = pd.Series(np.nan, index=test_page.index)
    known = a_wp.notna() & b_wp.notna()
    fav_hat = np.where(known, (a_wp > b_wp).astype(int), 0)
    p_fav = np.where(known & (a_wp > b_wp), 0.62, np.where(known, 0.38, 0.5))
    return p_half, p_fav, fav_hat


def evaluate_win(fit, val, test, x_cols):
    print("----- Win (y_a_win) -----")
    y_col = "y_a_win"
    fit_w = fit[fit[y_col].notna()]
    val_w = val[val[y_col].notna()]
    test_w = test[test[y_col].notna()]
    test_page = page_only(test_w)

    y_fit = fit_w[y_col].astype(int)
    y_val = val_w[y_col].astype(int)
    y_page = test_page[y_col].astype(int)

    x_fit, cat_maps = to_numeric_features(fit_w, x_cols)
    x_val, _ = to_numeric_features(val_w, x_cols, cat_maps=cat_maps)
    x_page, _ = to_numeric_features(test_page, x_cols, cat_maps=cat_maps)
    w_fit = sample_weights(fit_w)

    model = fit_classifier(x_fit, y_fit, w_fit, x_val, y_val)
    trees = _best_trees(model)
    p_model = model.predict_proba(x_page)[:, 1]
    pred_hat = (p_model >= 0.5).astype(int)
    p_half, p_fav, fav_hat = _win_baselines(test_page, y_page)
    model_logloss = log_loss(y_page, np.clip(p_model, 1e-6, 1 - 1e-6))
    model_acc = accuracy_score(y_page, pred_hat)

    print(f"  Trees used (early stopping on 2024 val): {trees}")
    print(f"  Test fights (page orientation): {len(y_page)}")
    print(f"  Baseline coin flip        logloss={log_loss(y_page, p_half):.4f}")
    print(
        f"  Baseline higher win%      logloss={log_loss(y_page, np.clip(p_fav, 1e-6, 1 - 1e-6)):.4f}  "
        f"acc={accuracy_score(y_page, fav_hat):.4f}"
    )
    print(
        f"  XGBoost (weighted)        logloss={model_logloss:.4f}  "
        f"acc={model_acc:.4f}"
    )
    print_calibration(y_page, p_model)

    metrics = {
        "trees": trees,
        "n_test": int(len(y_page)),
        "logloss": float(model_logloss),
        "acc": float(model_acc),
        "brier": float(brier_score_loss(y_page, np.clip(p_model, 1e-6, 1 - 1e-6))),
        "n_features": int(len(x_cols)),
    }

    vet = veteran_mask(test_page)
    if int(vet.sum()) >= 30:
        y_v = y_page[vet.to_numpy()]
        p_v = p_model[vet.to_numpy()]
        vet_logloss = log_loss(y_v, np.clip(p_v, 1e-6, 1 - 1e-6))
        vet_acc = accuracy_score(y_v, (p_v >= 0.5).astype(int))
        print(
            f"  Veteran-only test (both ≥ {MIN_FIGHTS_VETERAN} UFC fights): {int(vet.sum())} fights  "
            f"logloss={vet_logloss:.4f}  "
            f"acc={vet_acc:.4f}"
        )
        metrics["veteran_test_n"] = int(vet.sum())
        metrics["veteran_test_logloss"] = float(vet_logloss)
        metrics["veteran_test_acc"] = float(vet_acc)

        fit_v = fit_w[veteran_mask(fit_w)]
        val_v = val_w[veteran_mask(val_w)]
        if len(fit_v) >= 200 and len(val_v) >= 50:
            x_fit_v, _ = to_numeric_features(fit_v, x_cols, cat_maps=cat_maps)
            x_val_v, _ = to_numeric_features(val_v, x_cols, cat_maps=cat_maps)
            model_v = fit_classifier(
                x_fit_v,
                fit_v[y_col].astype(int),
                sample_weights(fit_v),
                x_val_v,
                val_v[y_col].astype(int),
            )
            test_v = test_page[vet]
            x_test_v, _ = to_numeric_features(test_v, x_cols, cat_maps=cat_maps)
            p_vv = model_v.predict_proba(x_test_v)[:, 1]
            y_vv = test_v[y_col].astype(int)
            vv_logloss = log_loss(y_vv, np.clip(p_vv, 1e-6, 1 - 1e-6))
            vv_acc = accuracy_score(y_vv, (p_vv >= 0.5).astype(int))
            print(
                f"  Veteran-trained XGBoost on same fights: "
                f"logloss={vv_logloss:.4f}  "
                f"acc={vv_acc:.4f}"
            )
            metrics["veteran_trained_logloss"] = float(vv_logloss)
            metrics["veteran_trained_acc"] = float(vv_acc)
    print()
    return {
        "model": model,
        "features": list(x_cols),
        "cat_maps": cat_maps,
        "metrics": metrics,
    }


def evaluate_regressor(fit, val, test, x_cols, y_col, baseline_col, title):
    print(f"----- {title} ({y_col}) -----")
    fit_r = fit[fit[y_col].notna()].copy()
    val_r = val[val[y_col].notna()].copy()
    test_r = page_only(test[test[y_col].notna()])
    y_fit = pd.to_numeric(fit_r[y_col], errors="coerce")
    y_val = pd.to_numeric(val_r[y_col], errors="coerce")
    y_test = pd.to_numeric(test_r[y_col], errors="coerce")
    fit_r, y_fit = fit_r.loc[y_fit.notna()], y_fit.loc[y_fit.notna()]
    val_r, y_val = val_r.loc[y_val.notna()], y_val.loc[y_val.notna()]
    test_r, y_test = test_r.loc[y_test.notna()], y_test.loc[y_test.notna()]

    x_fit, cat_maps = to_numeric_features(fit_r, x_cols)
    x_val, _ = to_numeric_features(val_r, x_cols, cat_maps=cat_maps)
    x_test, _ = to_numeric_features(test_r, x_cols, cat_maps=cat_maps)
    w_fit = sample_weights(fit_r)

    if baseline_col in test_r.columns:
        fill = float(np.nanmedian(y_fit))
        base = pd.to_numeric(test_r[baseline_col], errors="coerce").fillna(fill)
        base_mae = mean_absolute_error(y_test, base)
        base_name = baseline_col
    else:
        fill = float(np.nanmedian(y_fit))
        base = np.full(len(y_test), fill)
        base_mae = mean_absolute_error(y_test, base)
        base_name = f"fit median ({fill:.3f})"

    model = fit_regressor(x_fit, y_fit, w_fit, x_val, y_val)
    pred = model.predict(x_test)
    trees = _best_trees(model)
    model_mae = mean_absolute_error(y_test, pred)
    print(f"  Trees used (early stopping on 2024 val): {trees}")
    print(f"  Test fights: {len(y_test)}")
    print(f"  Baseline ({base_name})  MAE={base_mae:.4f}")
    print(f"  XGBoost (weighted)       MAE={model_mae:.4f}")
    print()
    return {
        "model": model,
        "features": list(x_cols),
        "cat_maps": cat_maps,
        "metrics": {
            "trees": trees,
            "n_test": int(len(y_test)),
            "mae": float(model_mae),
            "baseline_mae": float(base_mae),
            "baseline_name": base_name,
            "n_features": int(len(x_cols)),
        },
    }


def save_artifacts(bundles, extra_meta=None):
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    meta = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "fit_start": FIT_START.isoformat(),
        "val_start": VAL_START.isoformat(),
        "test_start": TEST_START.isoformat(),
        "models": {},
    }
    if extra_meta:
        meta.update(extra_meta)
    for name, bundle in bundles.items():
        path = os.path.join(ARTIFACT_DIR, f"{name}.joblib")
        joblib.dump(
            {
                "name": name,
                "model": bundle["model"],
                "features": bundle["features"],
                "cat_maps": bundle["cat_maps"],
                "metrics": bundle["metrics"],
            },
            path,
        )
        meta["models"][name] = {
            "path": os.path.basename(path),
            "n_features": len(bundle["features"]),
            "metrics": bundle["metrics"],
        }
        print(f"  saved {path}")
    meta_path = os.path.join(ARTIFACT_DIR, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2, default=str)
    print(f"  saved {meta_path}")
    return meta_path


def load_artifact(name):
    path = os.path.join(ARTIFACT_DIR, f"{name}.joblib")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Missing {path}. Run `python Models/train_baseline.py` first."
        )
    return joblib.load(path)


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
    print(f"Win / SLpM recipe:     {len(win_cols)} columns  (pre-grappling)")
    print(
        f"TD / control recipe:   {len(grappling_cols)} columns  "
        f"(+{len(grappling_cols) - len(win_cols)} products/class/entropy/funnel)"
    )
    print(
        "Sample weights: debut=0.35, either side < 5 UFC fights=0.55, otherwise=1.0"
    )
    print()

    if fit.empty or val.empty or test.empty:
        raise SystemExit("A time slice is empty. Check FIT_START / VAL_START / TEST_START.")

    slpm_bundle = evaluate_regressor(
        fit, val, test, win_cols,
        y_col="y_a_sig_str_landed_pm",
        baseline_col="a_career_sig_str_landed_pm_avg",
        title="Sig strikes landed per minute",
    )
    td_bundle = evaluate_regressor(
        fit, val, test, grappling_cols,
        y_col="y_a_td_landed",
        baseline_col="a_career_td_landed_avg",
        title="Takedowns landed (per fight)",
    )
    ctrl_bundle = evaluate_regressor(
        fit, val, test, grappling_cols,
        y_col="y_a_ctrl_seconds",
        baseline_col="a_career_ctrl_seconds_avg",
        title="Control time (seconds per fight)",
    )

    print("----- Stack fight-shape preds into win -----")
    print("FIT uses walk-forward OOF (a 2019 fight is not in its own SLpM/TD/ctrl model).")
    print("VAL/TEST use the production stat models (trained on FIT, stopped on 2024).")
    print("This-fight actual SLpM/TD/ctrl are never win features.")
    oof_slpm = walk_forward_oof_preds(fit, win_cols, "y_a_sig_str_landed_pm", "SLpM")
    oof_td = walk_forward_oof_preds(fit, grappling_cols, "y_a_td_landed", "TD")
    oof_ctrl = walk_forward_oof_preds(fit, grappling_cols, "y_a_ctrl_seconds", "ctrl")
    fit_s = apply_stack_columns(fit, {"slpm": oof_slpm, "td": oof_td, "ctrl": oof_ctrl})
    val_s = apply_stack_columns(
        val,
        {
            "slpm": production_stat_preds(slpm_bundle, val),
            "td": production_stat_preds(td_bundle, val),
            "ctrl": production_stat_preds(ctrl_bundle, val),
        },
    )
    test_s = apply_stack_columns(
        test,
        {
            "slpm": production_stat_preds(slpm_bundle, test),
            "td": production_stat_preds(td_bundle, test),
            "ctrl": production_stat_preds(ctrl_bundle, test),
        },
    )
    win_cols_stacked = list(win_cols) + [col for col in STACK_FEATURE_COLS if col not in win_cols]
    print(
        f"Win features: {len(win_cols)} pre-grappling + {len(STACK_FEATURE_COLS)} stacked "
        f"SLpM/TD/ctrl = {len(win_cols_stacked)}"
    )
    print()
    win_bundle = evaluate_win(fit_s, val_s, test_s, win_cols_stacked)

    print("Saving artifacts ...")
    save_artifacts(
        {
            "win": win_bundle,
            "slpm": slpm_bundle,
            "td": td_bundle,
            "ctrl": ctrl_bundle,
        },
        extra_meta={
            "win_n_features": len(win_cols_stacked),
            "grappling_n_features": len(grappling_cols),
            "win_recipe": (
                "pre-grappling columns plus OOF stacked SLpM/TD/ctrl "
                "(stack_a/b/diff for each); no this-fight actual stats"
            ),
            "grappling_recipe": "all fight_model_train columns except ids/labels",
            "stack_features": list(STACK_FEATURE_COLS),
        },
    )
    print("Done. Predict a card with: python Models/predict_card.py")
    print("Edit FIT_START / VAL_START / TEST_START at the top to change the era.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
