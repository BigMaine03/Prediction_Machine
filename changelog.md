extract_round_stats — wrong table selected/misattributed rows across two similarly-shaped tables, causing phantom extra rounds with scrambled column data.
extract_fighters — page-wide selector grabbed duplicate cells instead of one, causing fighter2 to always resolve to fighter1's identity across 100% of fights.
extract_totals — brittle inline-style selector matched nothing on any current page, silently returning empty significant-strike totals for every fight.
insert_round_stats — no delete step before insert, so rows from any of the above bugs (present in past scrapes) could persist indefinitely even after the extraction bug itself was fixed.
