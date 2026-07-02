# Sharia Scope

Sharia Scope screens **any company** for Shariah compliance from its financial
statements. Give it a company's annual/quarterly numbers — typed in, or read
automatically from an uploaded report by Claude — and it computes the five
PSX/KMI screening ratios, returns a compliant / non-compliant verdict with the
reasons, runs a dividend-purification calculation, and produces a branded PDF
tear-sheet. It is a **calculator, not a lookup table**: the verdict is computed
purely from the figures you supply — no pre-approved company list.

The app is a **FastAPI backend** (`server.py`) serving a single-page app
(`static/index.html`). All screening runs instantly in the browser; the server
handles AI extraction, PDF generation, and optional cloud saves.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python server.py            # serves on $PORT (default 8501); binds 0.0.0.0
```

Then open http://localhost:8501.

## Screens

- **Analyze** — upload a financial statement (PDF / image / Excel) and Claude
  extracts the figures, or enter the line items manually. Then screen for the
  verdict, ratio cards, failure reasons, dividend-purification calculator, the
  source document + report viewer, and a downloadable PDF tear-sheet.
- **Methodology** — thresholds, formulas, the business screen, purification, and
  the disclaimer.
- **FAQ** — Shariah-screening basics, ratios, purification, and how the tool works.
- **Saved Runs** — reopen any archived run (when cloud saves are enabled).

## AI extraction

The screening engine is fully offline and deterministic. AI extraction is a
convenience layer with three ways to supply a key:

- **Anthropic (default)** — set `ANTHROPIC_API_KEY` on the server, or, if no
  server key is set, enter your own key in the app (kept in the browser session
  only, sent per-request, never stored server-side).
- **AWS Bedrock** — explicitly set `AI_PROVIDER=bedrock` plus
  `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` (optionally
  `BEDROCK_MODEL`). AWS credentials alone do not change the default provider.

Without any key, everything still works via manual entry.

## Saving runs (optional)

Each run can be archived to Firebase — **inputs, computed outputs, the source
statement, and the generated PDF**. Without it the app is fully functional. To
enable: create a Firestore database (Native mode) + Cloud Storage in your
Firebase project, generate a service-account key, and provide it via
`FIREBASE_SA_JSON` / `FIREBASE_SERVICE_ACCOUNT` (JSON string), a git-ignored
`firebase-service-account.json`, or `GOOGLE_APPLICATION_CREDENTIALS`. A **Save**
button then appears after each screening, and **Saved Runs** lets you reopen any
run (source + report + inputs).

## Deploying (Replit / cloud)

`.replit` runs `uvicorn server:app --host 0.0.0.0 --port 5000`. Credentials come
from environment **Secrets** — nothing secret lives in the repo:

| Secret | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | Anthropic extraction |
| `AI_PROVIDER=bedrock` + `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` | AWS Bedrock extraction |
| `FIREBASE_SA_JSON` | service-account JSON content, for Save/History |
| `PUBLIC_BASE_URL` | canonical/OG/sitemap base URL (defaults to the Replit domain) |
| `APP_PASSWORD` | when set, gates the app behind a password |

**Always set `APP_PASSWORD` on a public deploy** — the app has no per-user login
and bills your Anthropic/AWS account, so the password is what stops strangers
from running up cost or reaching your data.

## Screening rules (PSX / KMI)

Computed from raw financials:

| Screen | Formula | Pass |
|---|---|---|
| Debt ratio | interest-bearing debt ÷ total assets | `< 37%` |
| Non-compliant investments | non-Shariah investments ÷ total assets | `< 33%` |
| Non-compliant income | non-Shariah income ÷ total revenue | `< 5%` |
| Illiquid assets | illiquid assets ÷ total assets | `≥ 25%` |
| Net liquid assets / share (advisory) | (assets − illiquid − liabilities) ÷ shares | `< market price` |

The five core tests determine the verdict; the net-liquid-assets check is an
advisory KMI indicator only. A business that is non-compliant by nature
(conventional banking, insurance, alcohol, tobacco, gambling, etc.) is screened
out by sector before any ratio is computed.

## Project layout

```
server.py            FastAPI backend (extract / PDF / runs / SEO) + serves the SPA
static/index.html    Single-page app (Analyze / Methodology / FAQ / Saved Runs)
static/              favicon, web manifest, OG image
allshariah_core.py   Ratio computation + screening engine
ai_extract.py        Claude / Bedrock extraction of line items from statements
report.py            PDF tear-sheet + purification-summary generator (reportlab)
market_data.py       Live PSX price lookup (yfinance)
storage.py           Firebase Firestore + Cloud Storage (optional)
tests/test_core.py   Unit tests for the engine
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Disclaimer

Educational tool. Verdicts are computed from the figures you enter and are not
investment advice, a religious ruling, or an official PSX/KMI screening service.
Always verify figures against audited statements and consult a qualified Shariah
advisor.
