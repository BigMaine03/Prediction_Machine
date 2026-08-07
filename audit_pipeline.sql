-- ============================================================
-- audit_pipeline.sql
-- Full data-quality audit for the UFC prediction pipeline.
-- Run the whole file, or section by section, in psql:
--   psql -d manas_ufc_pred_machine -f audit_pipeline.sql
-- Re-run this any time after a re-scrape or a schema/insert change.
-- ============================================================


-- ------------------------------------------------------------
-- SECTION 1: Row counts across every table -- quick sanity overview
-- ------------------------------------------------------------
SELECT 'events' AS table_name, COUNT(*) AS row_count FROM events
UNION ALL SELECT 'fighters', COUNT(*) FROM fighters
UNION ALL SELECT 'fights', COUNT(*) FROM fights
UNION ALL SELECT 'fight_metadata', COUNT(*) FROM fight_metadata
UNION ALL SELECT 'fight_totals', COUNT(*) FROM fight_totals
UNION ALL SELECT 'round_stats', COUNT(*) FROM round_stats
UNION ALL SELECT 'judge_scores', COUNT(*) FROM judge_scores
UNION ALL SELECT 'fighter_bio', COUNT(*) FROM fighter_bio
UNION ALL SELECT 'fighter_performance_stats', COUNT(*) FROM fighter_performance_stats
UNION ALL SELECT 'fighter_fight_features', COUNT(*) FROM fighter_fight_features
ORDER BY table_name;


-- ------------------------------------------------------------
-- SECTION 2: Fighter identity integrity
-- (the fighter1_id = fighter2_id bug -- should be 0 rows)
-- ------------------------------------------------------------
SELECT COUNT(*) AS self_fight_count
FROM fights
WHERE fighter1_id = fighter2_id;

-- fighters table should have no duplicate rows by URL
SELECT COUNT(*) AS total_fighters,
       COUNT(DISTINCT fighter_url) AS distinct_urls
FROM fighters;

-- any fighter_name shared by more than one fighter_id
-- (not necessarily a bug -- could be two real people with the same name --
-- but worth a manual glance if the list is long)
SELECT fighter_name, COUNT(*) AS row_count
FROM fighters
GROUP BY fighter_name
HAVING COUNT(*) > 1
ORDER BY row_count DESC;


-- ------------------------------------------------------------
-- SECTION 3: Referential integrity -- orphaned rows
-- (rows that reference a parent id that doesn't exist should never happen
-- given FK constraints, but worth confirming, and this also catches NULLs
-- where a real link was expected)
-- ------------------------------------------------------------
SELECT COUNT(*) AS fights_missing_event
FROM fights f
LEFT JOIN events e ON f.event_id = e.event_id
WHERE e.event_id IS NULL;

SELECT COUNT(*) AS fights_missing_fighter1
FROM fights f
LEFT JOIN fighters fr ON f.fighter1_id = fr.fighter_id
WHERE f.fighter1_id IS NOT NULL AND fr.fighter_id IS NULL;

SELECT COUNT(*) AS fights_missing_fighter2
FROM fights f
LEFT JOIN fighters fr ON f.fighter2_id = fr.fighter_id
WHERE f.fighter2_id IS NOT NULL AND fr.fighter_id IS NULL;

SELECT COUNT(*) AS fights_missing_totals
FROM fights f
LEFT JOIN fight_totals ft ON f.fight_id = ft.fight_id
WHERE ft.fight_id IS NULL;

SELECT COUNT(*) AS fights_missing_metadata
FROM fights f
LEFT JOIN fight_metadata fm ON f.fight_id = fm.fight_id
WHERE fm.fight_id IS NULL;

SELECT COUNT(*) AS fights_missing_round_stats
FROM fights f
LEFT JOIN round_stats rs ON f.fight_id = rs.fight_id
WHERE rs.fight_id IS NULL;


-- ------------------------------------------------------------
-- SECTION 4: round_stats sanity (the round-mislabeling bug)
-- ------------------------------------------------------------
-- no fight should have more than 5 rounds recorded
SELECT fight_id, COUNT(*) AS round_row_count
FROM round_stats
GROUP BY fight_id
HAVING COUNT(*) > 5;

-- round_number should never exceed 5
SELECT fight_id, round_number
FROM round_stats
WHERE round_number > 5 OR round_number < 1;

-- max recorded round_number per fight should not exceed fights.round
-- (fights.round is the round the fight actually ended in)
SELECT rs.fight_id, f.round AS official_final_round, MAX(rs.round_number) AS max_recorded_round
FROM round_stats rs
JOIN fights f ON rs.fight_id = f.fight_id
GROUP BY rs.fight_id, f.round
HAVING MAX(rs.round_number) > f.round;

-- fights where the recorded round count doesn't match the official final
-- round at all (e.g. fight ended round 3 but only 1 round_stats row exists)
-- NOTE: some legitimate gaps can exist if a round genuinely has no scraped
-- row -- treat this as a "worth spot-checking" list, not an automatic bug
SELECT f.fight_id, f.round AS official_final_round, COUNT(rs.round_stat_id) AS recorded_rounds
FROM fights f
LEFT JOIN round_stats rs ON f.fight_id = rs.fight_id
GROUP BY f.fight_id, f.round
HAVING COUNT(rs.round_stat_id) != f.round
ORDER BY ABS(COUNT(rs.round_stat_id) - f.round) DESC
LIMIT 30;


-- ------------------------------------------------------------
-- SECTION 5: Null / coverage audit -- every populated column,
-- every table (catches "brittle selector silently returns nothing"
-- bugs like the sig_str one we found)
-- ------------------------------------------------------------
SELECT
    COUNT(*) AS total,
    COUNT(fighter1_sig_str) AS has_sig_str,
    COUNT(fighter1_sig_str_percent) AS has_sig_str_pct,
    COUNT(fighter1_head) AS has_head,
    COUNT(fighter1_body) AS has_body,
    COUNT(fighter1_leg) AS has_leg,
    COUNT(fighter1_distance) AS has_distance,
    COUNT(fighter1_clinch) AS has_clinch,
    COUNT(fighter1_ground) AS has_ground
FROM fight_totals;

SELECT
    COUNT(*) AS total,
    COUNT(fighter1_kd) AS has_kd,
    COUNT(fighter1_sig_str) AS has_sig_str,
    COUNT(fighter1_td) AS has_td,
    COUNT(fighter1_sub_att) AS has_sub_att,
    COUNT(fighter1_ctrl) AS has_ctrl,
    COUNT(raw_round) AS has_raw_round
FROM round_stats;

SELECT
    COUNT(*) AS total,
    COUNT(page_title) AS has_title,
    COUNT(canonical_url) AS has_canonical
FROM fight_metadata;

SELECT
    COUNT(*) AS total,
    COUNT(referee) AS has_referee,
    COUNT(method) AS has_method,
    COUNT(weight_class) AS has_weight_class,
    COUNT(finish_details) AS has_finish_details
FROM fights;

SELECT COUNT(*) AS total, COUNT(judge_name) AS has_name, COUNT(score) AS has_score
FROM judge_scores;

SELECT COUNT(*) AS total, COUNT(event_location) AS has_location, COUNT(headline) AS has_headline
FROM events;

SELECT
    COUNT(*) AS total,
    COUNT(height) AS has_height,
    COUNT(reach) AS has_reach,
    COUNT(fighting_style) AS has_style
FROM fighter_bio;

SELECT
    COUNT(*) AS total,
    COUNT(sig_strikes_landed) AS has_sig,
    COUNT(takedown_defense) AS has_td_def
FROM fighter_performance_stats;

SELECT
    COUNT(*) AS total,
    COUNT(momentum_sig_str_landed_avg) AS has_momentum_sig_str,
    COUNT(career_sig_str_landed_avg) AS has_career_sig_str,
    COUNT(momentum_td_landed_avg) AS has_momentum_td
FROM fighter_fight_features;


-- ------------------------------------------------------------
-- SECTION 6: Value-range sanity checks
-- (catches parsing bugs that produce technically-non-null but
-- impossible values -- e.g. percentages outside 0-100, landed > attempted)
-- ------------------------------------------------------------

-- fight_totals: sig_str_percent should be a plain "NN%" or "---"
SELECT fight_id, fighter1_sig_str_percent
FROM fight_totals
WHERE fighter1_sig_str_percent IS NOT NULL
  AND fighter1_sig_str_percent !~ '^\d{1,3}%$'
  AND fighter1_sig_str_percent NOT IN ('---', '--')
LIMIT 20;

-- fight_totals: "X of Y" fields where X > Y (landed more than attempted --
-- impossible)
SELECT fight_id, fighter1_sig_str
FROM fight_totals
WHERE fighter1_sig_str ~ '^\d+ of \d+$'
  AND split_part(fighter1_sig_str, ' of ', 1)::int > split_part(fighter1_sig_str, ' of ', 2)::int
LIMIT 20;

-- events: no event dated in the future beyond a reasonable buffer
-- (adjust the interval if you deliberately scrape announced future cards)
SELECT event_id, headline, event_date
FROM events
WHERE event_date > CURRENT_DATE + INTERVAL '30 days'
ORDER BY event_date DESC
LIMIT 20;

-- events: no event dated before the UFC's actual founding (Nov 1993) --
-- catches date-parsing bugs producing garbage years
SELECT event_id, headline, event_date
FROM events
WHERE event_date < '1993-11-01'
ORDER BY event_date
LIMIT 20;

-- fighter_bio: height/reach in wildly impossible ranges (inches assumed)
SELECT fighter_id, height, reach
FROM fighter_bio
WHERE height IS NOT NULL AND (height < 48 OR height > 90)
   OR reach IS NOT NULL AND (reach < 48 OR reach > 100);


-- ------------------------------------------------------------
-- SECTION 7: fighter_fight_features completeness
-- (should have exactly 2 rows per fight -- one per fighter -- for every
-- fight that has two known fighters)
-- ------------------------------------------------------------
SELECT f.fight_id, COUNT(fff.fighter_fight_feature_id) AS feature_row_count
FROM fights f
LEFT JOIN fighter_fight_features fff ON f.fight_id = fff.fight_id
WHERE f.fighter1_id IS NOT NULL AND f.fighter2_id IS NOT NULL
GROUP BY f.fight_id
HAVING COUNT(fff.fighter_fight_feature_id) != 2
LIMIT 30;

-- overall count check: should be ~ 2x the number of eligible fights
SELECT
    (SELECT COUNT(*) FROM fights WHERE fighter1_id IS NOT NULL AND fighter2_id IS NOT NULL) * 2 AS expected_feature_rows,
    (SELECT COUNT(*) FROM fighter_fight_features) AS actual_feature_rows;

-- momentum values should never be negative, and percent averages should
-- fall within 0-100
SELECT fighter_fight_feature_id, fighter_id, fight_id, momentum_sig_str_percent_avg
FROM fighter_fight_features
WHERE momentum_sig_str_percent_avg IS NOT NULL
  AND (momentum_sig_str_percent_avg < 0 OR momentum_sig_str_percent_avg > 100);

SELECT fighter_fight_feature_id, fighter_id, fight_id, momentum_sig_str_landed_avg
FROM fighter_fight_features
WHERE momentum_sig_str_landed_avg < 0;
