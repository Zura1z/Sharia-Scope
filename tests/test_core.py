import pandas as pd

from allshariah_core import (
    calculate_purification,
    evaluate_company,
    normalize_percent,
    validate_data,
)


def base_row(**overrides):
    row = {
        "ticker": "ABOT",
        "company_name": "Abbott Lab (Pakistan) Ltd",
        "objective_status": "Compliant",
        "debt_ratio": "1.04",
        "investment_ratio": "0.00",
        "income_ratio": "0.63",
        "illiquid_assets_ratio": "61.42",
        "net_liquid_assets_ratio": "29.33",
        "share_price": "1050.14",
        "final_shariah_status": "Compliant",
        "source_document": "Karachi-Meezan-Index.pdf",
        "source_period": "Period ended December 2025",
        "notes": "",
    }
    row.update(overrides)
    return row


def test_normalize_percent_accepts_common_inputs():
    assert normalize_percent("4.97") == 4.97
    assert normalize_percent("4.97%") == 4.97
    assert normalize_percent("(143.92)") == -143.92
    assert normalize_percent("N/A") is None
    assert normalize_percent("") is None
    assert normalize_percent("not a number") is None


def test_compliant_company_passes_all_metrics():
    evaluation = evaluate_company(base_row())

    assert evaluation.status == "compliant"
    assert evaluation.failure_reasons == []
    assert all(metric.passed for metric in evaluation.metric_results)


def test_ratio_failure_marks_non_compliant():
    evaluation = evaluate_company(base_row(income_ratio="5.27"))

    assert evaluation.status == "non_compliant"
    assert any("Non-Compliant Income Ratio" in reason for reason in evaluation.failure_reasons)


def test_nc_by_nature_skips_metrics():
    evaluation = evaluate_company(
        base_row(
            ticker="MCB",
            company_name="MCB Bank Ltd",
            objective_status="Non-Compliant",
            final_shariah_status="Non-Compliant",
            debt_ratio="",
            investment_ratio="",
            income_ratio="",
            illiquid_assets_ratio="",
            net_liquid_assets_ratio="",
            share_price="",
            notes="NC by Nature - conventional banking.",
        )
    )

    assert evaluation.status == "non_compliant"
    assert evaluation.status_label == "Non-Compliant by Nature"
    assert evaluation.metric_results == []


def test_missing_financials_mark_review_required():
    evaluation = evaluate_company(
        base_row(
            final_shariah_status="Review Required",
            debt_ratio="",
            investment_ratio="",
            income_ratio="",
            illiquid_assets_ratio="",
            net_liquid_assets_ratio="",
            share_price="",
            notes="As no recent financial available therefore no Shariah opinion is drawn.",
        )
    )

    assert evaluation.status == "review"
    assert "no recent financials" in evaluation.failure_reasons[0].lower()


def test_exception_notes_remain_visible_but_rules_still_evaluate():
    evaluation = evaluate_company(
        base_row(
            investment_ratio="42.35",
            income_ratio="7.16",
            final_shariah_status="Compliant",
            notes="Exception granted in source report.",
        )
    )

    assert evaluation.status == "non_compliant"
    assert evaluation.notes == "Exception granted in source report."
    assert len(evaluation.failure_reasons) == 2


def test_calculate_purification():
    result = calculate_purification(500, 10, 3)

    assert result == (5000, 150)


def test_validate_data_reports_missing_required_values():
    df = pd.DataFrame([base_row(ticker="")])

    errors, warnings = validate_data(df)

    assert errors
    assert not warnings


def test_validate_data_warns_on_invalid_numeric_values():
    df = pd.DataFrame([base_row(debt_ratio="abc")])

    errors, warnings = validate_data(df)

    assert not errors
    assert warnings
