#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hwsim.agentic.city_data import load_city_graph, load_general_seeds  # noqa: E402


SCHEMA_FILE = ROOT / "data/generals/general_seed_bigquery_schema.json"
CSV_FILE = ROOT / "data/generals/general_seed.csv"


def validate_seed_files() -> None:
    city_graph = load_city_graph()
    seeds = load_general_seeds(city_ids=set(city_graph.city_map()))
    with SCHEMA_FILE.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    schema_names = [field["name"] for field in schema]
    with CSV_FILE.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != schema_names:
            raise ValueError("CSV header must match BigQuery schema order")
        rows = list(reader)
    if len(rows) != len(seeds):
        raise ValueError("CSV and JSON seed counts differ")
    print(f"Validated {len(seeds)} generals and {len(city_graph.cities)} cities.")


def load_bigquery(table_name: str) -> None:
    project_id = os.environ.get("GCP_PROJECT_ID")
    dataset = os.environ.get("BQ_DATASET")
    if not project_id or not dataset:
        raise SystemExit("Set GCP_PROJECT_ID and BQ_DATASET, or use --validate-only.")
    table_ref = f"{project_id}:{dataset}.{table_name}"
    dataset_ref = f"{project_id}:{dataset}"
    dataset_check = subprocess.run(
        ["bq", "--project_id", project_id, "show", "--format=none", dataset_ref],
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if dataset_check.returncode != 0:
        subprocess.run(["bq", "--project_id", project_id, "mk", "--dataset", dataset_ref], check=True)
    subprocess.run(
        [
            "bq",
            "--project_id",
            project_id,
            "load",
            "--replace",
            "--source_format=CSV",
            "--skip_leading_rows=1",
            table_ref,
            str(CSV_FILE),
            str(SCHEMA_FILE),
        ],
        check=True,
    )
    print(f"Loaded {table_ref}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate or load Three Kingdoms general seed data into BigQuery.")
    parser.add_argument("--validate-only", action="store_true", help="Validate local seed/schema files without calling bq.")
    parser.add_argument("--table-name", default="general_seed", help="BigQuery table name. Default: general_seed.")
    args = parser.parse_args()

    validate_seed_files()
    if not args.validate_only:
        load_bigquery(args.table_name)


if __name__ == "__main__":
    main()
