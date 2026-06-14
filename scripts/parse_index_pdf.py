"""Parse a PSX/KMI All-Share Islamic Index report PDF into the validation CSV.

This is a one-time data-prep utility. It turns the official Meezan/PSX index
report (the "COMPLETE RATIOS" table) into a CSV that Sharia Scope uses as a
ground-truth set to backtest the analyzer against Meezan's published rulings.

The app itself does NOT depend on this CSV to screen a company — any company is
screened from its own raw financials. This sheet is only the test oracle.

Usage:
    pdftotext -layout All-Share-Islamic-Index.pdf /tmp/asii.txt
    python scripts/parse_index_pdf.py /tmp/asii.txt data/kmi_all_share_index_dec2025.csv \\
        --source "All-Share-Islamic-Index.pdf" --period "Period ended December 2025"
"""

from __future__ import annotations

import argparse
import csv
import re
import sys

# Canonical output columns (must match allshariah_core.CANONICAL_COLUMNS).
COLUMNS = [
    "ticker",
    "company_name",
    "objective_status",
    "debt_ratio",
    "investment_ratio",
    "income_ratio",
    "illiquid_assets_ratio",
    "net_liquid_assets_ratio",
    "share_price",
    "final_shariah_status",
    "source_document",
    "source_period",
    "notes",
]

# A data row begins with "<rank> <TICKER>" then 9 whitespace-separated fields.
LEADING = re.compile(r"^(\d+)\s+([A-Z0-9&.\-]+)$")

FOOTNOTES = {
    "1": "Based on September 2025 accounts.",
    "2": "As per last available annual / half-yearly accounts.",
    "3": "December 2025 accounts unavailable; September 2025 accounts used.",
    "4": "Screened on the latest available financial statements.",
}


def parse_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = re.split(r"\s{2,}", line.strip())
        # Layout: ["<rank> <TICKER>", company..., objective, 6 ratios, final_status].
        # The company name can itself contain a double-space, so locate the
        # objective column dynamically rather than assuming a fixed index.
        if len(fields) < 4:
            continue
        lead = LEADING.match(fields[0])
        if not lead:
            continue
        obj_idx = next(
            (i for i in range(2, len(fields)) if fields[i].strip() in {"Compliant", "Non-Compliant"}),
            None,
        )
        if obj_idx is None:
            continue
        ticker = lead.group(2)

        # 'No recent financials' appendix: objective + a note, but no ratio columns.
        if obj_idx + 7 >= len(fields):
            tail = " ".join(f.strip() for f in fields[obj_idx + 1:]).strip()
            if "no shariah opinion" in tail.lower() or "no recent financial" in tail.lower():
                company, notes = strip_markers(" ".join(f.strip() for f in fields[1:obj_idx]).strip())
                rows.append(
                    {
                        "ticker": ticker,
                        "company_name": company,
                        "objective_status": fields[obj_idx].strip(),
                        "debt_ratio": "", "investment_ratio": "", "income_ratio": "",
                        "illiquid_assets_ratio": "", "net_liquid_assets_ratio": "", "share_price": "",
                        "final_shariah_status": "Review Required",
                        "notes": " ".join(notes + [tail]),
                    }
                )
            continue  # otherwise a header/garbage line

        company_raw = " ".join(f.strip() for f in fields[1:obj_idx]).strip()
        ratio_fields = fields[obj_idx + 1 : obj_idx + 7]
        final_raw = fields[obj_idx + 7].strip()

        notes: list[str] = []
        # Trailing footnote digit on the final status, e.g. "Non-Compliant 1".
        fn = re.match(r"^(.*?)(?:\s+([1-4]))?$", final_raw)
        final_status = fn.group(1).strip()
        if fn.group(2):
            notes.append(FOOTNOTES[fn.group(2)])

        company, marker_notes = strip_markers(company_raw)
        notes.extend(marker_notes)

        rows.append(
            {
                "ticker": ticker,
                "company_name": company,
                "objective_status": fields[obj_idx].strip(),
                "debt_ratio": clean(ratio_fields[0]),
                "investment_ratio": clean(ratio_fields[1]),
                "income_ratio": clean(ratio_fields[2]),
                "illiquid_assets_ratio": clean(ratio_fields[3]),
                "net_liquid_assets_ratio": clean(ratio_fields[4]),
                "share_price": clean(ratio_fields[5]),
                "final_shariah_status": normalize_status(final_status),
                "notes": " ".join(notes),
            }
        )
    return rows


def strip_markers(company: str) -> tuple[str, list[str]]:
    """Strip the trailing marker blob (any mix of * ^ #) and map each symbol to
    its legend note. Markers seen in the report: *, ^, ^^^, #, ##, and combos."""
    notes: list[str] = []
    blob_match = re.search(r"\s*([*^#]+(?:\s+[*^#]+)*)\s*$", company)
    if blob_match:
        blob = blob_match.group(1)
        company = company[: blob_match.start()].strip()
        if "##" in blob:
            notes.append("Service-based sector with insignificant fixed assets — Compliant with relaxation over the illiquid-assets / net-liquid-assets screen.")
        elif "#" in blob:
            notes.append("Compliant by exception — non-compliant investment/income ratio exceeds the threshold due to circular debt.")
        if "^^^" in blob:
            notes.append("Compliant with exception over the income ratio due to circular debt in the oil & gas sector.")
        elif "^" in blob:
            notes.append("Compliant with relaxation over the illiquid-assets / net-liquid-assets screen.")
        if "*" in blob:
            notes.append("Provisional dividend purification rate (subject to final adjustment).")
    return company, notes


def clean(value: str) -> str:
    value = value.strip()
    return "" if value.upper() in {"N/A", "NA", "-"} else value


def normalize_status(value: str) -> str:
    low = value.lower()
    if "nc by nature" in low:
        return "NC by Nature"
    if "non-compliant" in low or "non compliant" in low:
        return "Non-Compliant"
    if "compliant" in low:
        return "Compliant"
    return value


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="pdftotext -layout output")
    ap.add_argument("output", help="destination CSV")
    ap.add_argument("--source", default="", help="source document name")
    ap.add_argument("--period", default="", help="review period label")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8", errors="ignore") as fh:
        rows = parse_rows(fh.read())

    if not rows:
        print("No data rows parsed — check the input format.", file=sys.stderr)
        return 1

    for row in rows:
        row["source_document"] = args.source
        row["source_period"] = args.period

    with open(args.output, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    compliant = sum(r["final_shariah_status"] == "Compliant" for r in rows)
    nc = sum(r["final_shariah_status"] == "Non-Compliant" for r in rows)
    nature = sum(r["final_shariah_status"] == "NC by Nature" for r in rows)
    print(f"Wrote {len(rows)} rows to {args.output}")
    print(f"  Compliant: {compliant}  Non-Compliant: {nc}  NC by Nature: {nature}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
