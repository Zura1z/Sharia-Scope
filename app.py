"""Sharia Scope — analyze any company's Shariah compliance from its financials.

Give the app any company's annual/quarterly statements (manually, or via
AI-assisted extraction). It computes the six PSX/KMI screening ratios from the
raw numbers, returns a compliant / non-compliant verdict with the reasons, runs
a dividend-purification calculation, and produces a PDF tear-sheet. Saved runs
are archived to Firebase (optional). The bundled Meezan index sheet is used only
to backtest the analyzer, never as a lookup.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import ai_extract
import market_data
import storage
from allshariah_core import (
    RawFinancials,
    backtest,
    calculate_purification,
    compute_ratios,
    format_number,
    format_percent,
    load_data,
    screen_metrics,
)
from report import build_pdf_report

APP_DIR = Path(__file__).resolve().parent
INDEX_CSV = APP_DIR / "data" / "kmi_all_share_index_dec2025.csv"
APP_VERSION = "1.2"
RULE_VERSION = "PSX-KMI-Dec2025"

STATUS_COLORS = {"compliant": "#0f7a45", "non_compliant": "#a61b1b", "review": "#9a6a00"}

FIN_FIELDS = [
    ("fin_total_assets", "Total assets", "Total assets from the balance sheet."),
    ("fin_interest_bearing_debt", "Interest-bearing debt", "Short- + long-term interest-based borrowings, loans, leases."),
    ("fin_noncompliant_investments", "Non-compliant investments", "Investments in non-Shariah instruments / non-compliant company shares."),
    ("fin_noncompliant_income", "Non-compliant income", "Interest income + income from other non-Shariah sources."),
    ("fin_total_revenue", "Total revenue", "Total revenue/turnover + other income (income-ratio denominator)."),
    ("fin_illiquid_assets", "Illiquid assets", "Total assets minus liquid assets (cash, receivables, short-term investments)."),
    ("fin_total_liabilities", "Total liabilities", "Total liabilities from the balance sheet."),
    ("fin_number_of_shares", "Number of shares", "Ordinary shares outstanding — in the SAME scale as the amounts above."),
    ("fin_market_price_per_share", "Market price per share", "Current market price — NOT in the statements; enter it manually even after AI extraction."),
]
BUSINESS_YES = "Yes — Shariah-compliant business"
BUSINESS_NO = "No — non-compliant by nature"
MODEL_OPTIONS = ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"]

st.set_page_config(page_title="Sharia Scope", page_icon="☪️", layout="wide")

st.markdown(
    """
    <style>
      #MainMenu, header [data-testid="stToolbar"], footer {visibility: hidden;}
      .block-container {padding-top: 2.6rem; max-width: 1080px;}
      .ss-title {font-size: 1.7rem; font-weight: 700; color: var(--text-color); margin:0; line-height:1.55; padding-top:.25rem;}
      .ss-tag {color: var(--text-color); opacity:.7; font-size:.9rem; margin:.1rem 0 0;}
      .ss-chip {display:inline-block; border:1px solid rgba(128,128,128,.3); border-radius:999px;
                padding:3px 11px; font-size:.74rem; margin:2px 6px 2px 0; color:var(--text-color);}
      .ss-chip.on  {border-color:#1a9e5f; color:#1a9e5f;}
      .ss-chip.warn{border-color:#d39a2a; color:#d39a2a;}
      .ss-chip.off {opacity:.6;}
      .ss-badge {display:inline-block; color:#fff; border-radius:8px; padding:7px 16px; font-weight:700; font-size:1.05rem;}
      .ss-card {border:1px solid rgba(128,128,128,.28); border-radius:12px; padding:14px 16px; height:100%;
                background:var(--secondary-background-color, rgba(128,128,128,.08));}
      .ss-card .k {font-size:.75rem; color:var(--text-color); opacity:.62; text-transform:uppercase; letter-spacing:.04em;}
      .ss-card .v {font-size:1.25rem; font-weight:700; color:var(--text-color); margin:2px 0;}
      .ss-card .t {font-size:.74rem; color:var(--text-color); opacity:.66;}
      .ss-step {font-size:.72rem; text-transform:uppercase; letter-spacing:.06em; color:var(--text-color); opacity:.6; margin-bottom:.2rem;}
      .ss-pass {color:#1a9e5f; font-weight:600;} .ss-fail {color:#e0574c; font-weight:600;} .ss-na {color:#d39a2a; font-weight:600;}
    </style>
    """,
    unsafe_allow_html=True,
)

# --- Persistent config (NOT widget keys: dialog widget keys get wiped when the
#     dialog closes, so the Settings dialog syncs into these on "Done"). --------
st.session_state.setdefault("provider_label", "Anthropic API")
st.session_state.setdefault("model", MODEL_OPTIONS[0])
st.session_state.setdefault("bedrock_model", ai_extract.DEFAULT_BEDROCK_MODEL)
st.session_state.setdefault("smart", True)
st.session_state.setdefault("aws_region", "eu-central-1")

# Maps Settings-dialog widget keys (w_*) → persistent keys.
_CFG_SYNC = {
    "w_provider": "provider_label", "w_api_key": "api_key", "w_model": "model",
    "w_bedrock_model": "bedrock_model", "w_aws_key": "aws_key", "w_aws_secret": "aws_secret",
    "w_aws_region": "aws_region", "w_smart": "smart", "w_bucket": "bucket",
}
_CFG_DEFAULTS = {
    "w_provider": "Anthropic API", "w_api_key": "", "w_model": MODEL_OPTIONS[0],
    "w_bedrock_model": ai_extract.DEFAULT_BEDROCK_MODEL, "w_aws_key": "", "w_aws_secret": "",
    "w_aws_region": "eu-central-1", "w_smart": True, "w_bucket": "",
}


def read_config():
    """Resolve the effective AI + database config from the PERSISTENT keys."""
    label = st.session_state.get("provider_label", "Anthropic API")
    if label == "AWS Bedrock":
        provider = ai_extract.PROVIDER_BEDROCK
        ai_cfg = {
            "provider": provider,
            "model": st.session_state.get("bedrock_model") or ai_extract.DEFAULT_BEDROCK_MODEL,
            "aws_access_key": st.session_state.get("aws_key") or None,
            "aws_secret_key": st.session_state.get("aws_secret") or None,
            "aws_region": st.session_state.get("aws_region") or None,
        }
        key_present = ai_extract.credentials_present(
            provider, aws_access_key=ai_cfg["aws_access_key"], aws_secret_key=ai_cfg["aws_secret_key"], aws_region=ai_cfg["aws_region"]
        )
    else:
        provider = ai_extract.PROVIDER_ANTHROPIC
        ai_cfg = {"provider": provider, "model": st.session_state.get("model", MODEL_OPTIONS[0]), "api_key": st.session_state.get("api_key") or None}
        key_present = ai_extract.credentials_present(provider, api_key=ai_cfg["api_key"])
    ai_cfg["smart"] = st.session_state.get("smart", True)

    fb_cred, fb_bucket, storage_ok = None, None, False
    if storage.available():
        fb_cred = storage.resolve_credentials(st.session_state.get("fb_cred_dict"), base_dir=APP_DIR)
        if fb_cred:
            fb_bucket = st.session_state.get("bucket") or storage.default_bucket(fb_cred)
            bk = f"storage_ok::{fb_bucket}"
            if bk not in st.session_state:
                st.session_state[bk] = storage.bucket_exists(fb_cred, fb_bucket)
            storage_ok = st.session_state[bk]
    return ai_cfg, key_present, fb_cred, fb_bucket, storage_ok


@st.dialog("Settings", width="large")
def settings_dialog():
    # Seed dialog widgets from the persisted values (so re-opening shows them).
    for wk, default in _CFG_DEFAULTS.items():
        st.session_state.setdefault(wk, st.session_state.get(_CFG_SYNC[wk], default))

    st.caption("Credentials stay in this browser session; they are never written to the repo.")
    st.subheader("AI provider")
    st.radio("Provider", ["Anthropic API", "AWS Bedrock"], key="w_provider", horizontal=True)
    if st.session_state.get("w_provider") == "AWS Bedrock":
        st.caption("Leave keys blank to use your AWS default credential chain.")
        st.text_input("AWS Access Key ID (optional)", type="password", key="w_aws_key")
        st.text_input("AWS Secret Access Key (optional)", type="password", key="w_aws_secret")
        st.text_input("AWS Region", key="w_aws_region")
        if st.button("🔎 List available Claude models"):
            with st.spinner("Querying Bedrock…"):
                try:
                    models = ai_extract.list_bedrock_models(
                        st.session_state.get("w_aws_key") or None,
                        st.session_state.get("w_aws_secret") or None,
                        st.session_state.get("w_aws_region") or None,
                    )
                    st.session_state["bedrock_models"] = models
                    if not models:
                        st.warning("No Anthropic models found. Check the region, and that model access is granted in the Bedrock console.")
                except Exception as exc:
                    st.error(f"Could not list models: {exc}")
        avail = st.session_state.get("bedrock_models") or []
        if avail:
            if st.session_state.get("w_bedrock_model") not in avail:
                st.session_state["w_bedrock_model"] = avail[0]
            st.selectbox(f"Bedrock model ({len(avail)} available)", avail, key="w_bedrock_model")
            st.caption("Smart mode builds a Haiku → Sonnet → Opus ladder from these.")
        else:
            st.text_input("Bedrock model ID (list them above to populate)", key="w_bedrock_model")
        if st.button("Test connection"):
            with st.spinner("Testing…"):
                err = ai_extract.test_connection(
                    ai_extract.PROVIDER_BEDROCK, st.session_state.get("w_bedrock_model"),
                    aws_access_key=st.session_state.get("w_aws_key") or None,
                    aws_secret_key=st.session_state.get("w_aws_secret") or None,
                    aws_region=st.session_state.get("w_aws_region") or None,
                )
                st.success("Connection OK.") if err is None else st.error(err)
    else:
        st.text_input("Anthropic API key", type="password", key="w_api_key",
                      help="Used only for AI extraction; or set ANTHROPIC_API_KEY.")
        st.selectbox("Model", MODEL_OPTIONS, key="w_model", help="Opus is most accurate; Sonnet/Haiku are cheaper.")

    st.toggle("Smart extraction — start cheap (Haiku) and escalate only if needed", key="w_smart")
    st.caption("Off = always use the model above. Local text/OCR is tried first either way, to cut tokens." +
               ("" if ai_extract.ocr_available() else " (OCR engine not detected — scanned PDFs fall back to vision.)"))

    st.divider()
    st.subheader("Database (optional)")
    if not storage.available():
        st.caption("Install `firebase-admin` to enable saving runs.")
    else:
        up = st.file_uploader("Firebase service-account JSON", type=["json"], key="w_db_file")
        if up is not None:
            try:
                st.session_state["fb_cred_dict"] = json.loads(up.getvalue())
            except json.JSONDecodeError:
                st.error("That file isn't valid JSON.")
        cred = storage.resolve_credentials(st.session_state.get("fb_cred_dict"), base_dir=APP_DIR)
        if cred:
            st.success(f"Key loaded for project `{cred.get('project_id', '?')}`.")
            st.text_input("Storage bucket", key="w_bucket", placeholder=storage.default_bucket(cred))
        else:
            st.caption("No key yet — runs won't be saved.")
    st.divider()
    st.subheader("AI spend")
    sa = st.session_state.get("cost_total_anthropic", 0.0)
    sb = st.session_state.get("cost_total_bedrock", 0.0)
    st.caption(f"This session — Claude: ${sa:.4f} · Bedrock: ${sb:.4f}")
    _cred = storage.resolve_credentials(st.session_state.get("fb_cred_dict"), base_dir=APP_DIR) if storage.available() else None
    if _cred:
        tot = storage.read_costs(_cred)
        st.caption(f"All-time (Firebase) — Claude: ${tot.get('anthropic_usd', 0):.4f} · Bedrock: ${tot.get('bedrock_usd', 0):.4f}")

    st.divider()
    if st.button("Save & close", type="primary"):
        # Persist widget values to the durable keys (they survive the dialog close).
        for wk, pk in _CFG_SYNC.items():
            if wk in st.session_state:
                st.session_state[pk] = st.session_state[wk]
        for k in [k for k in st.session_state if k.startswith("storage_ok::")]:
            st.session_state.pop(k, None)
        st.rerun()


# --- Header + status strip --------------------------------------------------
ai_cfg, key_present, fb_cred, fb_bucket, storage_ok = read_config()

hcol, scol = st.columns([5, 1])
with hcol:
    st.markdown('<div class="ss-title">☪️ Sharia Scope</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="ss-tag">Analyze any company\'s Shariah compliance from its financial statements — '
        "PSX/KMI (SECP-aligned) screening matrix.</div>",
        unsafe_allow_html=True,
    )
with scol:
    st.write("")
    if st.button("⚙ Settings", use_container_width=True):
        settings_dialog()

ai_chip = '<span class="ss-chip on">AI ready</span>' if key_present else '<span class="ss-chip off">AI off — manual entry</span>'
if not fb_cred:
    arch_chip = '<span class="ss-chip off">Archive off</span>'
elif storage_ok:
    arch_chip = '<span class="ss-chip on">Archive: full</span>'
else:
    arch_chip = '<span class="ss-chip warn">Archive: metadata only</span>'
st.markdown(ai_chip + arch_chip, unsafe_allow_html=True)

NAV_OPTIONS = ["Analyze", "Saved", "Validation", "Methodology"]
nav = st.segmented_control("Section", NAV_OPTIONS, default="Analyze", key="nav", label_visibility="collapsed") or "Analyze"


# --- helpers ----------------------------------------------------------------
def num_field(key, label, help_text):
    return st.number_input(label, value=None, format="%.4f", key=key, help=help_text)


def session_raw() -> RawFinancials:
    return RawFinancials(
        company_name=st.session_state.get("fin_company_name", "") or "",
        ticker=st.session_state.get("fin_ticker", "") or "",
        business_compliant=st.session_state.get("fin_business", BUSINESS_YES) == BUSINESS_YES,
        business_activity=st.session_state.get("fin_business_activity", "") or "",
        total_assets=st.session_state.get("fin_total_assets"),
        interest_bearing_debt=st.session_state.get("fin_interest_bearing_debt"),
        noncompliant_investments=st.session_state.get("fin_noncompliant_investments"),
        noncompliant_income=st.session_state.get("fin_noncompliant_income"),
        total_revenue=st.session_state.get("fin_total_revenue"),
        illiquid_assets=st.session_state.get("fin_illiquid_assets"),
        total_liabilities=st.session_state.get("fin_total_liabilities"),
        number_of_shares=st.session_state.get("fin_number_of_shares"),
        market_price_per_share=st.session_state.get("fin_market_price_per_share"),
    )


def ratio_card(label, value_str, threshold, passed):
    if passed is True:
        verdict = '<span class="ss-pass">✓ Pass</span>'
    elif passed is False:
        verdict = '<span class="ss-fail">✗ Fail</span>'
    else:
        verdict = '<span class="ss-na">— No data</span>'
    return (f'<div class="ss-card"><div class="k">{label}</div><div class="v">{value_str}</div>'
            f'<div class="t">{threshold} &nbsp;·&nbsp; {verdict}</div></div>')


def apply_extraction(raw: RawFinancials, meta: dict) -> None:
    st.session_state["fin_company_name"] = raw.company_name
    st.session_state["fin_ticker"] = raw.ticker
    st.session_state["fin_business"] = BUSINESS_YES if raw.business_compliant else BUSINESS_NO
    st.session_state["fin_business_activity"] = raw.business_activity
    st.session_state["fin_total_assets"] = raw.total_assets
    st.session_state["fin_interest_bearing_debt"] = raw.interest_bearing_debt
    st.session_state["fin_noncompliant_investments"] = raw.noncompliant_investments
    st.session_state["fin_noncompliant_income"] = raw.noncompliant_income
    st.session_state["fin_total_revenue"] = raw.total_revenue
    st.session_state["fin_illiquid_assets"] = raw.illiquid_assets
    st.session_state["fin_total_liabilities"] = raw.total_liabilities
    st.session_state["fin_number_of_shares"] = raw.number_of_shares
    st.session_state["fin_market_price_per_share"] = raw.market_price_per_share
    st.session_state["fin_period"] = meta.get("period", "")
    st.session_state["fin_currency_unit"] = meta.get("currency_unit", "")
    st.session_state["fin_period_months"] = meta.get("reporting_period_months")
    st.session_state["extraction_meta"] = meta
    st.session_state["from_ai"] = True
    st.session_state["verified_chk"] = False
    st.session_state["loaded_provenance"] = None
    st.session_state.pop("price_quote", None)  # price belongs to the previous company


def reset_analysis():
    for k in list(st.session_state.keys()):
        if k.startswith("fin_") or k in (
            "extraction_meta", "from_ai", "verified_chk", "loaded_provenance",
            "source_doc", "last_result", "pur_shares", "pur_dps", "price_quote",
        ):
            st.session_state.pop(k, None)
    # Bump the uploader key so the file_uploader widget is reset too.
    st.session_state["uploader_ver"] = st.session_state.get("uploader_ver", 0) + 1


@st.cache_data(show_spinner=False)
def load_index(path_str: str) -> pd.DataFrame:
    return load_data(path_str)


@st.cache_data(show_spinner=False)
def index_ticker_pairs() -> list[tuple[str, str]]:
    """(ticker, company_name) pairs from the Meezan index — used to resolve a
    document's stated symbol to the canonical PSX ticker for the price lookup."""
    if not INDEX_CSV.exists():
        return []
    df = load_index(str(INDEX_CSV))
    return [(str(t).strip(), str(n).strip()) for t, n in zip(df["ticker"], df["company_name"]) if str(t).strip()]


@st.cache_data(show_spinner=True, ttl=900)
def fetch_psx_price(symbol: str) -> dict | None:
    """Cached live-price lookup (15-min TTL) so repeated screens don't re-hit Yahoo."""
    return market_data.fetch_price(symbol)


def build_record(raw, ratios, evaluation, meta, *, extraction=None, provider="", model="",
                 verified=False, purification=None, data_source="manual", parent_run_id=None) -> dict:
    extraction = extraction or {}
    return {
        **dataclasses.asdict(raw),
        "status": evaluation.status,
        "status_label": evaluation.status_label,
        "ratios": {k: ratios.get(k) for k in ["debt_ratio", "investment_ratio", "income_ratio", "illiquid_assets_ratio", "net_liquid_assets_ratio", "share_price"]},
        "failure_reasons": list(evaluation.failure_reasons),
        "notes": evaluation.notes,
        "period": meta.get("period", ""),
        "reporting_period_months": meta.get("reporting_period_months"),
        "currency_unit": meta.get("currency_unit", ""),
        "market_price_as_of": meta.get("market_price_as_of", ""),
        "market_price_source": meta.get("market_price_source", ""),
        "data_source": data_source,
        "ai_provider": provider,
        "ai_model": model,
        "extraction_notes": extraction.get("extraction_notes", ""),
        "evidence": extraction.get("evidence") or [],
        "debt_lines": extraction.get("debt_lines") or [],
        "debt_method": extraction.get("debt_method", ""),
        "share_count_source": extraction.get("share_count_source", ""),
        "share_scale_mismatch": bool(extraction.get("share_scale_mismatch")),
        "extraction_cost_usd": extraction.get("total_cost_usd", extraction.get("cost_usd")),
        "extraction_mode": extraction.get("mode", ""),
        "verified": bool(verified),
        "draft": (data_source == "ai" and not verified),
        "parent_run_id": parent_run_id,
        "purification": purification or None,
        "app_version": APP_VERSION,
        "rule_version": RULE_VERSION,
    }


def track_cost(provider, amount):
    """Accumulate an extraction's cost into session totals (and Firebase if available)."""
    if not amount:
        return
    key = "cost_total_bedrock" if provider == ai_extract.PROVIDER_BEDROCK else "cost_total_anthropic"
    st.session_state[key] = st.session_state.get(key, 0.0) + amount
    if fb_cred:
        storage.bump_cost(fb_cred, provider, amount)


# Apply a pending "Load from history" request before any input widget renders.
if "pending_load" in st.session_state:
    rec = st.session_state.pop("pending_load")
    st.session_state["fin_company_name"] = rec.get("company_name", "") or ""
    st.session_state["fin_ticker"] = rec.get("ticker", "") or ""
    st.session_state["fin_business"] = BUSINESS_YES if rec.get("business_compliant", True) else BUSINESS_NO
    st.session_state["fin_business_activity"] = rec.get("business_activity", "") or ""
    for field in ["total_assets", "interest_bearing_debt", "noncompliant_investments", "noncompliant_income",
                  "total_revenue", "illiquid_assets", "total_liabilities", "number_of_shares", "market_price_per_share"]:
        st.session_state[f"fin_{field}"] = rec.get(field)
    st.session_state["fin_period"] = rec.get("period", "") or ""
    st.session_state["fin_currency_unit"] = rec.get("currency_unit", "") or ""
    st.session_state["fin_period_months"] = rec.get("reporting_period_months")
    st.session_state["source_doc"] = None
    st.session_state["price_quote"] = (
        {"symbol": (rec.get("ticker") or "").upper(), "price": rec.get("market_price_per_share"),
         "as_of": rec.get("market_price_as_of", ""), "source": rec.get("market_price_source", "") or "saved run"}
        if rec.get("market_price_as_of") else None
    )
    st.session_state["extraction_meta"] = {
        "period": rec.get("period", ""), "currency_unit": rec.get("currency_unit", ""),
        "reporting_period_months": rec.get("reporting_period_months"),
        "evidence": rec.get("evidence") or [], "extraction_notes": rec.get("extraction_notes", ""),
        "debt_lines": rec.get("debt_lines") or [], "debt_method": rec.get("debt_method", ""),
        "share_count_source": rec.get("share_count_source", ""), "share_scale_mismatch": rec.get("share_scale_mismatch", False),
        "model": rec.get("ai_model", ""), "usage": None, "cost_usd": None,
    }
    st.session_state["from_ai"] = False
    st.session_state["verified_chk"] = bool(rec.get("verified", False))
    st.session_state["pur_shares"] = 0.0
    st.session_state["pur_dps"] = 0.0
    st.session_state["loaded_provenance"] = {
        "data_source": rec.get("data_source"), "ai_provider": rec.get("ai_provider", ""),
        "ai_model": rec.get("ai_model", ""), "verified": bool(rec.get("verified", False)),
        "evidence": rec.get("evidence") or [], "parent_id": rec.get("id"),
        "legacy": bool(rec.get("legacy")) or rec.get("data_source") is None,
        # carry the parent's source reference so a derived revision keeps it
        "source_path": rec.get("source_path"), "source_filename": rec.get("source_filename", ""),
        "source_sha256": rec.get("source_sha256", ""),
    }


def analysis_origin():
    """Return (origin, prov). origin ∈ {none, manual, ai, loaded}; prov['verified'] is True/False/None."""
    lp = st.session_state.get("loaded_provenance")
    if st.session_state.get("from_ai"):
        return "ai", {"data_source": "ai", "model": (st.session_state.get("extraction_meta") or {}).get("model", ""),
                      "verified": bool(st.session_state.get("verified_chk")), "legacy": False}
    if lp:
        if lp.get("legacy"):
            return "loaded", {"data_source": "legacy", "model": "", "verified": None, "legacy": True}
        if lp.get("data_source") == "ai":
            # verification tracks the user's current re-confirmation, not just the stored value
            return "loaded", {"data_source": "ai", "model": lp.get("ai_model", ""),
                              "verified": bool(st.session_state.get("verified_chk")), "legacy": False}
        return "loaded", {"data_source": lp.get("data_source") or "manual", "model": "", "verified": None, "legacy": False}
    if st.session_state.get("fin_company_name") or any(st.session_state.get(k) is not None for k, *_ in FIN_FIELDS):
        return "manual", {"data_source": "manual", "model": "", "verified": None, "legacy": False}
    return "none", {"data_source": "none", "model": "", "verified": None, "legacy": False}


# ===========================================================================
# ANALYZE
# ===========================================================================
if nav == "Analyze":
    origin, prov = analysis_origin()
    is_ai = origin == "ai"
    has_inputs = origin != "none"
    has_price = st.session_state.get("fin_market_price_per_share") not in (None, 0, 0.0)
    screened = "last_result" in st.session_state and st.session_state["last_result"][0] == session_raw()

    if st.session_state.pop("loaded_msg", False):
        st.success(st.session_state.pop("loaded_company_msg", "Run loaded into Analyze."))

    def chip(label, cls):
        return f'<span class="ss-chip {cls}">{label}</span>'

    src = st.session_state.get("source_doc")
    lp = st.session_state.get("loaded_provenance") or {}
    if origin == "none":
        source_chip = chip("Input not started", "off")
    elif src:
        source_chip = chip(f"Source: {src['name']}", "on")
    elif origin == "loaded" and lp.get("source_path"):
        source_chip = chip("Source: inherited", "on")
    else:
        source_chip = chip("Source: manual", "on")
    origin_label = {"ai": "AI extraction", "manual": "Manual entry", "none": "—", "loaded": f"Loaded · {prov['data_source']}"}[origin]
    origin_chip = chip(origin_label, "off" if origin == "none" else "on")
    v = prov["verified"]
    if v is True:
        ver_chip = chip("Verified", "on")
    elif v is False:
        ver_chip = chip("Unverified (draft)", "warn")
    elif prov.get("legacy"):
        ver_chip = chip("Verification unknown", "warn")
    elif origin == "manual":
        ver_chip = chip("Verification n/a", "off")
    else:
        ver_chip = chip("Verification —", "off")
    price_chip = chip("Price set" if has_price else "Price missing", "on" if has_price else ("warn" if has_inputs else "off"))
    screened_chip = chip("Screened" if screened else "Not screened", "on" if screened else "off")

    sc1, sc2 = st.columns([5, 1])
    sc1.markdown(source_chip + origin_chip + ver_chip + price_chip + screened_chip, unsafe_allow_html=True)
    if sc2.button("New analysis", use_container_width=True):
        reset_analysis()
        st.rerun()

    # ---- Step 1: Source & inputs -----------------------------------------
    with st.container(border=True):
        st.markdown('<div class="ss-step">Step 1 · Source &amp; inputs</div>', unsafe_allow_html=True)
        with st.expander("⚡ AI-assisted: upload a financial statement (PDF or image)", expanded=False):
            if not key_present:
                st.info("Add a Claude API key in **⚙ Settings** to enable AI extraction, or fill the form manually.")
            uploaded = st.file_uploader("Financial statement", type=["pdf", "png", "jpg", "jpeg", "webp"],
                                        key=f"uploader_{st.session_state.get('uploader_ver', 0)}",
                                        help="Claude reads the statements and pre-fills the form for you to verify.")
            if st.button("Extract with Claude", disabled=not (key_present and uploaded)):
                with st.spinner("Reading locally (text/OCR) then extracting with Claude…"):
                    try:
                        src_bytes = uploaded.getvalue()
                        smart = ai_cfg.get("smart", True)
                        if ai_cfg["provider"] == ai_extract.PROVIDER_BEDROCK:
                            bl = ai_extract.bedrock_ladder(st.session_state.get("bedrock_models") or []) if smart else []
                            ex_model, ex_escalate, ex_ladder = (None, True, bl) if bl else (ai_cfg.get("model"), False, None)
                        else:
                            ex_model, ex_escalate, ex_ladder = (None if smart else ai_cfg.get("model")), smart, None
                        raw, meta = ai_extract.smart_extract(
                            src_bytes, uploaded.name, provider=ai_cfg["provider"],
                            api_key=ai_cfg.get("api_key"), aws_access_key=ai_cfg.get("aws_access_key"),
                            aws_secret_key=ai_cfg.get("aws_secret_key"), aws_region=ai_cfg.get("aws_region"),
                            model=ex_model, escalate=ex_escalate, ladder=ex_ladder,
                        )
                        track_cost(ai_cfg["provider"], meta.get("total_cost_usd") or 0.0)
                        apply_extraction(raw, meta)
                        st.session_state["source_doc"] = {"bytes": src_bytes, "name": uploaded.name}
                        st.success("Extracted. Review in Step 2 before screening.")
                        st.rerun()
                    except ai_extract.ExtractionError as exc:
                        st.error(str(exc))

        c1, c2 = st.columns(2)
        c1.text_input("Company name", key="fin_company_name")
        c2.text_input("Ticker", key="fin_ticker")
        st.radio("Core business activity", [BUSINESS_YES, BUSINESS_NO], key="fin_business", horizontal=True,
                 help="Select 'No' for conventional banks, insurers, alcohol, tobacco, gambling, etc. — screened out by sector.")
        p1, p2, p3 = st.columns(3)
        p1.text_input("Business activity (optional)", key="fin_business_activity")
        p2.text_input("Reporting period", key="fin_period", placeholder="e.g. Year ended 30 Jun 2025")
        p3.text_input("Currency unit", key="fin_currency_unit", placeholder="e.g. PKR '000")
        st.caption("Enter all amounts in one consistent unit, and the share count in that same scale. Leave a field blank if unknown.")

        # Live PSX market-price lookup (#1) — fills the manual price field; editable.
        if market_data.available():
            tkr = (st.session_state.get("fin_ticker") or "").strip()
            fp1, fp2 = st.columns([1, 3])
            if fp1.button("↻ Fetch live price (PSX)", disabled=not tkr, use_container_width=True,
                          help="Looks up the latest PSX close from Yahoo Finance and fills 'Market price per share'. You can override it."):
                sym = market_data.resolve_ticker(tkr, st.session_state.get("fin_company_name", ""), index_ticker_pairs())
                quote = fetch_psx_price(sym)
                st.session_state["price_quote"] = quote or {"error": sym}
                if quote:
                    st.session_state["fin_market_price_per_share"] = quote["price"]
                st.rerun()
            q = st.session_state.get("price_quote")
            if q and not q.get("error"):
                fp2.success(f"💹 **{q['symbol']}** = PKR {q['price']:,.2f} · as of **{q['as_of']}** ({q['source']}). "
                            "Shown below — update it if you have a fresher price.")
            elif q and q.get("error"):
                fp2.warning(f"No PSX price found for **{q['error']}** on Yahoo Finance. Enter the market price manually below.")
            elif not tkr:
                fp2.caption("Enter a ticker to enable the live price lookup.")

        cols = st.columns(3)
        for i, (key, label, help_text) in enumerate(FIN_FIELDS):
            with cols[i % 3]:
                num_field(key, label, help_text)

    # ---- Step 2: Verify ---------------------------------------------------
    meta_ex = st.session_state.get("extraction_meta")
    evidence = (meta_ex or {}).get("evidence") or []

    def _evidence_table():
        ev_df = pd.DataFrame(evidence)
        show = [c for c in ["field", "value", "source_page", "source_label", "confidence", "rationale"] if c in ev_df.columns]
        st.dataframe(ev_df[show].rename(columns={"field": "Field", "value": "Value", "source_page": "Page", "source_label": "Source label", "confidence": "Confidence", "rationale": "Rationale"}),
                     width="stretch", hide_index=True)

    with st.container(border=True):
        st.markdown('<div class="ss-step">Step 2 · Verify</div>', unsafe_allow_html=True)
        if origin == "manual":
            st.caption("Manual entry — no AI extraction to verify. Double-check the figures, then screen.")
        elif origin == "loaded" and prov.get("legacy"):
            st.warning("Loaded **legacy** run — original provenance, model, and evidence are unavailable. Verification is unknown.")
        elif origin == "loaded" and prov["data_source"] == "ai":
            st.caption(f"Loaded AI run ({prov['model'] or 'model unknown'}). Re-confirm to save it as verified.")
            if evidence:
                _evidence_table()
            st.checkbox("Re-confirm these figures against the source statement.", key="verified_chk")
        elif origin == "loaded":
            st.caption("Loaded manual run — no AI extraction to verify.")
        elif origin == "ai":
            attempts = (meta_ex or {}).get("attempts") or []
            if attempts:
                tiers = " → ".join(a["model"].split(".")[-1] for a in attempts)
                esc = " (escalated)" if (meta_ex or {}).get("escalated") else ""
                in_tok = sum(a.get("input_tokens") or 0 for a in attempts)
                out_tok = sum(a.get("output_tokens") or 0 for a in attempts)
                total = (meta_ex or {}).get("total_cost_usd")
                cost_str = f"≈ ${total:.4f}" if total else "cost n/a (Bedrock varies)"
                st.caption(f"Extraction — read as **{(meta_ex or {}).get('mode', '?')}**, model **{tiers}**{esc} · {in_tok:,} in + {out_tok:,} out tokens · {cost_str}.")
            # Period guard (#3): income figures are a flow over this many months.
            months = (meta_ex or {}).get("reporting_period_months")
            plabel = ai_extract.period_label(months)
            if plabel:
                extra = " — the income ratio uses revenue and non-compliant income from this same period (a valid same-period ratio)." if ai_extract.is_partial_period(months) else ""
                st.caption(f"📅 Reporting period: **{plabel}**.{extra}")
            # Debt audit (#2): show exactly which liability lines code summed as debt.
            dlines = (meta_ex or {}).get("debt_lines") or []
            if dlines:
                with st.expander(f"Interest-bearing debt — {len(dlines)} balance-sheet line(s) summed in code"):
                    ddf = pd.DataFrame([{"Line item": d.get("label"), "Amount": d.get("amount")} for d in dlines])
                    st.dataframe(ddf, width="stretch", hide_index=True)
                    st.caption("Computed deterministically from the transcribed balance sheet — code classifies which liabilities are debt, not the model.")
            if (meta_ex or {}).get("debt_low_confidence"):
                st.warning("⚠️ The transcribed liability lines don't add up to the printed subtotal, so the **interest-bearing debt figure may be off** — double-check it against the statement before relying on the verdict.")
            if (meta_ex or {}).get("share_scale_mismatch"):
                st.warning("⚠️ The share count looked like it was read in a different unit than the balance sheet, so it was recomputed from paid-up capital ÷ face value to keep the net-liquid-assets-per-share screen consistent. Confirm **Number of shares** against the statement.")
            if (meta_ex or {}).get("extraction_notes"):
                st.caption("AI notes: " + meta_ex["extraction_notes"])
            if evidence:
                _evidence_table()
            if not has_price:
                st.info("Market price isn't in financial statements — enter **Market price per share** in Step 1 to complete the net-liquid-assets screen.")
            st.checkbox("I've reviewed these AI-extracted figures against the source statement.", key="verified_chk")
            if not st.session_state.get("verified_chk"):
                st.caption("⚠️ Unverified — AI can misclassify Shariah-specific line items. The verdict is marked draft until confirmed.")
        else:
            st.caption("Enter or extract figures in Step 1 to begin.")

    # ---- Step 3: Screen & export -----------------------------------------
    with st.container(border=True):
        st.markdown('<div class="ss-step">Step 3 · Screen &amp; export</div>', unsafe_allow_html=True)
        if st.button("Run Shariah screening", type="primary"):
            raw = session_raw()
            ratios = compute_ratios(raw)
            st.session_state["last_result"] = (raw, ratios, screen_metrics(ratios))
            st.rerun()

        stored = st.session_state.get("last_result")
        fresh = bool(stored) and stored[0] == session_raw()
        if stored and not fresh:
            st.info("Inputs changed since the last run — press **Run Shariah screening** to refresh.")

        if fresh:
            raw, ratios, evaluation = stored
            color = STATUS_COLORS.get(evaluation.status, "#5b6472")
            if prov["verified"] is False:
                st.warning("⚠️ This verdict uses **unverified** AI-extracted figures (draft). Confirm them in Step 2 before relying on it.")
            elif prov.get("legacy"):
                st.caption("Loaded legacy run — original provenance and verification are unknown.")
            st.markdown(f'<span class="ss-badge" style="background:{color}">{evaluation.status_label}</span>', unsafe_allow_html=True)
            st.write("")

            if evaluation.metric_results:
                cards = st.columns(3)
                for i, m in enumerate(evaluation.metric_results):
                    value_str = format_number(m.value) if m.key == "net_liquid_assets_ratio" else format_percent(m.value)
                    with cards[i % 3]:
                        st.markdown(ratio_card(m.label, value_str, m.threshold, m.passed), unsafe_allow_html=True)
                        st.write("")

            if evaluation.failure_reasons:
                st.markdown("**Reason for status**")
                for reason in evaluation.failure_reasons:
                    st.markdown(f"- {reason}")
            else:
                st.success("All screening thresholds are met.")

            income_ratio = ratios.get("income_ratio")
            purification_ctx = None
            with st.expander("Dividend purification calculator"):
                if income_ratio is None:
                    st.caption("Enter non-compliant income and total revenue to enable purification.")
                else:
                    pc1, pc2 = st.columns(2)
                    shares_owned = pc1.number_input("Shares you own", min_value=0.0, step=1.0, key="pur_shares")
                    dps = pc2.number_input("Dividend per share received", min_value=0.0, step=0.5, key="pur_dps")
                    result = calculate_purification(shares_owned, dps, income_ratio)
                    if result:
                        total_dividend, purify = result
                        m1, m2, m3 = st.columns(3)
                        m1.metric("Income ratio used", format_percent(income_ratio))
                        m2.metric("Total dividend", f"{total_dividend:,.2f}")
                        m3.metric("Amount to purify", f"{purify:,.2f}")
                        purification_ctx = {"shares": f"{shares_owned:,.2f}", "dps": f"{dps:,.2f}",
                                            "income_ratio": format_percent(income_ratio),
                                            "total_dividend": f"{total_dividend:,.2f}", "purification_amount": f"{purify:,.2f}"}

            pq = st.session_state.get("price_quote") or {}
            meta = {"period": st.session_state.get("fin_period", ""), "currency_unit": st.session_state.get("fin_currency_unit", ""),
                    "reporting_period_months": st.session_state.get("fin_period_months"),
                    "market_price_as_of": pq.get("as_of", ""), "market_price_source": pq.get("source", "") if not pq.get("error") else "",
                    "source": "Analyzed from entered financials"}
            audit = {
                "data_source": {"ai": "AI extraction", "manual": "manual entry", "none": "—", "loaded": f"loaded run ({prov['data_source']})"}[origin],
                "model": prov["model"],
                "verified": prov["verified"],  # True / False / None (unknown)
                "rule_version": RULE_VERSION, "app_version": APP_VERSION, "evidence": (meta_ex or {}).get("evidence") or [],
            }
            pdf_bytes = None
            try:
                pdf_bytes = build_pdf_report(evaluation=evaluation, company_name=raw.company_name, ticker=raw.ticker,
                                            meta=meta, purification=purification_ctx,
                                            generated_on=datetime.now().strftime("%Y-%m-%d %H:%M"), audit=audit)
                stub = (raw.ticker or raw.company_name or "company").strip().replace(" ", "_") or "company"
                rc1, rc2 = st.columns(2)
                rc1.download_button("⬇ Download PDF tear-sheet", data=pdf_bytes, file_name=f"sharia_scope_{stub}.pdf", mime="application/pdf")
            except Exception as exc:
                st.warning(f"Could not build the PDF report: {exc}")

            if fb_cred:
                lp = st.session_state.get("loaded_provenance") or {}
                if origin == "ai":
                    save_prov = {"data_source": "ai", "provider": ai_cfg.get("provider", ""),
                                 "model": (meta_ex or {}).get("final_model") or ai_cfg.get("model", ""),
                                 "verified": st.session_state.get("verified_chk", False), "extraction": meta_ex or {}, "parent": None, "inherit": None}
                elif origin == "loaded":
                    save_prov = {
                        "data_source": lp.get("data_source") or ("legacy" if lp.get("legacy") else "manual"),
                        "provider": lp.get("ai_provider", ""), "model": lp.get("ai_model", ""),
                        "verified": (st.session_state.get("verified_chk", False) if lp.get("data_source") == "ai" else bool(lp.get("verified"))),
                        "extraction": {"evidence": lp.get("evidence") or []}, "parent": lp.get("parent_id"),
                        "inherit": ({"source_path": lp.get("source_path"), "source_filename": lp.get("source_filename", ""), "source_sha256": lp.get("source_sha256", "")}
                                    if lp.get("source_path") and not st.session_state.get("source_doc") else None),
                    }
                else:
                    save_prov = {"data_source": "manual", "provider": "", "model": "", "verified": True, "extraction": {}, "parent": None, "inherit": None}

                if not storage_ok:
                    st.caption("Cloud Storage off — Save will store **metadata only**. Files attach once Storage is enabled.")
                if save_prov["data_source"] == "ai" and not save_prov["verified"]:
                    st.caption("⚠️ **Draft** — saved unverified until you confirm in Step 2.")
                if save_prov["parent"]:
                    extra = " — source inherited from parent" if save_prov["inherit"] else ""
                    st.caption(f"Saves as a **derived revision** of `{save_prov['parent'][:8]}…`{extra}.")
                if st.button("💾 Save run to history"):
                    with st.spinner("Archiving run to Firebase…"):
                        try:
                            res = storage.save_run(fb_cred, record=build_record(
                                raw, ratios, evaluation, meta, extraction=save_prov["extraction"], provider=save_prov["provider"],
                                model=save_prov["model"], verified=save_prov["verified"], purification=purification_ctx,
                                data_source=save_prov["data_source"], parent_run_id=save_prov["parent"]),
                                source=st.session_state.get("source_doc"), report_bytes=pdf_bytes, bucket_name=fb_bucket,
                                inherit_source=save_prov["inherit"])
                            st.success("Saved with documents." if res["files_archived"] else "Saved (metadata).")
                        except storage.StorageError as exc:
                            st.error(str(exc))

    st.caption("Educational prototype — not investment advice, a religious ruling, or an official PSX/KMI screening service.")


# ===========================================================================
# SAVED
# ===========================================================================
if nav == "Saved":
    st.markdown("##### Saved runs")
    if not storage.available():
        st.info("`firebase-admin` is not installed — saving is unavailable.")
    elif not fb_cred:
        st.info("Add a Firebase service-account key in **⚙ Settings** to save and reload runs.")
    else:
        try:
            runs = storage.list_runs(fb_cred, limit=200)
        except storage.StorageError as exc:
            runs = []
            st.error(str(exc))
        if not runs:
            st.caption("No saved runs yet — screen a company and press **Save**.")
        else:
            def _legacy(r):
                return bool(r.get("legacy")) or r.get("data_source") is None

            fc1, fc2 = st.columns([3, 2])
            query = fc1.text_input("Search company or ticker", placeholder="e.g. Lucky, LUCK").strip().lower()
            verdict_filter = fc2.selectbox("Verdict", ["All", "Compliant", "Non-Compliant", "Review Required"])

            def _match(r):
                if query and query not in (str(r.get("company_name", "")) + " " + str(r.get("ticker", ""))).lower():
                    return False
                if verdict_filter != "All" and (r.get("status_label") or "") != verdict_filter and not (verdict_filter == "Non-Compliant" and "Non-Compliant" in (r.get("status_label") or "")):
                    return False
                return True

            shown = [r for r in runs if _match(r)]
            if not shown:
                st.caption("No runs match the filter.")
            else:
                table = pd.DataFrame([{
                    "Ticker": r.get("ticker") or "—", "Company": r.get("company_name") or "—",
                    "Verdict": r.get("status_label") or "—",
                    "Source": "legacy" if _legacy(r) else ("AI" if r.get("data_source") == "ai" else "manual"),
                    "Verified": "—" if _legacy(r) else ("✓" if r.get("verified") else "draft"),
                    "Files": ", ".join(f for f in ["source" if r.get("source_path") else "", "report" if r.get("report_path") else ""] if f) or "—",
                    "Saved": r["created_at"].strftime("%Y-%m-%d %H:%M") if hasattr(r.get("created_at"), "strftime") else "—",
                } for r in shown])
                event = st.dataframe(table, width="stretch", hide_index=True, on_select="rerun", selection_mode="single-row", key="runs_table")
                sel = event.get("selection", {}).get("rows", []) if isinstance(event, dict) else getattr(getattr(event, "selection", None), "rows", [])
                # Selectbox opener — reliable on mobile, where the table's row checkbox is tiny.
                pick = st.selectbox("Open a run", [None] + list(range(len(shown))),
                                    format_func=lambda i: "Select a run…" if i is None else f"{shown[i].get('ticker') or '—'} · {shown[i].get('company_name') or '?'} · {shown[i].get('status_label') or ''}")
                sel_idx = pick if pick is not None else (sel[0] if sel else None)

                if sel_idx is not None:
                    r = shown[sel_idx]
                    color = STATUS_COLORS.get(r.get("status", ""), "#5b6472")
                    st.markdown(f'<span class="ss-badge" style="background:{color}">{r.get("status_label") or "—"}</span>', unsafe_allow_html=True)
                    st.write("")
                    rt = r.get("ratios") or {}
                    cols = st.columns(5)
                    for col, (lbl, val) in zip(cols, [("Debt", format_percent(rt.get("debt_ratio"))), ("Investments", format_percent(rt.get("investment_ratio"))),
                                                      ("Income", format_percent(rt.get("income_ratio"))), ("Illiquid", format_percent(rt.get("illiquid_assets_ratio"))),
                                                      ("NLA/share", format_number(rt.get("net_liquid_assets_ratio")))]):
                        col.metric(lbl, val)
                    ctx = " · ".join(b for b in [r.get("period"), r.get("currency_unit")] if b)
                    if _legacy(r):
                        prov_line = "Legacy run — provenance unavailable"
                    else:
                        src = f"AI ({r.get('ai_model')})" if r.get("data_source") == "ai" else "manual entry"
                        prov_line = " · ".join(x for x in [src, "verified" if r.get("verified") else "draft (unverified)",
                                                           f"rules {r.get('rule_version', '')}".strip(), f"app {r.get('app_version', '')}".strip()] if x.strip())
                        if r.get("parent_run_id"):
                            prov_line += f" · revision of {r['parent_run_id'][:8]}…"
                    st.caption(" · ".join(b for b in [ctx, prov_line] if b))
                    if r.get("failure_reasons"):
                        st.markdown("**Reasons:** " + "; ".join(r["failure_reasons"]))

                    b1, b2, b3, b4 = st.columns(4)
                    if b1.button("↩ Load into Analyze", key=f"load_{r['id']}"):
                        st.session_state["pending_load"] = r
                        st.session_state["nav"] = "Analyze"  # take the user to Analyze
                        st.session_state["loaded_msg"] = True
                        st.session_state["loaded_company_msg"] = f"Loaded {r.get('company_name') or r.get('ticker')} into Analyze."
                        st.rerun()
                    if r.get("source_path"):
                        try:
                            b2.download_button("⬇ Source", storage.download_blob(fb_cred, r["source_path"], bucket_name=fb_bucket),
                                               file_name=r.get("source_filename") or "source.pdf", key=f"src_{r['id']}")
                        except storage.StorageError as exc:
                            b2.caption(f"Source: {exc}")
                    else:
                        b2.caption("No source doc")
                    if r.get("report_path"):
                        try:
                            b3.download_button("⬇ Tear-sheet", storage.download_blob(fb_cred, r["report_path"], bucket_name=fb_bucket),
                                               file_name=f"{r.get('ticker') or 'run'}_tearsheet.pdf", mime="application/pdf", key=f"rep_{r['id']}")
                        except storage.StorageError as exc:
                            b3.caption(f"Report: {exc}")
                    else:
                        b3.caption("No tear-sheet")

                    if st.session_state.get("confirm_delete") == r["id"]:
                        nf = sum(1 for k in ("source_path", "report_path") if r.get(k))
                        st.warning(f"Delete **{r.get('ticker') or r.get('company_name')}** and its {nf} stored file(s)? This cannot be undone.")
                        d1, d2, _ = st.columns([1, 1, 4])
                        if d1.button("Confirm delete", key=f"cdel_{r['id']}"):
                            try:
                                storage.delete_run(fb_cred, r["id"], bucket_name=fb_bucket)
                                st.session_state.pop("confirm_delete", None)
                                st.rerun()
                            except storage.StorageError as exc:
                                st.error(str(exc))
                        if d2.button("Cancel", key=f"xdel_{r['id']}"):
                            st.session_state.pop("confirm_delete", None)
                            st.rerun()
                    elif b4.button("🗑 Delete run", key=f"del_{r['id']}"):
                        st.session_state["confirm_delete"] = r["id"]
                        st.rerun()


# ===========================================================================
# VALIDATION
# ===========================================================================
if nav == "Validation":
    st.markdown(
        "This tests the analyzer's hypothesis: run it across Meezan's **published** PSX/KMI All-Share "
        "Islamic Index and compare each computed verdict to Meezan's official ruling. The sheet is the "
        "oracle, not the app's data source."
    )
    source_choice = st.radio("Ground-truth sheet", ["Bundled KMI All-Share Index (Dec 2025)", "Upload my own labelled sheet"], horizontal=True)
    df = None
    if source_choice.startswith("Bundled"):
        if INDEX_CSV.exists():
            df = load_index(str(INDEX_CSV))
        else:
            st.error("Bundled index file is missing.")
    else:
        up = st.file_uploader("Labelled CSV/XLSX (must include final_shariah_status)", type=["csv", "xlsx", "xls"], key="val_upload")
        if up:
            df = load_data(up)

    if df is not None and len(df):
        result = backtest(df)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Companies", result["total"])
        c2.metric("Agreement with Meezan", f"{result['accuracy'] * 100:.1f}%")
        c3.metric("Disagreements", result["disagree"])
        c4.metric("Indeterminate", result["indeterminate"], help="Rows the analyzer can't resolve from available ratios.")
        st.caption(
            "Every disagreement shown is a company where Meezan granted a documented manual exception "
            "(service-sector relaxation, circular-debt income exception, etc.) — see the notes column. "
            "The sheet covers all 535 listed companies: 503 screened, plus 32 with no recent financials."
        )
        if result["mismatches"]:
            st.markdown("##### Where the analyzer differs from Meezan")
            mism = pd.DataFrame(result["mismatches"]).rename(columns={
                "ticker": "Ticker", "company_name": "Company", "analyzer": "Analyzer",
                "official": "Meezan", "reasons": "Breached rule(s)", "notes": "Meezan note / exception"})
            st.dataframe(mism, width="stretch", hide_index=True)
    elif df is not None:
        st.info("No rows to screen.")


# ===========================================================================
# METHODOLOGY
# ===========================================================================
if nav == "Methodology":
    st.markdown("##### Screening thresholds (PSX / KMI)")
    st.table(pd.DataFrame([
        ["Debt ratio", "Interest-bearing debt ÷ total assets", "< 37%"],
        ["Non-compliant investments", "Non-Shariah investments ÷ total assets", "< 33%"],
        ["Non-compliant income", "Non-Shariah income ÷ total revenue", "< 5%"],
        ["Illiquid assets", "Illiquid assets ÷ total assets", "≥ 25%"],
        ["Net liquid assets / share", "(Assets − illiquid − liabilities) ÷ shares", "< market price"],
    ], columns=["Screen", "Formula", "Pass condition"]))
    st.markdown(
        """
##### How the verdict is reached
1. **Business screen.** A core business that is non-compliant by nature (conventional banking,
   insurance, alcohol, tobacco, gambling, etc.) is screened out immediately — *Non-Compliant by Nature*.
2. **Financial screens.** Otherwise the five ratios above are computed from the raw figures and tested.
   Any single breach makes the company **Non-Compliant**; missing inputs produce **Review Required**.

##### Dividend purification
`purification = shares_owned × dividend_per_share × (non-compliant income ratio ÷ 100)`. Meezan marks
these rates *provisional* until final adjustment, so treat the figure as indicative.

##### Sources
- **PSX KMI (Karachi-Meezan) Shariah screening criteria** — primary rule source and validation oracle.
- **S&P Dow Jones Shariah indices methodology** — supporting context for sector screening and purification.

##### Disclaimer
Educational prototype. Not investment advice, a religious ruling, or an official PSX/KMI screening
service. Always verify figures against audited statements and consult a qualified Shariah advisor.
        """
    )
