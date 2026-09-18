"""Insert performance statistics for a fighter into fighter_performance_stats."""

import re


def insert_fighter_performance_stats(connection, fighter_id, stats):
    """Insert or update the performance stats row for a fighter and return fighter_id."""

    def safe_int(v):
        if v is None:
            return None
        s = str(v).strip()
        if s == "":
            return None
        m = re.search(r"-?\d+", s.replace(",", ""))
        return int(m.group()) if m else None

    def safe_float(v):
        if v is None:
            return None
        s = str(v).strip()
        if s == "":
            return None
        s = s.replace(",", "").replace("%", "")
        m = re.search(r"-?\d+(?:\.\d+)?", s)
        return float(m.group()) if m else None

    def safe_percent(v):
        return safe_float(v)

    def parse_time_to_seconds(v):
        if v is None:
            return None
        s = str(v).strip()
        if s == "":
            return None
        if ":" in s:
            parts = s.split(":")
            try:
                nums = [float(x) for x in parts]
            except ValueError:
                return None
            if len(nums) == 2:
                return nums[0] * 60 + nums[1]
            if len(nums) == 3:
                return nums[0] * 3600 + nums[1] * 60 + nums[2]
        return safe_float(s)

    stats = stats or {}

    sql = """
    INSERT INTO fighter_performance_stats
    (
        fighter_id,
        sig_strikes_landed,
        sig_strikes_attempted,
        takedowns_landed,
        takedowns_attempted,
        sig_strikes_landed_per_min,
        sig_strikes_absorbed_per_min,
        takedown_avg_per_15_min,
        submission_avg_per_15_min,
        sig_strikes_defense,
        takedown_defense,
        knockdown_avg,
        average_fight_time,
        standing_count,
        standing_percent,
        clinch_count,
        clinch_percent,
        ground_count,
        ground_percent,
        head_count,
        head_percent,
        body_count,
        body_percent,
        leg_count,
        leg_percent,
        ko_count,
        ko_percent,
        dec_count,
        dec_percent,
        sub_count,
        sub_percent
    )
    VALUES
    (
        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
    )
    ON CONFLICT (fighter_id)
    DO UPDATE SET
        sig_strikes_landed = EXCLUDED.sig_strikes_landed,
        sig_strikes_attempted = EXCLUDED.sig_strikes_attempted,
        takedowns_landed = EXCLUDED.takedowns_landed,
        takedowns_attempted = EXCLUDED.takedowns_attempted,
        sig_strikes_landed_per_min = EXCLUDED.sig_strikes_landed_per_min,
        sig_strikes_absorbed_per_min = EXCLUDED.sig_strikes_absorbed_per_min,
        takedown_avg_per_15_min = EXCLUDED.takedown_avg_per_15_min,
        submission_avg_per_15_min = EXCLUDED.submission_avg_per_15_min,
        sig_strikes_defense = EXCLUDED.sig_strikes_defense,
        takedown_defense = EXCLUDED.takedown_defense,
        knockdown_avg = EXCLUDED.knockdown_avg,
        average_fight_time = EXCLUDED.average_fight_time,
        standing_count = EXCLUDED.standing_count,
        standing_percent = EXCLUDED.standing_percent,
        clinch_count = EXCLUDED.clinch_count,
        clinch_percent = EXCLUDED.clinch_percent,
        ground_count = EXCLUDED.ground_count,
        ground_percent = EXCLUDED.ground_percent,
        head_count = EXCLUDED.head_count,
        head_percent = EXCLUDED.head_percent,
        body_count = EXCLUDED.body_count,
        body_percent = EXCLUDED.body_percent,
        leg_count = EXCLUDED.leg_count,
        leg_percent = EXCLUDED.leg_percent,
        ko_count = EXCLUDED.ko_count,
        ko_percent = EXCLUDED.ko_percent,
        dec_count = EXCLUDED.dec_count,
        dec_percent = EXCLUDED.dec_percent,
        sub_count = EXCLUDED.sub_count,
        sub_percent = EXCLUDED.sub_percent
    """

    values = [
        fighter_id,
        safe_int(stats.get("Sig. Strikes Landed")),
        safe_int(stats.get("Sig. Strikes Attempted")),
        safe_int(stats.get("Takedowns Landed")),
        safe_int(stats.get("Takedowns Attempted")),
        safe_float(stats.get("Sig. Str. LandedPer Min")),
        safe_float(stats.get("Sig. Str. AbsorbedPer Min")),
        safe_float(stats.get("Takedown avgPer 15 Min")),
        safe_float(stats.get("Submission avgPer 15 Min")),
        safe_percent(stats.get("Sig. Str. Defense")),
        safe_percent(stats.get("Takedown Defense")),
        safe_float(stats.get("Knockdown Avg")),
        parse_time_to_seconds(stats.get("Average fight time")),
        safe_int(stats.get("standing_count")),
        safe_percent(stats.get("standing_percent")),
        safe_int(stats.get("clinch_count")),
        safe_percent(stats.get("clinch_percent")),
        safe_int(stats.get("ground_count")),
        safe_percent(stats.get("ground_percent")),
        safe_int(stats.get("head_count")),
        safe_percent(stats.get("head_percent")),
        safe_int(stats.get("body_count")),
        safe_percent(stats.get("body_percent")),
        safe_int(stats.get("leg_count")),
        safe_percent(stats.get("leg_percent")),
        safe_int(stats.get("ko/tko_count")),
        safe_percent(stats.get("ko/tko_percent")),
        safe_int(stats.get("dec_count")),
        safe_percent(stats.get("dec_percent")),
        safe_int(stats.get("sub_count")),
        safe_percent(stats.get("sub_percent")),
    ]

    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, values)
        connection.commit()
        return fighter_id
    except Exception as e:
        connection.rollback()
        print(f"DB insert error for fighter_id={fighter_id}: {e}")
        return None