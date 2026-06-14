# Sharia Scope

Sharia Scope analyzes **any company's** Shariah compliance from its financial
statements. Give it a company's annual/quarterly numbers — typed in, or read
automatically from an uploaded report by Claude — and it computes the six
PSX/KMI screening ratios, returns a compliant / non-compliant verdict with the
reasons, runs a dividend-purification calculation, and produces a PDF tear-sheet.

It is **not** a lookup over a fixed list. The bundled Meezan index sheet is used
only to *validate* the analyzer (backtest its verdicts against Meezan's official
rulings) — it is never the source of truth for a live screen.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## The three tabs

- **🔍 Analyze a company** — enter the raw line items manually, or upload a
  financial statement (PDF/image) and click *Extract with Claude* to pre-fill
  them. Then *Run Shariah screening* for the verdict, ratio dashboard, failure
  reasons, dividend-purification calculator, and a downloadable PDF tear-sheet.
- **✅ Validation** — runs the analyzer across all ~500 companies in Meezan's
  published KMI All-Share Islamic Index and reports how often its computed
  verdict matches the official ruling (currently ~97% agreement; the remaining
  disagreements are companies Meezan granted a documented manual exception).
- **📖 Methodology** — the thresholds, formulas, business screen, purification
  formula, sources, and disclaimer.

## AI extraction (optional)

The formula engine is fully offline and deterministic. AI extraction is a
convenience: add a Claude API key in the sidebar (or set `ANTHROPIC_API_KEY`),
upload a statement, and Claude pulls the line items for you to verify. Without a
key, everything still works via manual entry.

## Saving runs (optional)

Every run can be archived in full to Firebase — **inputs, computed outputs, the
source statement (if uploaded), and the generated PDF tear-sheet**. It's entirely
optional; without it the app is fully offline. To turn it on:

1. In the Firebase console (your project), create a **Firestore database** (Native mode)
   and enable **Cloud Storage** (this creates the storage bucket).
2. Project settings → Service accounts → **Generate new private key** (downloads a JSON).
3. Make the key available one of three ways: drop the file in the project root as
   `firebase-service-account.json` (git-ignored); set `GOOGLE_APPLICATION_CREDENTIALS`
   to its path; or, on a host where secrets are strings, put the whole JSON in the
   `FIREBASE_SERVICE_ACCOUNT` env var. You can also just upload it in the app sidebar.

Then a **Save** button appears after each screening (metadata → Firestore, files →
Cloud Storage), and the **Saved** tab lets you reopen any run: download its source
statement and tear-sheet, or *Load* its inputs back into the Analyze form. If uploads
fail, set the exact bucket name (Console → Storage) in the sidebar.

## Deploying (Replit / cloud)

The app reads all credentials from environment variables, so on a host like Replit
you set them as **Secrets** — no secret ever lives in the repo:

| Secret | Purpose |
| --- | --- |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` | AWS Bedrock extraction |
| `ANTHROPIC_API_KEY` | Anthropic API extraction (alternative to Bedrock) |
| `FIREBASE_SERVICE_ACCOUNT` | the service-account JSON content, for Save/History |
| `APP_PASSWORD` | when set, the app requires this password before anything loads |

Two host notes: bind the server to `0.0.0.0` (e.g. `--server.address 0.0.0.0`), since
localhost can't be proxied; and **always set `APP_PASSWORD` on a public deploy** — the
app has no per-user login and bills your AWS/Anthropic account, so the password is what
stops strangers from running up the cost or reaching your data.

## Screening rules (PSX / KMI)

Computed from raw financials:

| Screen | Formula | Pass |
|---|---|---|
| Debt ratio | interest-bearing debt ÷ total assets | `< 37%` |
| Non-compliant investments | non-Shariah investments ÷ total assets | `< 33%` |
| Non-compliant income | non-Shariah income ÷ total revenue | `< 5%` |
| Illiquid assets | illiquid assets ÷ total assets | `≥ 25%` |
| Net liquid assets / share | (assets − illiquid − liabilities) ÷ shares | `< market price` |

A business that is non-compliant by nature (conventional banking, insurance,
alcohol, tobacco, gambling, etc.) is screened out by sector before any ratio is
computed.

## Project layout

```
app.py                 Streamlit UI (Analyze / Validation / Methodology)
allshariah_core.py     Ratio computation + screening engine + backtest
ai_extract.py          Claude-API extraction of line items from statements
report.py              PDF tear-sheet generator (reportlab)
data/
  kmi_all_share_index_dec2025.csv   Meezan index sheet — validation oracle only
scripts/parse_index_pdf.py          One-time PDF -> validation-CSV parser
tests/test_core.py     Unit tests for the engine
```

To regenerate the validation sheet from an official index PDF:

```bash
pdftotext -layout All-Share-Islamic-Index.pdf /tmp/asii.txt
python scripts/parse_index_pdf.py /tmp/asii.txt data/kmi_all_share_index_dec2025.csv \
    --source "All-Share-Islamic-Index.pdf" --period "Period ended December 2025"
```

## Tests

```bash
pytest
```

## Disclaimer

Educational prototype. Verdicts are computed from the figures you enter and are
not investment advice, a religious ruling, or an official PSX/KMI screening
service. Always verify figures against audited statements and consult a
qualified Shariah advisor.
