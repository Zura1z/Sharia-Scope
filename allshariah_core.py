from __future__ import annotations

from dataclasses import dataclass

import pandas as pd




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
            label="Net Liquid Assets / share (advisory)",
            value=nla_value,
            threshold="NLA per share < market price",
            passed=nla_passed,
            meaning=("Advisory KMI indicator (not one of the five tests). Net liquid assets per "
                     "share should stay below the market price. A negative value is normal and "
                     "passes — it means the company is not a thinly-veiled cash pile."),
        )
    )
    return results




# ---------------------------------------------------------------------------
# Analyze-any-company engine
#
# Compute the screening ratios from a company's *raw* financial line items, so
# the screener works for ANY company from its own statements — no lookup table.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawFinancials:
    """Raw balance-sheet / income-statement inputs for one company.

    Amounts are in the same currency unit (e.g. PKR '000); only ratios matter,
    so the unit cancels out. ``number_of_shares`` and ``market_price_per_share``
    drive the net-liquid-assets-per-share screen.
    """

    company_name: str = ""
    ticker: str = ""
    business_compliant: bool = True
    business_activity: str = ""
    total_assets: float | None = None
    interest_bearing_debt: float | None = None
    noncompliant_investments: float | None = None
    noncompliant_income: float | None = None
    total_revenue: float | None = None
    illiquid_assets: float | None = None
    total_liabilities: float | None = None
    number_of_shares: float | None = None
    market_price_per_share: float | None = None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator * 100.0


def compute_ratios(raw: RawFinancials) -> dict[str, object]:
    """Compute the six PSX/KMI screening ratios from raw financials.

    Returns a row dict shaped like the canonical columns so it can be fed
    straight into :func:`screen_metrics`.
    """
    nla_per_share: float | None = None
    if (
        None not in (raw.total_assets, raw.illiquid_assets, raw.total_liabilities)
        and raw.number_of_shares not in (None, 0)
    ):
        net_liquid_assets = raw.total_assets - raw.illiquid_assets - raw.total_liabilities
        nla_per_share = net_liquid_assets / raw.number_of_shares

    # A missing non-compliant figure means the company has no such item (no
    # interest-bearing investments / no prohibited income) — i.e. zero, a pass.
    # Only the denominators (total assets/revenue) are genuinely "required".
    nc_invest = 0.0 if raw.noncompliant_investments is None else raw.noncompliant_investments
    nc_income = 0.0 if raw.noncompliant_income is None else raw.noncompliant_income

    return {
        "ticker": raw.ticker,
        "company_name": raw.company_name,
        "objective_status": "Compliant" if raw.business_compliant else "Non-Compliant",
        "debt_ratio": _ratio(raw.interest_bearing_debt, raw.total_assets),
        "investment_ratio": _ratio(nc_invest, raw.total_assets),
        "income_ratio": _ratio(nc_income, raw.total_revenue),
        "illiquid_assets_ratio": _ratio(raw.illiquid_assets, raw.total_assets),
        "net_liquid_assets_ratio": nla_per_share,
        "share_price": raw.market_price_per_share,
        "final_shariah_status": "",
        "source_document": "",
        "source_period": "",
        "notes": raw.business_activity,
    }


def screen_metrics(row: pd.Series | dict[str, object]) -> CompanyEvaluation:
    """Screen a company purely from its ratios + business activity.

    The verdict is computed from the numbers alone — any pre-existing
    ``final_shariah_status`` on the row is ignored. This powers the live screen.
    """
    row_dict = dict(row)
    notes = str(row_dict.get("notes", "") or "").strip()
    objective = str(row_dict.get("objective_status", "") or "").lower()

    if has_nc_by_nature(objective) or "non-compliant" in objective or "non compliant" in objective:
        return CompanyEvaluation(
            status="non_compliant",
            status_label="Non-Compliant by Nature",
            metric_results=[],
            failure_reasons=["Core business activity is not Shariah-compliant (screened out by sector)."],
            notes=notes,
        )

    metric_results = evaluate_metric_rules(row_dict)
    # The Net Liquid Assets check is ADVISORY (a KMI fallback indicator), not one of
    # the five pass/fail tests — it must never on its own force a failure or a
    # "Review Required". This mirrors the on-screen result, which excludes it too.
    _ADVISORY = {"net_liquid_assets_ratio"}
    failure_reasons = [
        f"{result.label} breaches {result.threshold}."
        for result in metric_results
        if result.passed is False and result.key not in _ADVISORY
    ]
    missing_metrics = [
        result.label for result in metric_results
        if result.passed is None and result.key not in _ADVISORY
    ]

    if missing_metrics:
        return CompanyEvaluation(
            status="review",
            status_label="Review Required",
            metric_results=metric_results,
            failure_reasons=["Missing required input(s): " + ", ".join(missing_metrics) + "."],
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

    return CompanyEvaluation(
        status="compliant",
        status_label="Compliant",
        metric_results=metric_results,
        failure_reasons=[],
        notes=notes,
    )


def screen_financials(raw: RawFinancials) -> tuple[dict[str, object], CompanyEvaluation]:
    """Compute ratios from raw financials and screen them in one step."""
    ratios = compute_ratios(raw)
    return ratios, screen_metrics(ratios)






def evaluate_threshold(value: float | None, operator: str, limit: float) -> bool | None:
    if value is None:
        return None
    if operator == "lt":
        return value < limit
    if operator == "gte":
        return value >= limit
    raise ValueError(f"Unknown threshold operator: {operator}")




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




def has_nc_by_nature(text: str) -> bool:
    normalized = text.lower()
    return "nc by nature" in normalized or "non-compliant by nature" in normalized


