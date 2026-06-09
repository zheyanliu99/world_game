# BigQuery General Seed

The city-level agentic demo stores reusable general metadata in local seed files:

- `data/generals/general_seed.json`
- `data/generals/general_seed.csv`
- `data/generals/general_seed_bigquery_schema.json`

The app reads the local JSON seed at runtime. BigQuery is optional and is only for reuse outside the demo.

Validate the local files:

```bash
.venv/bin/python scripts/load_general_seed_bigquery.py --validate-only
```

Load into an existing Google Cloud project and dataset:

```bash
export GCP_PROJECT_ID="your-project"
export BQ_DATASET="historical_war_sim"
.venv/bin/python scripts/load_general_seed_bigquery.py
```

The script can create the dataset/table if the `bq` CLI is installed and credentials are configured. It does not create a Google Cloud project.
