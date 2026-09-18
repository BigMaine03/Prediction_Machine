"""Rebuild Layer 2 facts, then Layer 3 pre-fight features, then the model view.

Usage:
    python Features/run_feature_pipeline.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Database.create_fact_tables import create_fact_tables
from Database.create_feature_tables import create_feature_tables
from Database.db import get_connection
from Features.build_fight_facts import run as build_facts
from Features.build_fighter_features import run as build_features


def run():
    connection = get_connection()
    try:
        print("===============================")
        print("Feature pipeline")
        print("===============================")
        create_fact_tables(connection)
        create_feature_tables(connection)

        print()
        print("--- Layer 2: facts ---")
        fact_counts = build_facts(connection)

        print()
        print("--- Layer 3: pre-fight features ---")
        feature_rows = build_features(connection)

        print()
        print("===============================")
        print("Feature pipeline complete")
        print("===============================")
        print(f"fight_facts: {fact_counts['fight_facts']}")
        print(f"fighter_fight_facts: {fact_counts['fighter_fight_facts']}")
        print(f"fighter_round_facts: {fact_counts['fighter_round_facts']}")
        print(f"fighter_fight_features: {feature_rows}")
        print("Model export view: fight_model_rows (page order, debug)")
        print("Training view:     fight_model_train (page + swapped corners)")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(run())
