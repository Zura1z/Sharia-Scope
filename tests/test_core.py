from allshariah_core import (
    RawFinancials,
    compute_ratios,
    normalize_percent,
    screen_financials,
    screen_metrics,
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


def test_nla_per_share_is_advisory_not_blocking():
    # NLA/share = (1000-400-300)/100 = 3 > price 1. The Net Liquid Assets check is
    # advisory (a KMI indicator), so it must NOT force non-compliant or review when
    # the five core tests pass.
    _ratios, evaluation = screen_financials(clean_financials(market_price_per_share=1.0))

    assert evaluation.status == "compliant"
    assert not any("Net Liquid Assets" in reason for reason in evaluation.failure_reasons)


def test_missing_input_marks_review_required():
    _ratios, evaluation = screen_financials(clean_financials(total_revenue=None))

    assert evaluation.status == "review"


def test_screen_metrics_ignores_source_status():
    # A row whose ratios all pass should screen Compliant even if labelled otherwise.
    row = base_row(final_shariah_status="Non-Compliant")
    assert screen_metrics(row).status == "compliant"


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




def test_storage_credentials_optional(monkeypatch):
    import storage

    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    assert storage.available() in (True, False)
    # No credential configured anywhere -> None (history stays disabled, app still works).
    assert storage.resolve_credentials(None, base_dir="/nonexistent-sharia-dir") is None
    # An explicit dict is passed straight through.
    cred = {"project_id": "shariascope"}
    assert storage.resolve_credentials(cred) is cred


def test_resolve_credentials_reads_inline_env_json(monkeypatch, tmp_path):
    import storage

    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    # A host like Replit supplies the key as a JSON string in an env var.
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT", '{"project_id": "demo", "type": "service_account"}')
    assert storage.resolve_credentials(None, base_dir=tmp_path) == {"project_id": "demo", "type": "service_account"}
    # Malformed JSON returns None rather than raising.
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT", "{not valid json")
    assert storage.resolve_credentials(None, base_dir=tmp_path) is None


def test_sufficient_escalates_on_implausible_values():
    import ai_extract
    from allshariah_core import RawFinancials

    # The real Dewan Cement case: Haiku put the revaluation surplus (16.0M, equity)
    # into interest-bearing debt — which exceeds total liabilities (6.4M). Must escalate.
    bad = RawFinancials(total_assets=47553165, total_revenue=18225608, interest_bearing_debt=16041308,
                        illiquid_assets=35969816, total_liabilities=6433341)
    assert ai_extract._extraction_is_plausible(bad) is False
    good = RawFinancials(total_assets=1000, total_revenue=200, interest_bearing_debt=100,
                         illiquid_assets=400, total_liabilities=300)
    assert ai_extract._extraction_is_plausible(good) is True


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
    assert [ai_extract._model_tier(m) for m in ladder] == ["haiku", "sonnet", "opus"]
    assert ladder[0].startswith("eu.")  # region inference profile preferred for EU


def test_server_does_not_default_to_bedrock_when_aws_creds_exist(monkeypatch):
    import ai_extract
    import server

    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("AWS_REGION", "eu-central-1")

    assert server._ai_provider() == ai_extract.PROVIDER_ANTHROPIC
    assert server._bedrock_ready() is False
    assert server._ai_ready() is False

    monkeypatch.setenv("AI_PROVIDER", ai_extract.PROVIDER_BEDROCK)

    assert server._ai_provider() == ai_extract.PROVIDER_BEDROCK
    assert server._bedrock_ready() is True
    assert server._ai_ready() is True


def test_server_without_any_ai_credentials_is_not_ready(monkeypatch):
    import ai_extract
    import server

    for key in (
        "AI_PROVIDER",
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_SESSION_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(ai_extract, "_aws_chain_has_credentials", lambda: False)

    assert server._ai_provider() == ai_extract.PROVIDER_ANTHROPIC
    assert server._bedrock_ready() is False
    assert server._ai_ready() is False


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


# --- #2 deterministic debt classification from transcribed lines ------------
CRESCENT_LINES = [
    {"label": "Issued, subscribed and paid up share capital", "amount": 1000000, "section": "equity"},
    {"label": "Surplus on revaluation of operating fixed assets", "amount": 8864542, "section": "equity"},
    {"label": "Revenue reserves", "amount": 3162955, "section": "equity"},
    {"label": "Long term financing", "amount": 345923, "section": "non_current_liability"},
    {"label": "Deferred taxation", "amount": 820000, "section": "non_current_liability"},
    {"label": "Trade and other payables", "amount": 3593585, "section": "current_liability"},
    {"label": "Short term borrowings", "amount": 8555419, "section": "current_liability"},
    {"label": "Current portion of non current liabilities", "amount": 364798, "section": "current_liability"},
    {"label": "Unclaimed dividend", "amount": 11000, "section": "current_liability"},
    {"label": "Provision for taxation", "amount": 120000, "section": "current_liability"},
]


def test_classify_debt_matches_real_crescent_lines():
    import ai_extract

    debt, included = ai_extract.classify_debt_from_lines(CRESCENT_LINES)
    # Long term financing + short term borrowings + current portion = the true debt.
    assert debt == 9266140
    labels = {x["label"] for x in included}
    assert labels == {"Long term financing", "Short term borrowings", "Current portion of non current liabilities"}
    # 35.5% < 37% -> the debt screen passes, matching Meezan's Compliant verdict.
    assert debt / 26071358 * 100 < 37


def test_classify_debt_excludes_revaluation_deferred_tax_and_accruals():
    import ai_extract

    # The Dewan trap: revaluation surplus + deferred tax + payables + accrued markup
    # must all be EXCLUDED even when mis-sectioned as liabilities.
    lines = [
        {"label": "Long term financing", "amount": 2000000, "section": "non_current_liability"},
        {"label": "Surplus on revaluation of property, plant and equipment", "amount": 16000000, "section": "non_current_liability"},
        {"label": "Deferred tax liability", "amount": 13000000, "section": "non_current_liability"},
        {"label": "Trade and other payables", "amount": 3270000, "section": "current_liability"},
        {"label": "Accrued markup on borrowings", "amount": 450000, "section": "current_liability"},
        {"label": "Short term borrowings", "amount": 5000000, "section": "current_liability"},
    ]
    debt, _ = ai_extract.classify_debt_from_lines(lines)
    assert debt == 7000000  # only the two real borrowing lines


def test_classify_debt_includes_islamic_and_lease_instruments():
    import ai_extract

    lines = [
        {"label": "Diminishing musharaka", "amount": 100, "section": "non_current_liability"},
        {"label": "Sukuk certificates", "amount": 200, "section": "non_current_liability"},
        {"label": "Lease liabilities", "amount": 50, "section": "non_current_liability"},
        {"label": "Current portion of lease liabilities", "amount": 10, "section": "current_liability"},
        {"label": "Trade deposits and accrued markup", "amount": 999, "section": "current_liability"},
    ]
    debt, _ = ai_extract.classify_debt_from_lines(lines)
    assert debt == 360  # 100 + 200 + 50 + 10; the deposits/accrued line excluded


def test_classify_debt_returns_none_without_liability_lines():
    import ai_extract

    assert ai_extract.classify_debt_from_lines([]) == (None, [])
    assert ai_extract.classify_debt_from_lines([{"label": "Cash", "amount": 5, "section": "current_asset"}]) == (None, [])


def test_debt_falls_back_to_components_when_lines_find_none():
    import ai_extract

    # The ICC Industries case: interest-bearing directors' loans are booked under
    # equity (so the liability lines classify to zero), but the model reported the
    # interest-bearing portion in long_term_borrowings. A zero line sum must not
    # override that.
    payload = {
        "balance_sheet_lines": [
            {"label": "Directors Loan", "amount": 761328431, "section": "equity"},
            {"label": "Deferred tax liability", "amount": 2364443, "section": "non_current_liability"},
            {"label": "Trade and other payables", "amount": 119214053, "section": "current_liability"},
        ],
        "long_term_borrowings": 145000000,
        "short_term_borrowings": 0,
        "current_portion_long_term_debt": 0,
        "interest_bearing_debt": 145000000,
    }
    debt, method, _lines = ai_extract._select_interest_bearing_debt(payload)
    assert debt == 145000000 and method == "named_components"


def test_debt_prefers_positive_line_sum_over_model_aggregate():
    import ai_extract

    # When the lines DO find debt, that deterministic figure wins even if the
    # model's aggregate is inflated (the Dewan deferred-tax case).
    payload = {
        "balance_sheet_lines": [
            {"label": "Short term borrowings", "amount": 5000, "section": "current_liability"},
            {"label": "Deferred tax liability", "amount": 13000, "section": "non_current_liability"},
        ],
        "long_term_borrowings": 18000,
        "interest_bearing_debt": 18000,  # inflated: includes deferred tax
    }
    debt, method, _lines = ai_extract._select_interest_bearing_debt(payload)
    assert debt == 5000 and method == "balance_sheet_lines"


def test_section_total_sums_equity_lines():
    import ai_extract

    assert ai_extract._sum_section(CRESCENT_LINES, ("equity",)) == 13027497


def test_period_helpers():
    import ai_extract

    assert ai_extract.period_label(9) == "Nine months (3rd-quarter cumulative)"
    assert ai_extract.period_label(12).startswith("Full year")
    assert ai_extract.period_label(None) == ""
    assert ai_extract.is_partial_period(9) is True
    assert ai_extract.is_partial_period(12) is False


def test_yahoo_symbol_maps_to_karachi():
    import market_data

    assert market_data.yahoo_symbol("crtm") == "CRTM.KA"
    assert market_data.yahoo_symbol("LUCK.KA") == "LUCK.KA"
    assert market_data.yahoo_symbol("") == ""


def test_resolve_ticker_prefers_index_match_over_document_symbol():
    import market_data

    # A report said 'CRESTEX'; the authoritative PSX/Yahoo symbol is 'CRTM'.
    pairs = [("CRTM", "Crescent Textile Mills Ltd"), ("LUCK", "Lucky Cement Limited")]
    assert market_data.resolve_ticker("CRESTEX", "The Crescent Textile Mills Limited", pairs) == "CRTM"
    # No index match -> keep the extracted ticker.
    assert market_data.resolve_ticker("XYZ", "Unknown Co", pairs) == "XYZ"


def test_foots_detects_non_footing_section():
    import ai_extract

    assert ai_extract._section_matches_subtotal(100.0, 100.0) is True
    assert ai_extract._section_matches_subtotal(100.0, 100.4) is True   # within 1%
    assert ai_extract._section_matches_subtotal(106.0, 100.0) is False  # 6% off -> a line was mis-transcribed
    assert ai_extract._section_matches_subtotal(None, 100.0) is None     # can't check


def test_needs_stronger_escalates_haiku_only_on_material_debt():
    import ai_extract
    from allshariah_core import RawFinancials

    levered = RawFinancials(total_assets=1000.0, interest_bearing_debt=250.0)  # 25% of assets
    debt_light = RawFinancials(total_assets=1000.0, interest_bearing_debt=20.0)  # 2% of assets
    assert ai_extract._should_escalate_for_debt(levered, "eu.anthropic.claude-haiku-4-5-20251001-v1:0") is True
    assert ai_extract._should_escalate_for_debt(debt_light, "eu.anthropic.claude-haiku-4-5-20251001-v1:0") is False
    # A stronger tier is already the final word — never "needs stronger".
    assert ai_extract._should_escalate_for_debt(levered, "eu.anthropic.claude-sonnet-4-6") is False


def test_share_count_derived_from_paid_up_capital_matches_reported():
    import ai_extract

    # Ghani: paid-up capital 9,997 (mn) / Rs.10 = ~1,000 (mn) shares, agreeing with
    # the model's own count — so the directly-read count is kept.
    shares, source, mismatch = ai_extract._shares_outstanding(
        {"number_of_shares": 1000.0, "paid_up_capital": 9997.0, "share_face_value": 10.0}
    )
    assert source == "reported" and mismatch is False and shares == 1000.0


def test_share_count_fixes_scale_mismatch_using_paid_up_capital():
    import ai_extract

    # Balance sheet read in millions (capital 9,997mn -> 999.7mn shares) but the
    # share count read in actual units (1,000,000,000). That power-of-1000 gap is
    # corrected to the balance-sheet scale and flagged.
    shares, source, mismatch = ai_extract._shares_outstanding(
        {"number_of_shares": 1_000_000_000.0, "paid_up_capital": 9997.0, "share_face_value": 10.0}
    )
    assert source == "paid_up_capital" and mismatch is True
    assert abs(shares - 999.7) < 0.1


def test_share_count_falls_back_to_reported_without_capital():
    import ai_extract

    shares, source, mismatch = ai_extract._shares_outstanding({"number_of_shares": 500.0})
    assert source == "reported" and mismatch is False and shares == 500.0


def test_looks_like_scale_gap():
    import ai_extract

    assert ai_extract._looks_like_scale_gap(1000.0, 1_000_000.0) is True   # x1000
    assert ai_extract._looks_like_scale_gap(5.0, 10.0) is False            # face-value diff, not scale
    assert ai_extract._looks_like_scale_gap(0, 1000.0) is False
