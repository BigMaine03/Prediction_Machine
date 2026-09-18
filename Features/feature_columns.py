"""Single source of truth for derived fact/feature column names.

Import this from the fact builder, feature builder, and DDL helpers so
window prefixes and export lists cannot drift apart.
"""

# Numeric columns on fighter_fight_facts that are rolled into
# career / 18mo / last-3 / last-5 averages (and last-N vs career trends).
STAT_COLS = [
    "sig_str_landed",
    "sig_str_attempted",
    "sig_str_percent",
    "td_landed",
    "td_attempted",
    "td_percent",
    "kd",
    "sub_att",
    "rev",
    "ctrl_seconds",
    "distance_landed",
    "clinch_landed",
    "ground_landed",
    "distance_pct",
    "clinch_pct",
    "ground_pct",
    "sig_str_absorbed",
    "strike_defense",
    "td_absorbed",
    "td_defense",
    "kd_absorbed",
    "ctrl_against_seconds",
    "sig_str_diff",
    "td_diff",
    "ctrl_diff",
    "sig_str_landed_pm",
    "sig_str_absorbed_pm",
    "sig_str_attempted_pm",
    "td_landed_pm",
    "td_attempted_pm",
    "kd_pm",
    "sub_att_pm",
    "ctrl_seconds_pm",
    "pace",
    "style_entropy",
    "ctrl_per_td",
    "ground_per_ctrl_min",
    "sub_per_td",
]

# Added onto fighter_fight_facts after pairing the two corners + converting
# volume to per-minute. Kept here so DDL ALTERs match the builder INSERT list.
FACT_OPP_AND_RATE_COLS = [
    "sig_str_absorbed",
    "sig_str_opp_attempted",
    "strike_defense",
    "td_absorbed",
    "td_opp_attempted",
    "td_defense",
    "kd_absorbed",
    "ctrl_against_seconds",
    "sig_str_diff",
    "td_diff",
    "ctrl_diff",
    "fight_minutes",
    "sig_str_landed_pm",
    "sig_str_absorbed_pm",
    "sig_str_attempted_pm",
    "td_landed_pm",
    "td_attempted_pm",
    "kd_pm",
    "sub_att_pm",
    "ctrl_seconds_pm",
    "pace",
]

WINDOWS = ["career", "momentum", "last3", "last5"]
TREND_PAIRS = [("last5", "career"), ("last3", "last5")]


def window_stat_col(window, stat):
    return f"{window}_{stat}_avg"


def trend_col(newer, older, stat):
    return f"trend_{newer}_vs_{older}_{stat}"


HISTORY_FEATURE_COLS = [
    "days_since_last_fight",
    "fights_in_last_18mo",
    "fights_in_last_24mo",
    "stale_flag",
    "used_career_fallback_momentum",
    "used_career_fallback_last3",
    "used_career_fallback_last5",
    "debut_flag",
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
    "career_finish_rate",
    "career_decision_rate",
    "career_ko_rate",
    "career_sub_rate",
    "career_method_entropy",
    "last5_ko_rate",
    "last5_sub_rate",
    "last5_decision_rate",
    "last5_method_entropy",
    "num_five_round_fights",
    "num_title_fights",
    "avg_rounds_fought",
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
    "career_win_pct",
    "last5_win_pct",
    "opp_asof_win_pct",
    "opp_asof_last5_win_pct",
    "opp_asof_finish_rate",
    "opp_asof_ko_rate",
    "opp_asof_n_fights",
    "opp_asof_n_decided",
    "opp_asof_debut",
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
    "first_time_in_class",
    "n_prior_in_class",
    "switched_class",
    "class_move",
    "career_mix_entropy",
    "last5_mix_entropy",
]

# Pairwise terms on fight_model_train. {a}/{b} are view prefixes (f1/f2 or a/b).
# expected_* uses (1 - defense/100) so a high TDD shrinks expected takedowns.
MATCHUP_PRODUCTS = [
    (
        "prod_expected_td_pm",
        "{a}_career_td_attempted_pm_avg * (1.0 - {b}_career_td_defense_avg / 100.0)",
    ),
    (
        "prod_expected_td_pm_vs_recent_def",
        "{a}_career_td_attempted_pm_avg * (1.0 - {b}_last5_td_defense_avg / 100.0)",
    ),
    (
        "prod_last5_expected_td_pm",
        "{a}_last5_td_attempted_pm_avg * (1.0 - {b}_last5_td_defense_avg / 100.0)",
    ),
    (
        "prod_expected_sig_pm",
        "{a}_career_sig_str_attempted_pm_avg * (1.0 - {b}_career_strike_defense_avg / 100.0)",
    ),
    (
        "prod_last5_expected_sig_pm",
        "{a}_last5_sig_str_attempted_pm_avg * (1.0 - {b}_last5_strike_defense_avg / 100.0)",
    ),
    (
        "prod_reach_x_opp_distance_pct",
        "{a}_reach * {b}_career_distance_pct_avg",
    ),
    (
        "prod_ground_x_td_att_pm",
        "{a}_career_ground_pct_avg * {a}_career_td_attempted_pm_avg",
    ),
]

# Win / SLpM keep the pre-732 recipe. TD and control use the full set
# (matchup products, first-time-in-class, style entropy, TD funnel).
WIN_RECIPE_EXCLUDE_SUBSTRINGS = (
    "prod_",
    "first_time_in_class",
    "n_prior_in_class",
    "switched_class",
    "class_move",
    "mix_entropy",
    "style_entropy",
    "ctrl_per_td",
    "ground_per_ctrl",
    "sub_per_td",
)


def col_in_win_recipe(name):
    return not any(token in name for token in WIN_RECIPE_EXCLUDE_SUBSTRINGS)


def add_matchup_products(frame, a="a", b="b"):
    """Evaluate MATCHUP_PRODUCTS on a fight-level frame with a_*/b_* columns."""
    out = frame.copy()
    for alias, expr in MATCHUP_PRODUCTS:
        formula = expr.format(a=a, b=b)
        out[alias] = out.eval(formula, engine="python")
    return out

# Per-fight cardio stored on fighter_fight_facts (this-fight actuals, not pre-fight).
CARDIO_FACT_COLS = [
    "r1_sig_str_attempted",
    "r1_sig_str_landed",
    "r3_sig_str_attempted",
    "r3_sig_str_landed",
    "reached_round_3",
    "completed_round_3",
    "cardio_r3_r1_att",
    "cardio_r3_r1_landed",
    "late_td_landed",
    "late_td_attempted",
    "late_kd",
    "late_ctrl_seconds",
    "r5_sig_str_attempted",
    "r5_sig_str_landed",
    "reached_round_5",
    "completed_round_5",
    "went_the_distance",
    "cardio_r5_r1_att",
    "cardio_r5_r1_landed",
    "cardio_distance_att",
    "cardio_distance_landed",
    "cardio_consec_att",
    "cardio_consec_landed",
    "last_completed_round",
    "cardio_last_full_r1_att",
    "cardio_last_full_r1_landed",
    "cardio_five_stoppage_att",
    "cardio_five_stoppage_landed",
    "cardio_observed_att",
    "cardio_observed_landed",
]


def all_window_stat_cols():
    cols = []
    for window in WINDOWS:
        for stat in STAT_COLS:
            cols.append(window_stat_col(window, stat))
    return cols


def all_trend_cols():
    cols = []
    for newer, older in TREND_PAIRS:
        for stat in STAT_COLS:
            cols.append(trend_col(newer, older, stat))
    return cols


def feature_export_cols():
    """Pre-fight columns copied onto fight_model_rows as f1_* / f2_*.

    Does not include `result` (this-fight label).
    """
    return list(HISTORY_FEATURE_COLS) + all_window_stat_cols() + all_trend_cols()


FEATURE_EXPORT_COLS = feature_export_cols()

# Matchup diffs on the wide table. Curated so the view stays usable;
# both corners still get the full FEATURE_EXPORT_COLS as a_* / b_* / f1_* / f2_*.
DIFF_COLS = [
    "days_since_last_fight",
    "career_fights_count",
    "win_streak",
    "loss_streak",
    "career_wins",
    "career_losses",
    "career_finish_rate",
    "career_decision_rate",
    "career_ko_rate",
    "career_sub_rate",
    "career_method_entropy",
    "last5_ko_rate",
    "last5_sub_rate",
    "last5_decision_rate",
    "last5_method_entropy",
    "num_five_round_fights",
    "num_title_fights",
    "avg_rounds_fought",
    "career_cardio_r3_r1_att_avg",
    "last5_cardio_r3_r1_att_avg",
    "career_cardio_distance_att_avg",
    "last5_cardio_distance_att_avg",
    "career_cardio_consec_att_avg",
    "career_n_r3_complete",
    "career_n_distance",
    "career_cardio_observed_att_avg",
    "career_n_observed",
    "career_win_pct",
    "last5_win_pct",
    "opp_asof_win_pct",
    "opp_asof_last5_win_pct",
    "opp_asof_finish_rate",
    "career_avg_opp_win_pct",
    "last5_avg_opp_win_pct",
    "career_avg_opp_finish_rate",
    "career_mix_entropy",
    "last5_mix_entropy",
    "career_style_entropy_avg",
    "career_ctrl_per_td_avg",
    "career_late_td_avg",
    "career_late_kd_avg",
    "career_sig_str_landed_pm_avg",
    "last5_sig_str_landed_pm_avg",
    "momentum_sig_str_landed_pm_avg",
    "career_sig_str_absorbed_pm_avg",
    "last5_sig_str_absorbed_pm_avg",
    "career_strike_defense_avg",
    "last5_strike_defense_avg",
    "career_td_landed_pm_avg",
    "last5_td_landed_pm_avg",
    "career_td_defense_avg",
    "last5_td_defense_avg",
    "career_kd_pm_avg",
    "last5_kd_pm_avg",
    "career_ctrl_seconds_pm_avg",
    "last5_ctrl_seconds_pm_avg",
    "career_pace_avg",
    "last5_pace_avg",
    "career_sig_str_diff_avg",
    "last5_sig_str_diff_avg",
    "career_td_diff_avg",
    "last5_td_diff_avg",
    "career_distance_pct_avg",
    "last5_distance_pct_avg",
    "career_clinch_pct_avg",
    "last5_clinch_pct_avg",
    "career_ground_pct_avg",
    "last5_ground_pct_avg",
    "trend_last5_vs_career_sig_str_landed_pm",
    "trend_last5_vs_career_sig_str_diff",
    "trend_last5_vs_career_pace",
    "trend_last5_vs_career_td_landed_pm",
    "trend_last5_vs_career_distance_pct",
    "trend_last5_vs_career_ground_pct",
]

LABEL_FACT_COLS = [
    "sig_str_landed",
    "sig_str_attempted",
    "sig_str_absorbed",
    "td_landed",
    "td_attempted",
    "td_absorbed",
    "kd",
    "kd_absorbed",
    "sub_att",
    "ctrl_seconds",
    "ctrl_against_seconds",
    "sig_str_diff",
    "td_diff",
    "ctrl_diff",
    "distance_landed",
    "clinch_landed",
    "ground_landed",
    "fight_seconds",
    "sig_str_landed_pm",
    "sig_str_absorbed_pm",
    "td_landed_pm",
    "pace",
    "strike_defense",
    "td_defense",
]
