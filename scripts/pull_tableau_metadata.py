#!/usr/bin/env python3
"""
Pull metadata from a Tableau Cloud published data source and generate:
  - guide/data_source_guide.md   (Tableau metadata formatted as Hex AI context)
  - semantic_model/model.yml     (YAML metric/dimension definitions, auto-generated)

No LLM calls required — the raw Tableau metadata is embedded directly into the
markdown guide so the Hex AI agent can use it to understand the data model and
construct Snowflake queries.

Usage:
    python scripts/pull_tableau_metadata.py

Required env vars (copy .env.example → .env and fill in):
    TABLEAU_SERVER_URL, TABLEAU_SITE_NAME, TABLEAU_PAT_NAME,
    TABLEAU_PAT_SECRET, TABLEAU_DATASOURCE_NAME
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

TABLEAU_SERVER  = os.environ["TABLEAU_SERVER_URL"].rstrip("/")
TABLEAU_SITE    = os.environ["TABLEAU_SITE_NAME"]
PAT_NAME        = os.environ["TABLEAU_PAT_NAME"]
PAT_SECRET      = os.environ["TABLEAU_PAT_SECRET"]
DATASOURCE_NAME = os.environ["TABLEAU_DATASOURCE_NAME"]

REPO_ROOT           = Path(__file__).parent.parent
SEMANTIC_MODEL_PATH = REPO_ROOT / "semantic_model" / "model.yml"
GUIDE_PATH          = REPO_ROOT / "guide" / "data_source_guide.md"

# ── Tableau Auth ──────────────────────────────────────────────────────────────

JSON_HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
}


def tableau_sign_in() -> tuple[str, str]:
    """Sign in with a Personal Access Token. Returns (token, site_id)."""
    url = f"{TABLEAU_SERVER}/api/3.21/auth/signin"
    payload = {
        "credentials": {
            "personalAccessTokenName": PAT_NAME,
            "personalAccessTokenSecret": PAT_SECRET,
            "site": {"contentUrl": TABLEAU_SITE},
        }
    }
    resp = requests.post(url, json=payload, headers=JSON_HEADERS)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Auth failed — HTTP {resp.status_code}\n"
            f"  Response: {resp.text[:500]}"
        )
    data = resp.json()
    token   = data["credentials"]["token"]
    site_id = data["credentials"]["site"]["id"]
    print(f"  Signed in — site_id: {site_id}")
    return token, site_id


def tableau_sign_out(token: str) -> None:
    requests.post(
        f"{TABLEAU_SERVER}/api/3.21/auth/signout",
        headers={"x-tableau-auth": token},
    )

# ── Metadata API (GraphQL) ────────────────────────────────────────────────────

METADATA_QUERY = """
query GetDatasourceMetadata($datasourceName: String!) {
  publishedDatasourcesConnection(filter: {name: $datasourceName}) {
    nodes {
      id
      name
      description
      projectName
      updatedAt
      upstreamDatabasesConnection {
        nodes {
          name
          connectionType
        }
      }
      upstreamTablesConnection {
        nodes {
          name
          schema
          database { name }
        }
      }
      fieldsConnection {
        nodes {
          id
          name
          description
          isHidden
          ... on CalculatedField {
            dataType
            role
            formula
            aggregation
          }
          ... on ColumnField {
            dataType
            role
            aggregation
            defaultFormat
          }
          ... on GroupField {
            dataType
            role
          }
        }
      }
    }
  }
}
"""


def fetch_datasource_metadata(token: str) -> dict:
    url = f"{TABLEAU_SERVER}/api/metadata/graphql"
    headers = {
        **JSON_HEADERS,
        "x-tableau-auth": token,
    }
    payload = {
        "query": METADATA_QUERY,
        "variables": {"datasourceName": DATASOURCE_NAME},
    }
    resp = requests.post(url, headers=headers, json=payload)
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        raise RuntimeError(f"Metadata API errors: {data['errors']}")

    nodes = data["data"]["publishedDatasourcesConnection"]["nodes"]
    if not nodes:
        raise ValueError(
            f"No published data source named '{DATASOURCE_NAME}' found.\n"
            "Check TABLEAU_DATASOURCE_NAME in your .env file."
        )

    ds = nodes[0]
    field_count = len(ds["fieldsConnection"]["nodes"])
    print(f"  Found: '{ds['name']}' ({field_count} fields)")
    return ds

# ── Field Classification ──────────────────────────────────────────────────────

def snake_case(name: str) -> str:
    s = re.sub(r"[^\w\s]", "", name)
    s = re.sub(r"[\s]+", "_", s.strip())
    return s.lower()


def classify_fields(fields: list[dict]) -> dict:
    dimensions   = []
    measures     = []
    calculations = []

    for f in fields:
        if f.get("isHidden"):
            continue

        role      = (f.get("role") or "").lower()
        data_type = (f.get("dataType") or "unknown").lower()
        formula   = f.get("formula")

        base = {
            "name":         snake_case(f["name"]),
            "label":        f["name"],
            "description":  f.get("description") or "",
            "type":         data_type,
            "tableau_field": f["name"],
        }

        if formula:
            calculations.append({
                **base,
                "formula":     formula,
                "aggregation": f.get("aggregation") or "",
                "role":        role,
            })
        elif role == "measure":
            measures.append({
                **base,
                "aggregation": f.get("aggregation") or "SUM",
                "format":      f.get("defaultFormat") or "",
            })
        else:
            dimensions.append({**base, "role": "dimension"})

    return {"dimensions": dimensions, "measures": measures, "calculated_fields": calculations}

# ── YAML Semantic Model ───────────────────────────────────────────────────────

def build_semantic_model(ds: dict, classified: dict, generated_at: str) -> dict:
    upstream_tables = [
        {
            "name":     t["name"],
            "schema":   t.get("schema") or "",
            "database": (t.get("database") or {}).get("name") or "",
        }
        for t in ds.get("upstreamTablesConnection", {}).get("nodes", [])
    ]
    upstream_databases = [
        {
            "name":            db["name"],
            "connection_type": db.get("connectionType") or "",
        }
        for db in ds.get("upstreamDatabasesConnection", {}).get("nodes", [])
    ]

    return {
        "version": 1,
        "data_source": {
            "name":               ds["name"],
            "description":        ds.get("description") or "",
            "tableau_project":    ds.get("projectName") or "",
            "tableau_server":     TABLEAU_SERVER,
            "tableau_site":       TABLEAU_SITE,
            "upstream_databases": upstream_databases,
            "upstream_tables":    upstream_tables,
            "last_extracted":     generated_at,
        },
        "dimensions":       classified["dimensions"],
        "measures":         classified["measures"],
        "calculated_fields": classified["calculated_fields"],
    }

# ── Markdown Guide ────────────────────────────────────────────────────────────

def build_guide(ds: dict, classified: dict, raw_metadata: dict, generated_at: str) -> str:
    upstream_tables = ds.get("upstreamTablesConnection", {}).get("nodes", [])
    upstream_dbs    = ds.get("upstreamDatabasesConnection", {}).get("nodes", [])

    table_rows = "\n".join(
        f"| `{t.get('database', {}).get('name', '')}` | `{t.get('schema', '')}` | `{t['name']}` |"
        for t in upstream_tables
    ) or "_No upstream tables found._"

    db_rows = "\n".join(
        f"| `{db['name']}` | `{db.get('connectionType', '')}` |"
        for db in upstream_dbs
    ) or "_No upstream databases found._"

    dim_rows = "\n".join(
        f"| `{d['tableau_field']}` | {d['type']} | {d['description'] or '—'} |"
        for d in classified["dimensions"]
    ) or "_No dimensions._"

    measure_rows = "\n".join(
        f"| `{m['tableau_field']}` | {m['type']} | {m.get('aggregation', '')} | {m['description'] or '—'} |"
        for m in classified["measures"]
    ) or "_No measures._"

    calc_rows = "\n".join(
        f"| `{c['tableau_field']}` | {c['type']} | `{c['formula']}` | {c['description'] or '—'} |"
        for c in classified["calculated_fields"]
    ) or "_No calculated fields._"

    raw_json = json.dumps(raw_metadata, indent=2, default=str)

    return f"""<!-- Auto-generated by pull_tableau_metadata.py | Generated: {generated_at} | DO NOT EDIT -->

# {ds['name']} — Tableau Data Source Context for Hex AI

> **Instructions for Hex AI Agent**
>
> This file contains metadata extracted directly from a Tableau Cloud published data source.
> Use this context to:
> 1. Identify the correct Snowflake database, schema, and table names for SQL queries
> 2. Understand what each field represents and how to use it correctly
> 3. Translate Tableau calculated field formulas into equivalent Snowflake SQL
> 4. Apply the correct aggregations when writing metric queries
>
> When a user asks a question about this data, construct Snowflake SQL using the upstream
> table names below. Tableau field names map directly to column names in those tables
> (unless overridden by a calculated field formula).

---

## Data Source Overview

| Property | Value |
|---|---|
| **Name** | {ds['name']} |
| **Tableau Project** | {ds.get('projectName') or '—'} |
| **Description** | {ds.get('description') or '—'} |
| **Last Updated in Tableau** | {ds.get('updatedAt') or '—'} |
| **Metadata Extracted** | {generated_at} |

---

## Upstream Databases

| Database | Connection Type |
|---|---|
{db_rows}

## Upstream Tables (use these for Snowflake queries)

| Database | Schema | Table |
|---|---|---|
{table_rows}

---

## Dimensions

These are categorical or date fields. Use them in `GROUP BY`, `WHERE`, and `SELECT` clauses.

| Tableau Field Name | Type | Description |
|---|---|---|
{dim_rows}

---

## Measures

These are numeric fields. Apply the listed aggregation when using them in queries.

| Tableau Field Name | Type | Default Aggregation | Description |
|---|---|---|---|
{measure_rows}

---

## Calculated Fields

These fields are defined by Tableau formulas. Translate the formula to Snowflake SQL when constructing queries. The formula syntax uses Tableau's expression language — `[Field Name]` references map to column names in the upstream tables.

| Tableau Field Name | Type | Formula | Description |
|---|---|---|---|
{calc_rows}

---

## Raw Metadata (JSON)

The complete metadata payload from the Tableau Metadata API is included below for reference.
The Hex AI agent may use this for any field details not captured in the tables above.

```json
{raw_json}
```
"""

# ── Write Outputs ─────────────────────────────────────────────────────────────

def write_outputs(guide_md: str, model: dict) -> None:
    SEMANTIC_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    GUIDE_PATH.parent.mkdir(parents=True, exist_ok=True)

    header = (
        "# Auto-generated by pull_tableau_metadata.py\n"
        f"# Data source: {model['data_source']['name']}\n"
        f"# Generated: {model['data_source']['last_extracted']}\n"
        "# DO NOT EDIT — re-run the script to regenerate\n\n"
    )
    SEMANTIC_MODEL_PATH.write_text(
        header + yaml.dump(model, allow_unicode=True, sort_keys=False, default_flow_style=False)
    )
    print(f"  Wrote: {SEMANTIC_MODEL_PATH.relative_to(REPO_ROOT)}")

    GUIDE_PATH.write_text(guide_md)
    print(f"  Wrote: {GUIDE_PATH.relative_to(REPO_ROOT)}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\n{'─'*60}")
    print(f"  Tableau → Hex Metadata Pipeline")
    print(f"  Data source: {DATASOURCE_NAME}")
    print(f"{'─'*60}\n")

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print("[1/3] Signing in to Tableau Cloud...")
    token, _site_id = tableau_sign_in()

    try:
        print(f"\n[2/3] Fetching metadata from Tableau Metadata API...")
        ds         = fetch_datasource_metadata(token)
        classified = classify_fields(ds["fieldsConnection"]["nodes"])

        counts = {k: len(v) for k, v in classified.items()}
        print(f"       {counts}")

        print(f"\n[3/3] Generating output files...")
        raw_metadata = {
            "name":               ds["name"],
            "description":        ds.get("description"),
            "projectName":        ds.get("projectName"),
            "updatedAt":          ds.get("updatedAt"),
            "upstreamDatabases":  ds.get("upstreamDatabasesConnection", {}).get("nodes", []),
            "upstreamTables":     ds.get("upstreamTablesConnection", {}).get("nodes", []),
            "fields":             [
                f for f in ds["fieldsConnection"]["nodes"] if not f.get("isHidden")
            ],
        }

        model    = build_semantic_model(ds, classified, generated_at)
        guide_md = build_guide(ds, classified, raw_metadata, generated_at)
        write_outputs(guide_md, model)

    finally:
        tableau_sign_out(token)
        print("\n  Signed out of Tableau.")

    print(f"\n{'─'*60}")
    print(f"  Done! Next steps:")
    print(f"    git add semantic_model/ guide/")
    print(f"    git commit -m 'chore: regenerate Tableau metadata docs'")
    print(f"    git push")
    print(f"{'─'*60}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyError as e:
        print(f"\nERROR: Missing required environment variable: {e}")
        print("Copy .env.example to .env and fill in your credentials.\n")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}\n")
        raise
