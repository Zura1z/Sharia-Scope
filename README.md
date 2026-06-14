# AllShariah V1

AllShariah V1 is a local Streamlit app for checking PSX Shariah screening status from a manually maintained CSV/XLSX file. It is intentionally simple: no backend, no database, no login, no cloud deployment, and no PDF ingestion.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The app opens in upload mode. Upload a CSV/XLSX file to start, or choose the bundled sample-data option from the sidebar when you want to test the workflow.

After selecting a company, use **Download compliance report** to generate a local HTML report with the status, screening ratios, failure reasons, source notes, and dividend purification calculation. Open the HTML file in a browser, or print/save it as PDF.

## Data File

The app can load CSV or XLSX files with these columns:

```text
ticker, company_name, objective_status, debt_ratio, investment_ratio,
income_ratio, illiquid_assets_ratio, net_liquid_assets_ratio, share_price,
final_shariah_status, source_document, source_period, notes
```

Percent values can be entered as `4.97` or `4.97%`. Missing values may be blank or `N/A`.

The bundled starter file is `data/allshariah_template.csv`.

## V1 Rules

- Debt ratio: `D/A < 37%`
- Non-compliant investments: `NCInv/TA < 33%`
- Non-compliant income: `NCInc/TR < 5%`
- Illiquid assets: `IA/TA >= 25%`
- Net liquid assets: `NLA < share price`

Rows marked `NC by Nature` skip ratio calculation and display as non-compliant by nature. Rows with no recent financials or no Shariah opinion display as review required.

## Tests

```bash
pytest
```

## Disclaimer

This is an educational prototype. It depends on manually prepared source data and is not investment advice, a religious ruling, or an official PSX/KMI screening service.
