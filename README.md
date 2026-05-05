# Tableau + Hex Demo

Turn your Tableau data source into an AI-ready data model for Hex.

> **Your Tableau data, now with AI.**

---

## What this does

```
Tableau Cloud                     This repo                    Hex
─────────────────    ──────────────────────────────    ──────────────────
Published data   →   semantic_model/model.yml      →   AI chat context
source metadata  →   guide/data_source_guide.md    →   AI chat context
```

A single script pulls all field metadata (including calculated field formulas) from
a Tableau published data source, then uses Claude to generate:

- **`semantic_model/model.yml`** — structured YAML with every metric and dimension defined
- **`guide/data_source_guide.md`** — plain-English guide to the data: what it means, how to use it, common patterns

Both files live in this repo. Hex connects to the repo as an external asset, giving
Hex AI structured context to answer questions about your Tableau data.

---

## Demo Script

### Step 1 — Show the Tableau dashboard

Open the dashboard in Tableau Cloud. Walk through:
- What business question it answers
- Key metrics on the dashboard
- The published data source powering it

### Step 2 — Show the published data source

In Tableau Cloud → Data Sources tab:
- Open the data source
- Show the field list, especially any calculated fields
- Point out that all this metadata lives in Tableau's Metadata API

### Step 3 — Run the metadata extraction script

```bash
# One-time setup
pip install -r requirements.txt
cp .env.example .env
# Fill in .env with your Tableau PAT and data source name

# Pull metadata + generate docs
python scripts/pull_tableau_metadata.py
```

The script:
1. Authenticates to Tableau Cloud via Personal Access Token
2. Queries the Tableau Metadata API (GraphQL) for all fields, types, formulas
3. Sends the metadata to Claude, which writes the guide and semantic model
4. Saves both files into this repo

Show the terminal output, then open the generated files to show what was created.

### Step 4 — Push to GitHub

```bash
git add semantic_model/ guide/
git commit -m "chore: regenerate Tableau metadata docs from $(date +%Y-%m-%d)"
git push
```

### Step 5 — Connect Hex to this repo

In Hex:
1. Go to **Settings → Knowledge → External Files**
2. Connect this GitHub repository
3. Select `semantic_model/model.yml` and `guide/data_source_guide.md` as context files
4. Save

### Step 6 — Chat with your Tableau data in Hex

Open a Hex project connected to the same underlying database. Open Hex AI chat.
The AI now has full context of your Tableau data model.

Example questions to demo:
- _"What metrics are available in this data source?"_
- _"How is [calculated field] computed? When should I use it?"_
- _"Write a SQL query to get total sales by region for Q1"_
- _"What's the difference between [Metric A] and [Metric B]?"_

---

## Setup

### Prerequisites

- Python 3.10+
- Tableau Cloud account with a published data source
- Tableau Personal Access Token (Settings → My Account Settings → Personal Access Tokens)
- Anthropic API key

### Environment variables

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `TABLEAU_SERVER_URL` | e.g. `https://prod-useast-a.online.tableau.com` |
| `TABLEAU_SITE_NAME` | Your site's content URL (the part after `/site/`) |
| `TABLEAU_PAT_NAME` | Personal Access Token name |
| `TABLEAU_PAT_SECRET` | Personal Access Token secret |
| `TABLEAU_DATASOURCE_NAME` | Exact name of the published data source |
| `ANTHROPIC_API_KEY` | Anthropic API key |

### Finding your Tableau Server URL and Site Name

- **Server URL:** The base URL of your Tableau Cloud instance (before `/site/`)
- **Site Name:** In the URL `https://prod-useast-a.online.tableau.com/site/mysite/...`, the site name is `mysite`
- For the **default site**, use an empty string: `TABLEAU_SITE_NAME=`

---

## Repository structure

```
tableauhexdemo/
├── scripts/
│   └── pull_tableau_metadata.py   # Main extraction + generation script
├── semantic_model/
│   └── model.yml                  # Generated — YAML metric/dimension definitions
├── guide/
│   └── data_source_guide.md       # Generated — plain-English usage guide
├── requirements.txt
├── .env.example
└── README.md
```

---

## Re-running

Run the script any time the Tableau data source changes. The generated files will be
overwritten with fresh content. Commit and push to update the Hex context.
