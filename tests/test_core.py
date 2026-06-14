import pandas as pd

from allshariah_core import (
    RawFinancials,
    backtest,
    calculate_purification,
    compute_ratios,
    evaluate_company,
    normalize_percent,
    screen_financials,
    screen_metrics,
    validate_data,
)


def clean_financials(**overrides) -> RawFinancials:
    base = dict(
        company_name="Demo Co",
        ticker="DEMO",
        business_compliant=True,
        total_assets=1000.0,
        interest_bearing_debt=100.0,   # 10% < 37%
        noncompliant_investments=50.0,  # 5% < 33%
        noncompliant_income=2.0,        # 1% < 5%
        total_revenue=200.0,
        illiquid_assets=400.0,          # 40% >= 25%
        total_liabilities=300.0,
        number_of_shares=100.0,         # NLA/share = (1000-400-300)/100 = 3 < 50
        market_price_per_share=50.0,
    )
    base.update(overrides)
    return RawFinancials(**base)


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


# --- analyze-any-company engine -------------------------------------------
def test_compute_ratios_from_raw_financials():
    ratios = compute_ratios(clean_financials())

    assert ratios["debt_ratio"] == 10.0
    assert ratios["investment_ratio"] == 5.0
    assert ratios["income_ratio"] == 1.0
    assert ratios["illiquid_assets_ratio"] == 40.0
    assert ratios["net_liquid_assets_ratio"] == 3.0  # per share
    assert ratios["share_price"] == 50.0


def test_screen_financials_compliant():
    _ratios, evaluation = screen_financials(clean_financials())

    assert evaluation.status == "compliant"
    assert evaluation.failure_reasons == []


def test_screen_financials_debt_breach_is_non_compliant():
    _ratios, evaluation = screen_financials(clean_financials(interest_bearing_debt=500.0))  # 50% > 37%

    assert evaluation.status == "non_compliant"
    assert any("Debt Ratio" in reason for reason in evaluation.failure_reasons)


def test_business_screen_marks_nc_by_nature_without_ratios():
    _ratios, evaluation = screen_financials(clean_financials(business_compliant=False))

    assert evaluation.status == "non_compliant"
    assert evaluation.status_label == "Non-Compliant by Nature"
    assert evaluation.metric_results == []


def test_nla_per_share_breach_is_non_compliant():
    # NLA/share = (1000-400-300)/100 = 3; price below it should fail the screen.
    _ratios, evaluation = screen_financials(clean_financials(market_price_per_share=1.0))

    assert evaluation.status == "non_compliant"
    assert any("Net Liquid Assets" in reason for reason in evaluation.failure_reasons)


def test_missing_input_marks_review_required():
    _ratios, evaluation = screen_financials(clean_financials(total_revenue=None))

    assert evaluation.status == "review"


def test_screen_metrics_ignores_source_status():
    # A row whose ratios all pass should screen Compliant even if labelled otherwise.
    row = base_row(final_shariah_status="Non-Compliant")
    assert screen_metrics(row).status == "compliant"


def test_backtest_agrees_with_clean_official_rows():
    rows = [
        base_row(ticker="ABOT", final_shariah_status="Compliant"),
        base_row(ticker="ASTL", income_ratio="5.27", final_shariah_status="Non-Compliant"),
    ]
    result = backtest(pd.DataFrame(rows))

    assert result["total"] == 2
    assert result["disagree"] == 0
    assert result["accuracy"] == 1.0


# --- regression tests for QA findings -------------------------------------
def test_pdf_report_escapes_markup_and_does_not_crash():
    from report import build_pdf_report

    _ratios, evaluation = screen_financials(clean_financials(company_name="Alpha <b broken & Co"))
    pdf = build_pdf_report(
        evaluation=evaluation,
        company_name="Alpha <b broken & Co",  # would crash ReportLab without escaping
        ticker="X<Y",
        meta={"period": "Nine months ended 31 Mar 2024 (standalone) — long value", "currency_unit": "PKR 000", "source": "AI-extracted"},
        purification=None,
        generated_on="2026-06-14",
    )
    assert pdf[:4] == b"%PDF"


def test_pdf_report_renders_full_report():
    from report import build_pdf_report

    _ratios, evaluation = screen_financials(clean_financials())
    pdf = build_pdf_report(
        evaluation=evaluation,
        company_name="Demo Co",
        ticker="DEMO",
        meta={"period": "Year ended 30 June 2025", "currency_unit": "PKR 000", "source": "Manual entry"},
        purification={"shares": "1,000", "dps": "10.00", "income_ratio": "1.00%", "total_dividend": "10,000.00", "purification_amount": "100.00"},
        generated_on="2026-06-14",
    )
    assert pdf[:4] == b"%PDF" and len(pdf) > 1500


def test_raw_financials_equality_detects_input_change():
    # The app uses RawFinancials equality to invalidate stale results.
    a = clean_financials()
    assert a == clean_financials()
    assert a != clean_financials(interest_bearing_debt=999.0)


def test_parser_captures_exception_markers():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "parse_index_pdf.py"
    spec = importlib.util.spec_from_file_location("parse_index_pdf", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    sample = (
        "  168 HUBC        Hub Power Company Ltd * #          Compliant     18.84%   42.35%   7.16%   37.34%   71.91   221.38   Compliant\n"
        "  244 NETSOL      Netsol Technologies Ltd * ##      Compliant      5.91%   1.31%   1.49%    8.73%   114.67   134.69   Compliant\n"
    )
    rows = {r["ticker"]: r for r in mod.parse_rows(sample)}
    assert "circular debt" in rows["HUBC"]["notes"].lower()
    assert "service-based" in rows["NETSOL"]["notes"].lower()
    assert rows["HUBC"]["company_name"] == "Hub Power Company Ltd"  # markers stripped


def test_storage_credentials_optional(monkeypatch):
    import storage

    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    assert storage.available() in (True, False)
    # No credential configured anywhere -> None (history stays disabled, app still works).
    assert storage.resolve_credentials(None, base_dir="/nonexistent-sharia-dir") is None
    # An explicit dict is passed straight through.
    cred = {"project_id": "shariascope"}
    assert storage.resolve_credentials(cred) is cred


def test_sufficient_escalates_on_implausible_values():
    import ai_extract
    from allshariah_core import RawFinancials

    # The real Dewan Cement case: Haiku put the revaluation surplus (16.0M, equity)
    # into interest-bearing debt — which exceeds total liabilities (6.4M). Must escalate.
    bad = RawFinancials(total_assets=47553165, total_revenue=18225608, interest_bearing_debt=16041308,
                        illiquid_assets=35969816, total_liabilities=6433341)
    assert ai_extract._sufficient(bad) is False
    good = RawFinancials(total_assets=1000, total_revenue=200, interest_bearing_debt=100,
                         illiquid_assets=400, total_liabilities=300)
    assert ai_extract._sufficient(good) is True


def test_bedrock_ladder_orders_cheap_to_expensive():
    import ai_extract

    avail = [
        "anthropic.claude-3-haiku-20240307-v1:0",
        "eu.anthropic.claude-3-haiku-20240307-v1:0",
        "eu.anthropic.claude-sonnet-4-6-20251114-v1:0",
        "eu.anthropic.claude-opus-4-8-20260101-v1:0",
        "anthropic.titan-text",  # ignored (not a Claude tier)
    ]
    ladder = ai_extract.bedrock_ladder(avail)
    assert [ai_extract._tier_of(m) for m in ladder] == ["haiku", "sonnet", "opus"]
    assert ladder[0].startswith("eu.")  # region inference profile preferred for EU


def test_is_complete_requires_expected_artifacts():
    import storage

    assert storage._is_complete({}, None, None) is False  # nothing archived -> not complete
    assert storage._is_complete({"report_path": "x"}, None, b"r") is True
    assert storage._is_complete({}, None, b"r") is False  # report expected but missing
    assert storage._is_complete({"report_path": "x"}, {"bytes": b"s"}, b"r") is False  # source expected, missing
    assert storage._is_complete({"report_path": "x", "source_path": "y"}, {"bytes": b"s"}, b"r") is True
    # an inherited source (derived revision) counts as present
    assert storage._is_complete({"report_path": "x", "source_path": "p"}, None, b"r", {"source_path": "p"}) is True


def test_storage_round_trip_live():
    """Full Firestore + Storage round trip — only runs when a credential is present."""
    import os
    import pytest

    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        pytest.skip("no live Firebase credential")
    import storage

    cred = storage.resolve_credentials(None)
    res = storage.save_run(
        cred,
        record={"ticker": "TEST", "company_name": "PyTest Co", "status": "compliant", "status_label": "Compliant", "ratios": {}},
        source={"bytes": b"%PDF-1.4 source", "name": "t.pdf"},
        report_bytes=b"%PDF-1.4 report",
    )
    try:
        assert res["id"] and res["files_archived"] is True
        runs = storage.list_runs(cred, limit=20)
        row = next(x for x in runs if x["id"] == res["id"])
        assert storage.download_blob(cred, row["report_path"]) == b"%PDF-1.4 report"
        assert storage.download_blob(cred, row["source_path"]) == b"%PDF-1.4 source"
    finally:
        storage.delete_run(cred, res["id"])
    assert all(x["id"] != res["id"] for x in storage.list_runs(cred, limit=20))
