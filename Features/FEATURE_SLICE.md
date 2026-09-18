# Feature slice: defense, rates, last-N windows, corner-safe training

This document describes the slice implemented on top of the Layer 2 facts /
Layer 3 features pipeline. Rebuild with:

```bash
python Features/run_feature_pipeline.py
```

Use Anaconda Python if the system `python3` does not have pandas/numpy/psycopg2.

Train models on **`fight_model_train`**, not `fight_model_rows`.

---

## Why this slice

Four gaps were blocking a truthful model:

0. **Page-order leak** — UFCStats often lists the winner first, so `fighter1` is not a random corner.
1. **Defense is the other person** — absorbed strikes / TD defense live on the opponent’s fact row.
2. **Counts are not rates** — 80 strikes in 90 seconds is not the same as 80 in 25 minutes.
3. **Career vs recent form** — 18-month means existed; last-3 / last-5 and *trend* (recent − career) did not.

---

## Layer 2 facts (`fighter_fight_facts`)

After both corners of a fight are parsed, each row is paired with its opponent.

### Opponent-linked (this fight)

| Column | Meaning |
|--------|---------|
| `sig_str_absorbed` | Opponent significant strikes landed |
| `sig_str_opp_attempted` | Opponent significant strikes attempted |
| `strike_defense` | `100 - opponent accuracy`. NULL if opponent attempted 0 (not 0%) |
| `td_absorbed` / `td_opp_attempted` / `td_defense` | Same pattern for takedowns |
| `kd_absorbed` | Opponent knockdowns |
| `ctrl_against_seconds` | Opponent control time |
| `sig_str_diff` | landed − absorbed |
| `td_diff` | TD landed − TD absorbed |
| `ctrl_diff` | control − control against |

### Per-minute (denominator = `fight_seconds`)

`fight_minutes` = `fight_seconds / 60`. NULL if duration missing or not positive.

| Column | Formula |
|--------|---------|
| `sig_str_landed_pm` | landed / minutes |
| `sig_str_absorbed_pm` | absorbed / minutes |
| `sig_str_attempted_pm` | attempted / minutes |
| `td_landed_pm` / `td_attempted_pm` | TD / minutes |
| `kd_pm` / `sub_att_pm` / `ctrl_seconds_pm` | count / minutes |
| `pace` | `(sig attempted + TD attempted) / minutes` |

These are **this-fight actuals** (labels when predicting stats). They are **not** pre-fight features until rolled in Layer 3.

---

## Layer 3 features (`fighter_fight_features`)

Still one row per fighter × fight. Every rolling stat uses only fights with `event_date` **strictly before** this fight.

### Windows (valid-for-stats fights only)

NC / DQ still count toward `fights_in_last_18mo` (activity) but are excluded from averages.

| Prefix | Window |
|--------|--------|
| `career_*_avg` | All prior valid fights |
| `momentum_*_avg` | Prior valid fights in trailing 18 months |
| `last3_*_avg` | Last 3 prior **valid** fights |
| `last5_*_avg` | Last 5 prior **valid** fights |

Stats rolled in every window include offense, style mix, defense, diffs, and per-minute rates (see `Features/feature_columns.py` `STAT_COLS`).

If a window is empty, the value **falls back to career** and a flag is set:

- `used_career_fallback_momentum`
- `used_career_fallback_last3`
- `used_career_fallback_last5`

Counts (not averages): `career_fights_count`, `last3_fights_count`, `last5_fights_count` = number of valid fights actually in that window (0 on debuts).

### Trends (style evolution / form)

| Column | Meaning |
|--------|---------|
| `trend_last5_vs_career_{stat}` | last5 avg − career avg (positive = improving vs own baseline) |
| `trend_last3_vs_last5_{stat}` | last3 avg − last5 avg (short-term acceleration) |

Debuts stay NULL (no prior valid fights).

Tier 2 history (streaks, last-3/5 **record**, finish/decision rate, title/5-round counts) is unchanged.

`result` on this table is still **this fight’s outcome** (debug label). Do not use it as X.

---

## Model tables

### `fight_model_rows` — one row per fight, **page order**

- `f1_*` / `f2_*` = pre-fight features for UFCStats fighter1 / fighter2
- `diff_*` = f1 − f2 for a curated matchup subset
- `y_*` = this-fight labels from facts (including absorbed, diffs, per-minute)
- `f1_ape_index` = reach − height (NULL if either missing)

Use this to **inspect** a card. Do **not** train `y_fighter1_win` on this view as-is: through ~2009 fighter1 won 100% of the time because the winner is listed first.

### `fight_model_train` — two rows per fight (training)

| `orientation` | Slot A | Slot B | `y_a_win` |
|---------------|--------|--------|-----------|
| `page` | page fighter1 | page fighter2 | 1 iff fighter1 won |
| `swapped` | page fighter2 | page fighter1 | 1 iff fighter2 won |

Diffs flip sign on the swapped row. Draws / NC keep `y_a_win` NULL.

After the swap, slot-A win rate is **~50% in every era**, including the 1990s.

Also includes `a_ape_index` / `b_ape_index` / `diff_ape_index`.

**Train on `fight_model_train`.** Time-split on `event_date`. Both orientations of the same fight share a date; that is intentional (symmetry), not leakage of the future.

---

## Finish mix + adaptability entropy (item 4)

Among prior wins classified as KO_TKO / SUB / DEC (DQ ignored in the mix):

| Column | Meaning |
|--------|---------|
| `career_ko_rate` / `career_sub_rate` | Share of classified prior wins |
| `career_decision_rate` | Unchanged: DEC / **all** prior wins (includes DQ in the denominator) |
| `career_method_entropy` | Shannon entropy of (KO, SUB, DEC) shares, divided by log2(3). 0 = specialist, 1 = even mix |
| `last5_ko_rate` / `last5_sub_rate` / `last5_decision_rate` / `last5_method_entropy` | Same, using wins inside the last 5 prior fights |

## Cardio (censoring-aware)

This is **output sustainability**, not “winning economically.” Low volume with good rounds is efficiency, not gas.

Per fight on `fighter_fight_facts`:

| Column | Rule |
|--------|------|
| `reached_round_3` | A round-3 row exists |
| `completed_round_3` | R3 was a full (or ≥ 4:50) round. A 0:20 R3 KO does **not** count |
| `cardio_r3_r1_att` / `_landed` | Round 3 / round 1 (diagnostic; 3-round gas) |
| `cardio_r5_r1_att` | Round 5 / round 1 when R5 exists |
| `cardio_distance_att` | R5/R1 if 5-rounder went the distance, else R3/R1 if 3-rounder did |
| `cardio_consec_att` | Mean of consecutive round ratios on distance fights |
| `last_completed_round` | Last **full** round (≥ 4:50). Not the truncated stoppage round |
| `cardio_five_stoppage_att` | 5-round fight that did **not** go the distance, but completed ≥ R3: last-full / R1 |
| `cardio_observed_att` | Distance ratio if they went 25 min; else 5-round stoppage ratio |
| `late_td_*` / `late_kd` / `late_ctrl_seconds` | Rounds 4–5 on 5-rounders, round 3+ otherwise |

Pre-fight (prior fights only):

| Column | Rule |
|--------|------|
| `career_cardio_distance_att_avg` | Mean **distance** ratio (R5/R1 or R3/R1) on fights that went the distance |
| `career_cardio_consec_att_avg` | Mean consecutive-round path on those same fights |
| `career_n_distance` | How many prior fights went the distance |
| `career_cardio_r3_r1_att_avg` | Still stored: completed-R3 ratio (includes mid-fight 5-round R3) |
| `last5_*` / `last3_*` | Same windows |

A debut or a KO specialist with `career_n_distance = 0` has NULL distance cardio — not 0.4.

## Opponent quality as-of-date (item 6)

No rankings. Quality is the opponent’s **own** pre-fight record at this `event_date`.

Win % = prior W / (W+L). Draws and NC are out of the denominator. Debuts are NULL, not 0.00.

### Tonight’s opponent (`opp_asof_*`)

Copied from the opponent’s feature row for **this same fight** (their history strictly before T):

| Column | Meaning |
|--------|---------|
| `opp_asof_win_pct` | Opponent career W/(W+L) as of T |
| `opp_asof_last5_win_pct` | Opponent last-5 decided fights |
| `opp_asof_finish_rate` / `opp_asof_ko_rate` | Among opponent’s prior wins |
| `opp_asof_n_fights` / `opp_asof_n_decided` | How many UFC fights / decided fights they already had |
| `opp_asof_debut` | Opponent has no prior UFC fight |
| `career_win_pct` / `last5_win_pct` | **Your** same metrics (so the wide table can compare a vs b) |

On `fight_model_train`, `a_opp_asof_win_pct` should equal `b_career_win_pct` (and vice versa).

### Strength of schedule (average of **past** opponents)

For fight T, average `opp_asof_*` over **your prior fights** (each opponent’s quality as of *that* past date — still no look-ahead):

| Column | Meaning |
|--------|---------|
| `career_avg_opp_win_pct` | Mean quality of everyone you’ve already fought |
| `last5_avg_opp_win_pct` | Same, last 5 opponents |
| `last3_avg_opp_win_pct` | Last 3 |
| `career_avg_opp_last5_win_pct` | Mean of those opponents’ **recent** form |
| `career_avg_opp_finish_rate` | Have you been fighting finishers? |
| `career_avg_opp_n_fights` | Have you been fighting veterans? |
| `career_n_opp_known` | How many past opponents had a defined win % (skips debuts) |

## What this is not (still later)

- Rankings / top-15
- Stance / age-at-fight (bio age is a current snapshot — leakage)
- KMeans style labels
- Judge-based fight IQ (`judge_scores.score` still text)

---

## Files

| File | Role |
|------|------|
| `Features/feature_columns.py` | Column name source of truth |
| `Features/build_fight_facts.py` | Pair corners + per-minute |
| `Features/build_fighter_features.py` | last-3/5 + trends |
| `Database/create_fact_tables.py` | New fact columns |
| `Database/create_feature_tables.py` | Feature columns + both views |
| `Features/run_feature_pipeline.py` | Orchestrator |

---

## Suggested next model step

```sql
SELECT * FROM fight_model_train
WHERE event_date < '2024-01-01';   -- train
-- test: event_date >= '2024-01-01'
-- target: y_a_win  (drop rows where it is NULL, or model draws/NC separately)
-- features: a_*, b_*, diff_*  (never y_*)
```
