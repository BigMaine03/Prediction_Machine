"""Log card picks and score them against completed fights in the database."""

from datetime import date, datetime

from Database.create_card_pick_tables import create_card_pick_tables


TOSSUP_PCT = 50.0
HIGH_CONF = 0.66


def _norm_url(url):
    if not url:
        return None
    text = str(url).strip().replace("https://", "http://").rstrip("/")
    return text or None


def _name_key(name):
    return " ".join(str(name or "").strip().lower().split())


def _as_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def match_key(event_url, fighter_a_url, fighter_b_url, fighter_a_name, fighter_b_name, event_date=None):
    a = _norm_url(fighter_a_url) or _name_key(fighter_a_name)
    b = _norm_url(fighter_b_url) or _name_key(fighter_b_name)
    left, right = (a, b) if a <= b else (b, a)
    event = _norm_url(event_url) or ""
    when = (_as_date(event_date) or date.min).isoformat()
    return f"{event}|{when}|{left}|{right}"


def classify_pick(p_a):
    """TOSSUP if displayed percents are 50.0/50.0; else the side above 50%."""
    p_a = float(p_a)
    p_a = min(max(p_a, 0.0), 1.0)
    p_b = 1.0 - p_a
    pct_a = round(p_a * 100.0, 1)
    pct_b = round(p_b * 100.0, 1)
    favorite_pct = max(pct_a, pct_b)
    high = favorite_pct >= round(HIGH_CONF * 100.0, 1)
    if pct_a == TOSSUP_PCT or pct_b == TOSSUP_PCT:
        return "TOSSUP", True, False, p_a, p_b
    pick = "A" if pct_a > TOSSUP_PCT else "B"
    return pick, False, high, p_a, p_b


def _positive_id(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def upsert_card_picks(connection, event, rows):
    create_card_pick_tables(connection)
    event_url = _norm_url(event.get("url"))
    event_date = _as_date(event.get("date"))
    headline = event.get("headline")
    sql = """
    INSERT INTO card_picks (
        match_key, event_url, event_date, event_headline,
        fighter_a_name, fighter_b_name, fighter_a_url, fighter_b_url,
        fighter_a_id, fighter_b_id,
        p_a, p_b, pick, is_tossup, is_high_conf_66, predicted_at
    )
    VALUES (
        %s, %s, %s, %s,
        %s, %s, %s, %s,
        %s, %s,
        %s, %s, %s, %s, %s, now()
    )
    ON CONFLICT (match_key) DO UPDATE SET
        event_url = EXCLUDED.event_url,
        event_date = EXCLUDED.event_date,
        event_headline = EXCLUDED.event_headline,
        fighter_a_name = EXCLUDED.fighter_a_name,
        fighter_b_name = EXCLUDED.fighter_b_name,
        fighter_a_url = EXCLUDED.fighter_a_url,
        fighter_b_url = EXCLUDED.fighter_b_url,
        fighter_a_id = EXCLUDED.fighter_a_id,
        fighter_b_id = EXCLUDED.fighter_b_id,
        p_a = EXCLUDED.p_a,
        p_b = EXCLUDED.p_b,
        pick = EXCLUDED.pick,
        is_tossup = EXCLUDED.is_tossup,
        is_high_conf_66 = EXCLUDED.is_high_conf_66,
        predicted_at = now()
    WHERE card_picks.scored_at IS NULL
    """
    logged = []
    with connection.cursor() as cursor:
        for row in rows:
            pick, tossup, high, p_a, p_b = classify_pick(row["p_a"])
            key = match_key(
                event_url,
                row.get("fighter_a_url"),
                row.get("fighter_b_url"),
                row.get("fighter_a_name"),
                row.get("fighter_b_name"),
                event_date,
            )
            cursor.execute(
                sql,
                (
                    key,
                    event_url,
                    event_date,
                    headline,
                    row.get("fighter_a_name"),
                    row.get("fighter_b_name"),
                    _norm_url(row.get("fighter_a_url")),
                    _norm_url(row.get("fighter_b_url")),
                    _positive_id(row.get("fighter_a_id")),
                    _positive_id(row.get("fighter_b_id")),
                    p_a,
                    p_b,
                    pick,
                    tossup,
                    high,
                ),
            )
            logged.append(
                {
                    **row,
                    "p_a": p_a,
                    "p_b": p_b,
                    "pick": pick,
                    "is_tossup": tossup,
                    "is_high_conf_66": high,
                }
            )
    connection.commit()
    return logged


def _load_completed_fights(connection, event_urls, event_dates):
    urls = [url for url in event_urls if url]
    dates = [day for day in event_dates if day]
    if not urls and not dates:
        return []
    clauses = []
    params = []
    if urls:
        clauses.append("e.event_url = ANY(%s)")
        params.append(urls)
    if dates:
        clauses.append("e.event_date = ANY(%s)")
        params.append(dates)
    sql = f"""
    SELECT
        f.fight_id,
        e.event_url,
        e.event_date,
        f.winner_id,
        f.outcome_type,
        f.fighter1_id,
        f.fighter2_id,
        f1.fighter_name AS f1_name,
        f2.fighter_name AS f2_name,
        f1.fighter_url AS f1_url,
        f2.fighter_url AS f2_url
    FROM fights f
    JOIN events e ON e.event_id = f.event_id
    LEFT JOIN fighters f1 ON f1.fighter_id = f.fighter1_id
    LEFT JOIN fighters f2 ON f2.fighter_id = f.fighter2_id
    WHERE ({" OR ".join(clauses)})
      AND f.outcome_type IS NOT NULL
      AND f.outcome_type <> 'unknown'
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _same_fighter(pick_id, pick_url, pick_name, fight_id, fight_url, fight_name):
    if pick_id and fight_id and int(pick_id) == int(fight_id):
        return True
    pu, fu = _norm_url(pick_url), _norm_url(fight_url)
    if pu and fu and pu == fu:
        return True
    pn, fn = _name_key(pick_name), _name_key(fight_name)
    return bool(pn and fn and pn == fn)


def _winner_side(pick, fight):
    winner_id = fight.get("winner_id")
    if winner_id is None:
        return None
    winner_id = int(winner_id)
    if pick.get("fighter_a_id") and int(pick["fighter_a_id"]) == winner_id:
        return "A"
    if pick.get("fighter_b_id") and int(pick["fighter_b_id"]) == winner_id:
        return "B"
    if fight.get("fighter1_id") and int(fight["fighter1_id"]) == winner_id:
        if _same_fighter(
            pick.get("fighter_a_id"), pick.get("fighter_a_url"), pick.get("fighter_a_name"),
            fight.get("fighter1_id"), fight.get("f1_url"), fight.get("f1_name"),
        ):
            return "A"
        if _same_fighter(
            pick.get("fighter_b_id"), pick.get("fighter_b_url"), pick.get("fighter_b_name"),
            fight.get("fighter1_id"), fight.get("f1_url"), fight.get("f1_name"),
        ):
            return "B"
    if fight.get("fighter2_id") and int(fight["fighter2_id"]) == winner_id:
        if _same_fighter(
            pick.get("fighter_a_id"), pick.get("fighter_a_url"), pick.get("fighter_a_name"),
            fight.get("fighter2_id"), fight.get("f2_url"), fight.get("f2_name"),
        ):
            return "A"
        if _same_fighter(
            pick.get("fighter_b_id"), pick.get("fighter_b_url"), pick.get("fighter_b_name"),
            fight.get("fighter2_id"), fight.get("f2_url"), fight.get("f2_name"),
        ):
            return "B"
    return None


def _match_fight(pick, fights):
    for fight in fights:
        event_ok = False
        pick_url = _norm_url(pick.get("event_url"))
        fight_url = _norm_url(fight.get("event_url"))
        if pick_url and fight_url and pick_url == fight_url:
            event_ok = True
        pick_date = _as_date(pick.get("event_date"))
        fight_date = _as_date(fight.get("event_date"))
        if pick_date and fight_date and pick_date == fight_date:
            event_ok = True
        if not event_ok:
            continue
        a_is_1 = _same_fighter(
            pick.get("fighter_a_id"), pick.get("fighter_a_url"), pick.get("fighter_a_name"),
            fight.get("fighter1_id"), fight.get("f1_url"), fight.get("f1_name"),
        )
        b_is_2 = _same_fighter(
            pick.get("fighter_b_id"), pick.get("fighter_b_url"), pick.get("fighter_b_name"),
            fight.get("fighter2_id"), fight.get("f2_url"), fight.get("f2_name"),
        )
        a_is_2 = _same_fighter(
            pick.get("fighter_a_id"), pick.get("fighter_a_url"), pick.get("fighter_a_name"),
            fight.get("fighter2_id"), fight.get("f2_url"), fight.get("f2_name"),
        )
        b_is_1 = _same_fighter(
            pick.get("fighter_b_id"), pick.get("fighter_b_url"), pick.get("fighter_b_name"),
            fight.get("fighter1_id"), fight.get("f1_url"), fight.get("f1_name"),
        )
        if (a_is_1 and b_is_2) or (a_is_2 and b_is_1):
            return fight
    return None


def score_pending_picks(connection):
    create_card_pick_tables(connection)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT card_pick_id, event_url, event_date, event_headline,
                   fighter_a_name, fighter_b_name, fighter_a_url, fighter_b_url,
                   fighter_a_id, fighter_b_id, pick, is_tossup, is_high_conf_66
            FROM card_picks
            WHERE scored_at IS NULL
            """
        )
        columns = [col[0] for col in cursor.description]
        pending = [dict(zip(columns, row)) for row in cursor.fetchall()]
    if not pending:
        return 0
    fights = _load_completed_fights(
        connection,
        [row.get("event_url") for row in pending],
        [_as_date(row.get("event_date")) for row in pending],
    )
    scored = 0
    with connection.cursor() as cursor:
        for pick in pending:
            fight = _match_fight(pick, fights)
            if fight is None:
                continue
            outcome = fight.get("outcome_type")
            winner_side = _winner_side(pick, fight) if outcome == "decisive" else None
            pick_correct = None
            if outcome == "decisive" and winner_side and pick.get("pick") in ("A", "B"):
                pick_correct = pick["pick"] == winner_side
            cursor.execute(
                """
                UPDATE card_picks
                SET fight_id = %s,
                    outcome_type = %s,
                    winner_side = %s,
                    pick_correct = %s,
                    scored_at = now()
                WHERE card_pick_id = %s
                  AND scored_at IS NULL
                """,
                (
                    fight.get("fight_id"),
                    outcome,
                    winner_side,
                    pick_correct,
                    pick["card_pick_id"],
                ),
            )
            scored += 1
    connection.commit()
    return scored


def ledger_summary(connection):
    create_card_pick_tables(connection)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                COUNT(*) AS n_logged,
                COUNT(*) FILTER (WHERE scored_at IS NULL) AS n_pending,
                COUNT(*) FILTER (WHERE is_tossup) AS n_tossup,
                COUNT(*) FILTER (
                    WHERE scored_at IS NOT NULL AND is_tossup
                ) AS n_tossup_scored,
                COUNT(*) FILTER (
                    WHERE pick_correct IS NOT NULL
                ) AS n_picks_scored,
                COUNT(*) FILTER (WHERE pick_correct IS TRUE) AS n_correct,
                COUNT(*) FILTER (
                    WHERE is_high_conf_66 AND pick_correct IS NOT NULL
                ) AS n_high_scored,
                COUNT(*) FILTER (
                    WHERE is_high_conf_66 AND pick_correct IS TRUE
                ) AS n_high_correct,
                COUNT(*) FILTER (
                    WHERE scored_at IS NOT NULL
                      AND outcome_type IN ('draw', 'no_contest')
                ) AS n_void
            FROM card_picks
            """
        )
        row = cursor.fetchone()
        keys = [
            "n_logged",
            "n_pending",
            "n_tossup",
            "n_tossup_scored",
            "n_picks_scored",
            "n_correct",
            "n_high_scored",
            "n_high_correct",
            "n_void",
        ]
        return dict(zip(keys, row))


def _pct(correct, total):
    if not total:
        return "n/a"
    return f"{100.0 * correct / total:.1f}%"


def print_ledger(summary, newly_scored=0):
    print("=" * 72)
    print("Pick ledger  (stored in card_picks)")
    print("P>50% picks only.  50.0/50.0 = tossup (not counted).  66%+ is its own bucket.")
    if newly_scored:
        print(f"Newly scored this run: {newly_scored}")
    n_pick = summary["n_picks_scored"] or 0
    n_ok = summary["n_correct"] or 0
    n_high = summary["n_high_scored"] or 0
    n_high_ok = summary["n_high_correct"] or 0
    print(
        f"  Decisive picks (P>50%):  {n_ok}/{n_pick}  right  ({_pct(n_ok, n_pick)})"
    )
    print(
        f"  66%+ favorites:          {n_high_ok}/{n_high}  right  ({_pct(n_high_ok, n_high)})"
    )
    print(
        f"  Tossups logged:          {summary['n_tossup']}  "
        f"(scored {summary['n_tossup_scored']}, never counted as hits)"
    )
    print(f"  Draws/NC (void):         {summary['n_void']}")
    print(f"  Pending (not in DB yet): {summary['n_pending']}")
    print(f"  All logged bouts:        {summary['n_logged']}")
    print("=" * 72)


def update_ledger(connection, event, rows):
    newly_scored = score_pending_picks(connection)
    logged = upsert_card_picks(connection, event, rows)
    newly_scored += score_pending_picks(connection)
    summary = ledger_summary(connection)
    return logged, newly_scored, summary
