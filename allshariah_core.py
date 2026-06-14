from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable

import pandas as pd


CANONICAL_COLUMNS = [
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

REQUIRED_COLUMNS = ["ticker", "company_name", "final_shariah_status"]
NUMERIC_COLUMNS = [
    "debt_ratio",
    "investment_ratio",
    "income_ratio",
    "illiquid_assets_ratio",
    "net_liquid_assets_ratio",
    "share_price",
]


@dataclass(frozen=True)
class ValidationMessage:
    level: str
    message: str


@dataclass(frozen=True)
class MetricResult:
    key: str
    label: str
    value: float | None
    threshold: str
    passed: bool | None
    meaning: str


@dataclass(frozen=True)
class CompanyEvaluation:
    status: str
    status_label: str
    metric_results: list[MetricResult]
    failure_reasons: list[str]
    notes: str


METRIC_RULES = [
    {
        "key": "debt_ratio",
        "label": "Debt Ratio",
        "threshold": "D/A < 37%",
        "meaning": "Interest-bearing debt should stay below the PSX/KMI debt threshold.",
        "limit": 37.0,
        "operator": "lt",
    },
    {
        "key": "investment_ratio",
        "label": "Non-Compliant Investment Ratio",
        "threshold": "NCInv/TA < 33%",
        "meaning": "Non-compliant investments should stay below the PSX/KMI threshold.",
        "limit": 33.0,
        "operator": "lt",
    },
    {
        "key": "income_ratio",
        "label": "Non-Compliant Income Ratio",
        "threshold": "NCInc/TR < 5%",
        "meaning": "Non-compliant income should remain below the tolerated income threshold.",
        "limit": 5.0,
        "operator": "lt",
    },
    {
        "key": "illiquid_assets_ratio",
        "label": "Illiquid Assets Ratio",
        "threshold": "IA/TA >= 25%",
        "meaning": "Illiquid assets should meet the PSX/KMI minimum level.",
        "limit": 25.0,
        "operator": "gte",
    },
]


def load_data(file: str | Path | BinaryIO) -> pd.DataFrame:
    """Load a CSV or XLSX data source and normalize its columns."""
    name = getattr(file, "name", str(file))
    suffix = Path(name).suffix.lower()

    if suffix == ".csv":
        df = pd.read_csv(file, dtype=str, keep_default_na=False)
    elif suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(file, dtype=str, keep_default_na=False)
    else:
        raise ValueError("Unsupported file type. Please use CSV or XLSX.")

    df.columns = [normalize_column_name(col) for col in df.columns]
    for column in CANONICAL_COLUMNS:
        if column not in df.columns:
            df[column] = ""
    return df[CANONICAL_COLUMNS].copy()


def normalize_column_name(value: object) -> str:
    text = str(value).strip().lower()
    replacements = {
        "%": "",
        "/": "_",
        "-": "_",
        " ": "_",
        "(": "",
        ")": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    while "__" in text:
        text = text.replace("__", "_")
    aliases = {
        "company": "company_name",
        "name": "company_name",
        "objective": "objective_status",
        "final_status": "final_shariah_status",
        "status": "final_shariah_status",
        "investment": "investment_ratio",
        "income": "income_ratio",
        "illiquid_assets": "illiquid_assets_ratio",
        "net_liquid_assets": "net_liquid_assets_ratio",
        "period": "source_period",
        "source": "source_document",
    }
    return aliases.get(text, text)


def validate_data(df: pd.DataFrame) -> tuple[list[ValidationMessage], list[ValidationMessage]]:
    errors: list[ValidationMessage] = []
    warnings: list[ValidationMessage] = []

    missing_columns = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing_columns:
        errors.append(
            ValidationMessage(
                "error",
                "Missing required columns: " + ", ".join(missing_columns),
            )
        )
        return errors, warnings

    for column in REQUIRED_COLUMNS:
        missing_count = df[column].map(is_blank).sum()
        if missing_count:
            errors.append(
                ValidationMessage(
                    "error",
                    f"{missing_count} row(s) are missing required field '{column}'.",
                )
            )

    for column in NUMERIC_COLUMNS:
        if column not in df.columns:
            continue
        invalid_rows = []
        for index, value in df[column].items():
            if is_blank(value) or is_na_text(value):
                continue
            if normalize_percent(value) is None:
                invalid_rows.append(str(index + 2))
        if invalid_rows:
            warnings.append(
                ValidationMessage(
                    "warning",
                    f"Column '{column}' has non-numeric value(s) on spreadsheet row(s): "
                    + ", ".join(invalid_rows[:8])
                    + ("..." if len(invalid_rows) > 8 else ""),
                )
            )

    return errors, warnings


def normalize_percent(value: object) -> float | None:
    if value is None or is_blank(value) or is_na_text(value):
        return None
    if isinstance(value, (int, float)) and not pd.isna(value):
        return float(value)

    text = str(value).strip()
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace("%", "").replace(",", "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def evaluate_company(row: pd.Series | dict[str, object]) -> CompanyEvaluation:
    row_dict = dict(row)
    status_text = combined_text(
        row_dict.get("final_shariah_status", ""),
        row_dict.get("objective_status", ""),
        row_dict.get("notes", ""),
    )
    notes = str(row_dict.get("notes", "") or "").strip()

    if has_review_required(status_text):
        return CompanyEvaluation(
            status="review",
            status_label="Review Required",
            metric_results=[],
            failure_reasons=["The source row has no recent financials or no Shariah opinion."],
            notes=notes,
        )

    if has_nc_by_nature(status_text):
        return CompanyEvaluation(
            status="non_compliant",
            status_label="Non-Compliant by Nature",
            metric_results=[],
            failure_reasons=["The company is marked NC by Nature in the source data."],
            notes=notes,
        )

    metric_results = evaluate_metric_rules(row_dict)
    failure_reasons = [
        f"{result.label} breaches {result.threshold}."
        for result in metric_results
        if result.passed is False
    ]
    missing_metrics = [
        result.label for result in metric_results if result.passed is None
    ]

    if missing_metrics:
        return CompanyEvaluation(
            status="review",
            status_label="Review Required",
            metric_results=metric_results,
            failure_reasons=[
                "Missing required metric value(s): " + ", ".join(missing_metrics) + "."
            ],
            notes=notes,
        )

    if failure_reasons:
        return CompanyEvaluation(
            status="non_compliant",
            status_label="Non-Compliant",
            metric_results=metric_results,
            failure_reasons=failure_reasons,
            notes=notes,
        )

    if "non-compliant" in status_text or "non compliant" in status_text:
        return CompanyEvaluation(
            status="non_compliant",
            status_label="Non-Compliant",
            metric_results=metric_results,
            failure_reasons=["The source data marks this company as non-compliant."],
            notes=notes,
        )

    return CompanyEvaluation(
        status="compliant",
        status_label="Compliant",
        metric_results=metric_results,
        failure_reasons=[],
        notes=notes,
    )


def evaluate_metric_rules(row: dict[str, object]) -> list[MetricResult]:
    results: list[MetricResult] = []
    for rule in METRIC_RULES:
        value = normalize_percent(row.get(rule["key"]))
        passed = evaluate_threshold(value, rule["operator"], rule["limit"])
        results.append(
            MetricResult(
                key=rule["key"],
                label=rule["label"],
                value=value,
                threshold=rule["threshold"],
                passed=passed,
                meaning=rule["meaning"],
            )
        )

    nla_value = normalize_percent(row.get("net_liquid_assets_ratio"))
    share_price = normalize_percent(row.get("share_price"))
    nla_passed = None
    if nla_value is not None and share_price is not None:
        nla_passed = nla_value < share_price
    results.append(
        MetricResult(
            key="net_liquid_assets_ratio",
            label="Net Liquid Assets Check",
            value=nla_value,
            threshold="NLA < share price",
            passed=nla_passed,
            meaning="Net liquid assets should be lower than the share price used in the source report.",
        )
    )
    return results


def calculate_purification(
    shares_owned: float, dividend_per_share: float, income_ratio: float | None
) -> tuple[float, float] | None:
    if income_ratio is None:
        return None
    total_dividend = float(shares_owned) * float(dividend_per_share)
    purification_amount = total_dividend * (float(income_ratio) / 100)
    return total_dividend, purification_amount


def evaluate_threshold(value: float | None, operator: str, limit: float) -> bool | None:
    if value is None:
        return None
    if operator == "lt":
        return value < limit
    if operator == "gte":
        return value >= limit
    raise ValueError(f"Unknown threshold operator: {operator}")


def status_from_evaluation(evaluation: CompanyEvaluation) -> tuple[str, str]:
    if evaluation.status == "compliant":
        return "Compliant", "#0f7a45"
    if evaluation.status == "review":
        return "Review Required", "#9a6a00"
    return evaluation.status_label, "#a61b1b"


def format_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def format_number(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.2f}"


def is_blank(value: object) -> bool:
    return value is None or str(value).strip() == ""


def is_na_text(value: object) -> bool:
    return str(value).strip().lower() in {"n/a", "na", "nan", "none", "-"}


def combined_text(*values: object) -> str:
    return " ".join(str(value or "") for value in values).strip().lower()


def has_nc_by_nature(text: str) -> bool:
    normalized = text.lower()
    return "nc by nature" in normalized or "non-compliant by nature" in normalized


def has_review_required(text: str) -> bool:
    normalized = text.lower()
    markers: Iterable[str] = (
        "no recent financial",
        "no shariah opinion",
        "review required",
        "no opinion",
    )
    return any(marker in normalized for marker in markers)
