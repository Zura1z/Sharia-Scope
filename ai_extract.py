"""AI-assisted extraction of Shariah-screening inputs from financial statements.

Uses the Claude API to read a company's annual/quarterly report (PDF or image)
and pull out the raw line items the screener needs. The result pre-fills the
manual form for the user to verify — the formula engine itself stays offline and
deterministic, so the app works with or without a key.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from allshariah_core import RawFinancials

DEFAULT_MODEL = "claude-opus-4-8"
# Amazon Bedrock uses provider-prefixed model IDs. Cross-region inference may
# require a regional prefix (e.g. "us.anthropic.claude-opus-4-8") depending on
# the account — the sidebar exposes this as an editable field.
DEFAULT_BEDROCK_MODEL = "anthropic.claude-opus-4-8"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_BEDROCK = "bedrock"

# Tool schema = the structured shape we force Claude to return. Numeric fields are
# nullable so Claude reports "not found" rather than inventing a number.
_NUM = {"type": ["number", "null"]}
EXTRACTION_TOOL = {
    "name": "report_financials",
    "description": "Report the Shariah-screening line items extracted from the financial statements.",
    "input_schema": {
        "type": "object",
        "properties": {
            "company_name": {"type": ["string", "null"]},
            "ticker": {"type": ["string", "null"]},
            "business_activity": {
                "type": ["string", "null"],
                "description": "One-line description of the company's core business.",
            },
            "business_compliant": {
                "type": "boolean",
                "description": "False if the core business is haram by nature "
                "(conventional banking, conventional insurance, alcohol, tobacco, "
                "gambling, pork, adult media, interest-based finance); otherwise true.",
            },
            "total_assets": _NUM,
            "interest_bearing_debt": dict(_NUM, description="Total interest-bearing debt if a single figure; otherwise leave and fill the components below."),
            # Named components — transcribe these exact lines; code sums them so the
            # model never has to judge 'which liability is debt' (it conflates deferred tax).
            "long_term_borrowings": dict(_NUM, description="Non-current 'long term borrowings/financing' line ONLY. NOT deferred tax, advances, payables, or provisions."),
            "short_term_borrowings": dict(_NUM, description="Current 'short term borrowings' / running finance line."),
            "current_portion_long_term_debt": dict(_NUM, description="'Current and overdue portion of non-current liabilities' / current maturity of long-term financing."),
            "noncompliant_investments": dict(_NUM, description="ONLY investments in conventional/interest-based instruments (bonds, T-bills, conventional bank deposits/funds) and shares of companies that are themselves Shariah non-compliant. EXCLUDE investments in Shariah-compliant subsidiaries/associates. If the split is unclear, report what you can and note the assumption."),
            "noncompliant_income": dict(_NUM, description="Interest income plus income from other non-Shariah sources."),
            "total_revenue": dict(_NUM, description="Total revenue/turnover plus other income, used as the income-ratio denominator."),
            "illiquid_assets": dict(_NUM, description="Total assets minus liquid assets (cash, bank, receivables, short-term marketable investments)."),
            "total_liabilities": dict(_NUM, description="ALL liabilities = non-current + current (= total assets − total equity)."),
            "total_equity": dict(_NUM, description="Share capital and reserves subtotal (capital + reserves + revaluation surplus + retained earnings)."),
            "number_of_shares": dict(_NUM, description="Number of ordinary shares outstanding."),
            "paid_up_capital": dict(_NUM, description="Issued, subscribed and paid-up share CAPITAL — a money amount in the same unit as the other figures (NOT a share count). Used to cross-check the share count's scale."),
            "share_face_value": dict(_NUM, description="Par/face value per share, from 'ordinary shares of Rs. X each' (PSX is almost always 10, occasionally 5)."),
            "market_price_per_share": dict(_NUM, description="Latest market price per share, if stated."),
            "currency_unit": {"type": ["string", "null"], "description": "e.g. 'PKR 000' or 'PKR mn'."},
            "period": {"type": ["string", "null"], "description": "Reporting period, e.g. 'Year ended 30 June 2025' or '3rd quarter ended 31 March 2026'."},
            "reporting_period_months": {
                "type": ["integer", "null"],
                "description": "How many months the INCOME statement covers: 12 for a full year, 9 for a 3rd-quarter cumulative, 6 for a half-year, 3 for a single quarter. Balance-sheet figures are point-in-time regardless.",
            },
            # Verbatim transcription of the statement of financial position. Code —
            # not the model — classifies which liabilities are interest-bearing debt,
            # so the model never has to make the judgement it keeps getting wrong.
            "balance_sheet_lines": {
                "type": "array",
                "description": "EVERY line item on the statement of financial position (balance sheet), transcribed exactly as printed with its amount and section. Transcribe ALL equity and liability lines especially — do NOT classify, summarise, or skip any. Use the most recent period's column.",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Exact line-item label as printed, e.g. 'Long term financing' or 'Deferred taxation'."},
                        "amount": _NUM,
                        "section": {
                            "type": "string",
                            "enum": ["equity", "non_current_liability", "current_liability", "non_current_asset", "current_asset"],
                            "description": "Which balance-sheet section the line sits under.",
                        },
                    },
                    "required": ["label", "amount", "section"],
                    "additionalProperties": False,
                },
            },
            "extraction_notes": {"type": ["string", "null"], "description": "Caveats, assumptions, or figures that could not be found."},
            "evidence": {
                "type": "array",
                "description": "One entry per financial figure you reported (skip company_name/ticker/business). Lets the user audit every classification.",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string", "description": "The field name, e.g. interest_bearing_debt."},
                        "value": {"type": ["number", "null"]},
                        "source_page": {"type": ["string", "null"], "description": "Page or note number in the document."},
                        "source_label": {"type": ["string", "null"], "description": "The exact line-item label(s) you summed."},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "rationale": {"type": ["string", "null"], "description": "Why these line items were included/excluded for Shariah screening."},
                    },
                    "required": ["field", "value", "source_page", "source_label", "confidence", "rationale"],
                    "additionalProperties": False,
                },
            },
        },
        "required": [
            "company_name", "ticker", "business_activity", "business_compliant",
            "total_assets", "interest_bearing_debt", "long_term_borrowings",
            "short_term_borrowings", "current_portion_long_term_debt", "noncompliant_investments",
            "noncompliant_income", "total_revenue", "illiquid_assets",
            "total_liabilities", "total_equity", "number_of_shares", "paid_up_capital",
            "share_face_value", "market_price_per_share",
            "currency_unit", "period", "reporting_period_months", "balance_sheet_lines",
            "extraction_notes", "evidence",
        ],
        "additionalProperties": False,
    },
}

# --- deterministic interest-bearing-debt classification --------------------
# The model transcribes balance-sheet lines verbatim; CODE decides which ones
# are interest-bearing debt. This removes the single biggest source of error —
# the model conflating deferred tax / revaluation surplus / payables with debt.
# EXCLUDE is checked first and wins, so "accrued mark-up on borrowings" (a
# payable) is dropped even though it contains "borrowing".
DEBT_EXCLUDE = (
    "payable", "deferred", "revaluation", "surplus", "provision", "unclaimed",
    "unpaid", "dividend", "contract liabilit", "advance", "deposit", "retention",
    "accrued", "accrual", "taxation", "tax", "gratuity", "employee benefit",
    "compensated absences", "pension", "staff retirement", "creditor", "warranty",
    "grant", "subsidy", "refund", "billing in excess", "due to",
)
DEBT_INCLUDE = (
    "borrowing", "long term financ", "long-term financ", "short term financ",
    "short-term financ", "term finance", "long term loan", "long-term loan",
    "short term loan", "short-term loan", "bank loan", "running finance",
    "running musharaka", "lease liabilit", "finance lease",
    "liabilities against assets subject to finance lease", "ijarah", "ijara",
    "diminishing musharak", "musharak", "sukuk", "bond", "redeemable capital",
    "debenture", "overdraft", "term finance certificate",
    "current portion of long term", "current portion of long-term",
    "current maturity of long term", "current maturity of long-term",
    "current portion of non current", "current portion of non-current",
    "current and overdue portion", "current portion of lease",
)


def _classify_debt_line(label: str) -> bool:
    """True if a balance-sheet liability label is interest-bearing debt."""
    low = (label or "").lower()
    if any(k in low for k in DEBT_EXCLUDE):
        return False
    return any(k in low for k in DEBT_INCLUDE)


def classify_debt_from_lines(lines: list[dict]) -> tuple[float | None, list[dict]]:
    """Sum interest-bearing debt from transcribed balance-sheet lines.

    Looks only at liability sections, applies the keyword map, and returns
    ``(total_debt, included_lines)``. Returns ``(None, [])`` when no liability
    lines were transcribed (so the caller falls back to the model's own figure).
    """
    liabilities = [
        ln for ln in (lines or [])
        if (ln.get("section") or "") in ("non_current_liability", "current_liability")
    ]
    if not liabilities:
        return None, []
    included: list[dict] = []
    for ln in liabilities:
        amount = _as_number(ln.get("amount"))
        if amount is None or amount == 0:
            continue
        if _classify_debt_line(ln.get("label") or ""):
            included.append({"label": ln.get("label") or "", "amount": amount, "section": ln.get("section")})
    total = sum(item["amount"] for item in included)
    return total, included


def _sum_section(lines: list[dict], sections: tuple[str, ...]) -> float | None:
    """Sum the amounts of all lines in the given balance-sheet sections, or None."""
    vals = [_as_number(ln.get("amount")) for ln in (lines or []) if (ln.get("section") or "") in sections]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def _section_matches_subtotal(line_sum: float | None, reported: float | None, tol: float = 0.01) -> bool | None:
    """True if a section's transcribed lines sum to its printed subtotal (within
    1%). None when either value is missing (can't check)."""
    if line_sum is None or reported is None or not reported:
        return None
    return abs(line_sum - reported) <= abs(reported) * tol


# Published per-1M-token list prices (Anthropic first-party). Bedrock prices differ
# by region, so cost is shown only for the first-party API.
MODEL_PRICES = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


TIER_PRICES = {"haiku": (1.0, 5.0), "sonnet": (3.0, 15.0), "opus": (5.0, 25.0), "fable": (10.0, 50.0)}


def estimate_cost(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    """Estimate USD cost from per-1M prices. Matches versioned/Bedrock IDs by
    base name, then by tier (regional Bedrock prices vary, so it's approximate)."""
    if input_tokens is None or output_tokens is None:
        return None
    base = (model or "").lower()
    price = next((p for k, p in MODEL_PRICES.items() if k in base), None)
    if price is None:
        price = next((p for t, p in TIER_PRICES.items() if t in base), None)
    if price is None:
        return None
    return input_tokens / 1_000_000 * price[0] + output_tokens / 1_000_000 * price[1]


def _region_prefix(region: str) -> str:
    r = (region or "").lower()
    if r.startswith("eu"):
        return "eu."
    if r.startswith("ap"):
        return "apac."
    return "us."


# Cheapest → most capable. Smart extraction starts low and escalates only if
# the cheaper model didn't recover enough of the critical figures. Bedrock IDs
# are NOT hard-coded — they vary by region/account, so the app discovers them.
MODEL_LADDER = {
    PROVIDER_ANTHROPIC: ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-8"],
}


def list_bedrock_models(access_key=None, secret_key=None, region=None, session_token=None) -> list[str]:
    """List Anthropic/Claude model + inference-profile IDs available in the account.

    Inference profiles (e.g. ``eu.anthropic.claude-…``) are what newer models /
    EU regions require for on-demand calls, so they're included first.
    """
    import boto3

    kwargs = {"region_name": region or os.environ.get("AWS_REGION") or "eu-central-1"}
    if access_key and secret_key:
        kwargs.update(aws_access_key_id=access_key, aws_secret_access_key=secret_key)
        if session_token:
            kwargs["aws_session_token"] = session_token
    client = boto3.client("bedrock", **kwargs)
    prefix = _region_prefix(kwargs["region_name"])
    ids: set[str] = set()
    # ACTIVE foundation models -> build their regional inference-profile IDs
    # (the modern models — Haiku 4.5, Sonnet 4.6, Opus 4.8 — are invoked this way).
    try:
        for m in client.list_foundation_models(byProvider="Anthropic").get("modelSummaries", []):
            mid = m.get("modelId", "")
            status = (m.get("modelLifecycle") or {}).get("status", "")
            its = m.get("inferenceTypesSupported") or []
            if "claude" not in mid.lower() or status == "LEGACY" or "claude-3-" in mid:
                continue  # skip legacy / 2024 models
            if "INFERENCE_PROFILE" in its:
                ids.add(prefix + mid)
            if "ON_DEMAND" in its:
                ids.add(mid)
    except Exception:
        pass
    # also any explicitly-listed (non-legacy) inference profiles
    try:
        for p in client.list_inference_profiles(maxResults=100).get("inferenceProfileSummaries", []):
            pid = p.get("inferenceProfileId", "")
            if "anthropic" in pid.lower() and "claude-3-" not in pid:
                ids.add(pid)
    except Exception:
        pass
    return sorted(ids)


def _model_tier(model_id: str) -> str:
    m = (model_id or "").lower()
    for t in ("haiku", "sonnet", "opus"):
        if t in m:
            return t
    return "other"


def bedrock_ladder(available: list[str]) -> list[str]:
    """Cheapest → most-capable ladder picked from available Bedrock IDs."""
    by_tier: dict[str, list[str]] = {"haiku": [], "sonnet": [], "opus": []}
    for mid in available:
        t = _model_tier(mid)
        if t in by_tier:
            by_tier[t].append(mid)
    ladder = []
    for t in ("haiku", "sonnet", "opus"):
        if by_tier[t]:
            # prefer region-prefixed inference profiles, then the latest version
            picks = sorted(by_tier[t], key=lambda x: (x.startswith(("eu.", "us.", "apac.", "global.")), x))
            ladder.append(picks[-1])
    return ladder


def test_connection(provider, model, *, api_key=None, aws_access_key=None, aws_secret_key=None,
                    aws_region=None, aws_session_token=None) -> str | None:
    """Tiny live call to verify creds+model. Returns None on success, else an error string."""
    try:
        client = build_client(provider, api_key=api_key, aws_access_key=aws_access_key, aws_secret_key=aws_secret_key,
                              aws_region=aws_region, aws_session_token=aws_session_token)
        client.messages.create(model=model, max_tokens=8, messages=[{"role": "user", "content": "ping"}])
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
TEXT_MIN_CHARS = 280   # below this, a PDF page-set is treated as scanned/image-only
OCR_MAX_PAGES = 40     # cap OCR work on very long scanned documents
TEXT_CHAR_CAP = 320_000  # ~80k tokens — bounds cost on huge documents

SYSTEM_PROMPT = (
    "You are a financial analyst preparing inputs for PSX/KMI Shariah screening. Read the "
    "attached financial statements and extract the exact line items. Use the company's "
    "STANDALONE (unconsolidated) accounts and the most recent period. All amounts in one "
    "consistent unit. Return null for a figure only if it is genuinely absent; never guess.\n\n"
    "CRITICAL CLASSIFICATION RULES — these prevent the most common mistakes:\n"
    "- INTEREST-BEARING DEBT = ONLY borrowings/loans/finances/leases that carry interest/markup, "
    "both non-current AND current (long-term borrowings + current maturity of long-term financing + "
    "overdue portion + short-term borrowings / running finance). EXCLUDE all equity: share capital, "
    "reserves, un-appropriated profit, and especially 'SURPLUS ON REVALUATION of property, plant and "
    "equipment' (that is EQUITY, never debt). Also exclude deferred tax, trade/other payables, and "
    "provisions.\n"
    "- TOTAL LIABILITIES = ALL liabilities = non-current liabilities + current liabilities (NOT just "
    "one of them). It equals Total Assets minus Total Equity.\n"
    "- TOTAL EQUITY = the 'share capital and reserves' subtotal (capital + reserves + revaluation "
    "surplus + un-appropriated profit).\n"
    "- ILLIQUID ASSETS = total assets minus LIQUID assets (cash & bank, short-term marketable "
    "investments, trade receivables). Property/plant/equipment, stores and stock-in-trade are ILLIQUID. "
    "Illiquid assets must be <= total assets and is usually dominated by PP&E. Do NOT confuse "
    "'cash & bank balances' with 'short-term investments'.\n"
    "- NON-COMPLIANT INVESTMENTS = investments in conventional/interest instruments (TFCs, bonds, "
    "T-bills, conventional funds/deposits) and shares of non-compliant companies. If a small "
    "short-term investment exists but its nature is undisclosed, report 0 and say so in the note "
    "(do not block on null) unless it is clearly material.\n"
    "- NON-COMPLIANT INCOME = interest/markup income EARNED + income from non-Shariah sources (NOT "
    "finance cost, which is interest PAID). If none is disclosed and other income is immaterial, "
    "report 0 with a note.\n\n"
    "TRANSCRIBE THE BALANCE SHEET: in 'balance_sheet_lines', copy EVERY line of the statement of "
    "financial position exactly as printed — label, amount, and section (equity / non_current_liability "
    "/ current_liability / non_current_asset / current_asset). Do not classify, merge, or omit lines; "
    "the screening code reads these to decide which liabilities are interest-bearing debt, so accurate "
    "transcription matters more than your own judgement. Put 'Surplus on revaluation' and all reserves "
    "under 'equity'; put 'Deferred tax' and 'Trade and other payables' under their liability section "
    "with their real labels (never relabel them as borrowings).\n"
    "REPORTING PERIOD: set 'reporting_period_months' to how many months the income statement covers "
    "(12 = full year, 9 = three quarters, 6 = half year, 3 = one quarter). The income ratio uses revenue "
    "and non-compliant income from THIS SAME period.\n"
    "SHARE COUNT: report 'paid_up_capital' (the issued/paid-up share capital money amount, in the SAME "
    "unit as every other figure) and 'share_face_value' (e.g. 10 from 'shares of Rs. 10 each'). "
    "'number_of_shares' must be on the same scale as the balance-sheet amounts: if you scaled the "
    "statement to millions, the share count is in millions too.\n\n"
    "RECONCILE before finishing: Total Assets MUST equal Total Equity + Total Liabilities. If your "
    "numbers don't balance, re-read the statement of financial position and fix them."
)

_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def available() -> bool:
    """True if the anthropic SDK is importable."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def resolve_api_key(api_key: str | None = None) -> str | None:
    return api_key or os.environ.get("ANTHROPIC_API_KEY")


def resolve_aws(
    access_key: str | None = None,
    secret_key: str | None = None,
    region: str | None = None,
    session_token: str | None = None,
) -> dict[str, str | None]:
    return {
        "aws_access_key": access_key or os.environ.get("AWS_ACCESS_KEY_ID"),
        "aws_secret_key": secret_key or os.environ.get("AWS_SECRET_ACCESS_KEY"),
        "aws_region": region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
        "aws_session_token": session_token or os.environ.get("AWS_SESSION_TOKEN"),
    }


def credentials_present(
    provider: str,
    *,
    api_key: str | None = None,
    aws_access_key: str | None = None,
    aws_secret_key: str | None = None,
    aws_region: str | None = None,
) -> bool:
    """True if enough credentials exist for the chosen provider."""
    if provider == PROVIDER_BEDROCK:
        aws = resolve_aws(aws_access_key, aws_secret_key, aws_region)
        if aws["aws_access_key"] and aws["aws_secret_key"]:
            return True
        # No explicit keys: fall back to the AWS default credential chain.
        return _aws_chain_has_credentials()
    return bool(resolve_api_key(api_key))


def _aws_chain_has_credentials() -> bool:
    try:
        import boto3

        return boto3.Session().get_credentials() is not None
    except Exception:
        return False


def _aws_default_region() -> str | None:
    try:
        import boto3

        return boto3.Session().region_name
    except Exception:
        return None


def build_client(
    provider: str,
    *,
    api_key: str | None = None,
    aws_access_key: str | None = None,
    aws_secret_key: str | None = None,
    aws_region: str | None = None,
    aws_session_token: str | None = None,
):
    """Build the right Anthropic client for the chosen provider."""
    import anthropic

    if provider == PROVIDER_BEDROCK:
        aws = resolve_aws(aws_access_key, aws_secret_key, aws_region, aws_session_token)
        if not anthropic_has_bedrock():
            raise ExtractionError(
                "AWS Bedrock support requires boto3. Install it with: pip install boto3"
            )
        kwargs = {"aws_region": aws["aws_region"] or _aws_default_region() or "us-east-1"}
        # If explicit keys are given use them; otherwise let boto3 resolve the
        # default credential chain (~/.aws, env, instance role).
        if aws["aws_access_key"] and aws["aws_secret_key"]:
            kwargs["aws_access_key"] = aws["aws_access_key"]
            kwargs["aws_secret_key"] = aws["aws_secret_key"]
            if aws["aws_session_token"]:
                kwargs["aws_session_token"] = aws["aws_session_token"]
        return anthropic.AnthropicBedrock(**kwargs)

    key = resolve_api_key(api_key)
    if not key:
        raise ExtractionError("No Claude API key provided. Add it in the sidebar or set ANTHROPIC_API_KEY.")
    return anthropic.Anthropic(api_key=key)


def anthropic_has_bedrock() -> bool:
    try:
        import anthropic  # noqa: F401

        return hasattr(anthropic, "AnthropicBedrock")
    except ImportError:
        return False


def _content_block(file_bytes: bytes, filename: str) -> dict:
    suffix = Path(filename).suffix.lower()
    media_type = _MEDIA_TYPES.get(suffix)
    data = base64.standard_b64encode(file_bytes).decode("utf-8")
    if media_type == "application/pdf":
        return {"type": "document", "source": {"type": "base64", "media_type": media_type, "data": data}}
    if media_type:  # image
        return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
    # Fallback: treat as UTF-8 text.
    text = file_bytes.decode("utf-8", errors="ignore")
    return {"type": "text", "text": f"FINANCIAL STATEMENT TEXT:\n\n{text}"}


class ExtractionError(RuntimeError):
    """Raised when AI extraction cannot complete."""


# --- local document preparation (cheap text/OCR before any AI call) ---------
def ocr_available() -> bool:
    try:
        import pytesseract  # noqa
        import pdf2image  # noqa

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _pdf_text(file_bytes: bytes) -> str:
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(file_bytes))
        return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception:
        return ""


def _ocr_pdf(file_bytes: bytes) -> str:
    try:
        import pytesseract
        from pdf2image import convert_from_bytes

        images = convert_from_bytes(file_bytes, dpi=200, first_page=1, last_page=OCR_MAX_PAGES)
        return "\n".join(pytesseract.image_to_string(img) for img in images).strip()
    except Exception:
        return ""


def _ocr_image(file_bytes: bytes) -> str:
    try:
        import io

        import pytesseract
        from PIL import Image

        return pytesseract.image_to_string(Image.open(io.BytesIO(file_bytes))).strip()
    except Exception:
        return ""


def prepare_document(file_bytes: bytes, filename: str) -> tuple[str, object]:
    """Return ``(mode, payload)`` choosing the cheapest viable path.

    mode ∈ {'text', 'ocr', 'document', 'image'}. Text/OCR send plain text to the
    model (few tokens, cheap); 'document'/'image' fall back to vision only when
    no text could be recovered.
    """
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        text = _pdf_text(file_bytes)
        if len(text) >= TEXT_MIN_CHARS:
            return "text", text
        ocr = _ocr_pdf(file_bytes)
        if len(ocr) >= TEXT_MIN_CHARS:
            return "ocr", ocr
        return "document", file_bytes
    if suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        ocr = _ocr_image(file_bytes)
        if len(ocr) >= TEXT_MIN_CHARS:
            return "ocr", ocr
        return "image", file_bytes
    try:
        return "text", file_bytes.decode("utf-8", "ignore")
    except Exception:
        return "document", file_bytes


def _prepared_block(mode: str, payload: object, filename: str) -> dict:
    if mode in ("text", "ocr"):
        text = str(payload)[:TEXT_CHAR_CAP]
        trunc = " (truncated)" if len(str(payload)) > TEXT_CHAR_CAP else ""
        label = "OCR" if mode == "ocr" else "extracted text"
        return {"type": "text", "text": f"FINANCIAL STATEMENT — {label}{trunc}:\n\n{text}"}
    return _content_block(payload, filename)


def _extraction_is_plausible(raw: RawFinancials) -> bool:
    """Did a cheaper model recover enough — and *plausible* — figures to stop?

    Presence alone isn't enough: a value that's present but obviously wrong
    (e.g. interest-bearing debt > total liabilities — the classic 'revaluation
    surplus mistaken for debt' mistake, or any item exceeding total assets)
    forces escalation to a stronger model.
    """
    critical = [raw.total_assets, raw.total_revenue, raw.interest_bearing_debt, raw.illiquid_assets, raw.total_liabilities]
    if sum(1 for v in critical if v is not None) < 4:
        return False
    ta, tl = raw.total_assets, raw.total_liabilities
    if raw.interest_bearing_debt is not None and tl is not None and raw.interest_bearing_debt > tl * 1.02:
        return False  # debt cannot exceed total liabilities
    if ta:
        for v in (raw.interest_bearing_debt, raw.total_liabilities, raw.noncompliant_investments, raw.illiquid_assets):
            if v is not None and v > ta * 1.05:
                return False  # no balance-sheet item should exceed total assets
    return True


def _request_extraction(client, model: str, content_block: dict) -> tuple[dict, object]:
    """Run one forced-tool extraction call and return ``(payload, token_usage)``."""
    import anthropic

    user_message = [content_block, {"type": "text", "text": "Extract the Shariah-screening line items from this document."}]
    try:
        response = client.messages.create(
            model=model,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            tools=[EXTRACTION_TOOL],
            tool_choice={"type": "tool", "name": "report_financials"},
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.AuthenticationError as exc:
        raise ExtractionError("Claude rejected the API key (authentication error).") from exc
    except anthropic.APIStatusError as exc:
        raise ExtractionError(f"Claude API error: {exc.message}") from exc
    except anthropic.APIError as exc:
        raise ExtractionError(f"Could not reach the Claude API: {exc}") from exc
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"Extraction failed ({type(exc).__name__}): {exc}") from exc

    payload = next((block.input for block in response.content if block.type == "tool_use"), None)
    if payload is None:
        raise ExtractionError("Claude did not return structured financials. Try again or enter values manually.")
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload, getattr(response, "usage", None)


def _select_interest_bearing_debt(payload: dict) -> tuple[float | None, str, list[dict]]:
    """Choose the most reliable debt figure and report how it was found.

    Order of preference: code summed from the transcribed balance-sheet lines,
    then summed from the named debt components, then the model's own aggregate
    (least reliable — it tends to absorb deferred tax and revaluation surplus).
    Returns ``(debt, method_used, lines_summed)``.
    """
    lines = payload.get("balance_sheet_lines") or []
    debt_from_lines, lines_summed = classify_debt_from_lines(lines)
    if debt_from_lines is not None:
        return debt_from_lines, "balance_sheet_lines", lines_summed

    component_fields = ("long_term_borrowings", "short_term_borrowings", "current_portion_long_term_debt")
    components = [_as_number(payload.get(field)) for field in component_fields]
    present_components = [value for value in components if value is not None]
    if present_components:
        return sum(present_components), "named_components", []

    return _as_number(payload.get("interest_bearing_debt")), "model_aggregate", []


def _check_section_footing(payload: dict) -> dict:
    """Check whether each balance-sheet section's lines add up to its subtotal.

    A section that doesn't foot means a line was mis-transcribed (a value copied
    from the wrong row), so the debt summed from those lines can't be trusted.
    """
    lines = payload.get("balance_sheet_lines") or []
    reported_equity = _as_number(payload.get("total_equity"))
    reported_liabilities = _as_number(payload.get("total_liabilities"))
    liability_lines_sum = _sum_section(lines, ("non_current_liability", "current_liability"))

    liabilities_foot = _section_matches_subtotal(liability_lines_sum, reported_liabilities)
    equity_foots = _section_matches_subtotal(_sum_section(lines, ("equity",)), reported_equity)
    assets_foot = _section_matches_subtotal(_sum_section(lines, ("non_current_asset", "current_asset")), _as_number(payload.get("total_assets")))

    section_checks = (liabilities_foot, equity_foots, assets_foot)
    if all(check is None for check in section_checks):
        everything_foots = None
    else:
        everything_foots = not any(check is False for check in section_checks)

    return {
        "liabilities_foot": liabilities_foot,
        "everything_foots": everything_foots,
        "liability_lines_sum": liability_lines_sum,
        "reported_liabilities": reported_liabilities,
        "reported_equity": reported_equity,
    }


STANDARD_FACE_VALUES = (1.0, 2.0, 5.0, 10.0, 50.0, 100.0)


def _looks_like_scale_gap(a: float, b: float) -> bool:
    """True if two values differ by roughly a factor of 1,000 / 1,000,000 / 1,000,000,000.

    That is the signature of a units mismatch — e.g. the balance sheet read in
    millions but the share count read in actual units.
    """
    if not a or not b:
        return False
    ratio = max(a, b) / min(a, b)
    return any(abs(ratio - factor) <= factor * 0.1 for factor in (1_000, 1_000_000, 1_000_000_000))


def _shares_outstanding(payload: dict) -> tuple[float | None, str, bool]:
    """Pick a share count that is on the same scale as the balance sheet.

    The NLA-per-share screen divides balance-sheet figures by the share count, so
    the two must share a scale. A count derived from paid-up capital is guaranteed
    to, so when the model's own share count differs from it by a power of 1,000
    (a units mismatch) we use the derived one and flag it. Otherwise we trust the
    count the model read directly. Returns ``(shares, source, scale_mismatch)``.
    """
    reported = _as_number(payload.get("number_of_shares"))
    paid_up_capital = _as_number(payload.get("paid_up_capital"))
    face_value = _as_number(payload.get("share_face_value"))
    if face_value not in STANDARD_FACE_VALUES:
        face_value = 10.0  # PSX par value is almost always Rs. 10

    derived = paid_up_capital / face_value if (paid_up_capital and face_value) else None
    if not derived or derived <= 0:
        return reported, "reported", False
    if not reported or reported <= 0:
        return derived, "paid_up_capital", False
    if abs(reported - derived) <= derived * 0.05:
        return reported, "reported", False
    if _looks_like_scale_gap(reported, derived):
        return derived, "paid_up_capital", True
    return reported, "reported", False


def _financials_from_payload(payload: dict, debt: float | None, total_liabilities: float | None,
                             number_of_shares: float | None) -> RawFinancials:
    """Assemble the RawFinancials the screening engine expects from the payload."""
    return RawFinancials(
        company_name=payload.get("company_name") or "",
        ticker=payload.get("ticker") or "",
        business_compliant=bool(payload.get("business_compliant", True)),
        business_activity=payload.get("business_activity") or "",
        total_assets=_as_number(payload.get("total_assets")),
        interest_bearing_debt=debt,
        noncompliant_investments=_as_number(payload.get("noncompliant_investments")),
        noncompliant_income=_as_number(payload.get("noncompliant_income")),
        total_revenue=_as_number(payload.get("total_revenue")),
        illiquid_assets=_as_number(payload.get("illiquid_assets")),
        total_liabilities=total_liabilities,
        number_of_shares=number_of_shares,
        market_price_per_share=_as_number(payload.get("market_price_per_share")),
    )


def _extract_once(client, model: str, content_block: dict) -> tuple[RawFinancials, dict]:
    """Run a single extraction and turn the model's answer into financials + metadata."""
    payload, usage = _request_extraction(client, model, content_block)
    lines = payload.get("balance_sheet_lines") or []

    debt, debt_method, debt_lines = _select_interest_bearing_debt(payload)
    footing = _check_section_footing(payload)
    shares, share_source, share_scale_mismatch = _shares_outstanding(payload)

    equity = footing["reported_equity"]
    if equity is None:
        equity = _sum_section(lines, ("equity",))
    total_liabilities = footing["reported_liabilities"]
    if total_liabilities is None:
        total_liabilities = _sum_section(lines, ("non_current_liability", "current_liability"))

    financials = _financials_from_payload(payload, debt, total_liabilities, shares)

    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    debt_is_low_confidence = debt_method == "balance_sheet_lines" and footing["liabilities_foot"] is False

    meta = {
        "currency_unit": payload.get("currency_unit") or "",
        "period": payload.get("period") or "",
        "reporting_period_months": payload.get("reporting_period_months"),
        "extraction_notes": payload.get("extraction_notes") or "",
        "model": model,
        "evidence": payload.get("evidence") or [],
        "total_equity": equity,
        "debt_method": debt_method,
        "debt_lines": debt_lines,
        "balance_sheet_lines": lines,
        "lines_foot": footing["everything_foots"],
        "foot_liabilities": footing["liabilities_foot"],
        "liab_lines_sum": footing["liability_lines_sum"],
        "reported_total_liabilities": footing["reported_liabilities"],
        "debt_low_confidence": bool(debt_is_low_confidence),
        "share_count_source": share_source,
        "share_scale_mismatch": share_scale_mismatch,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens} if input_tokens is not None else None,
        "cost_usd": estimate_cost(model, input_tokens, output_tokens),
    }
    return financials, meta


def _balance_sheet_reconciles(raw: RawFinancials, meta: dict) -> bool:
    """Balance-sheet identity check: Total Assets ≈ Total Equity + Total Liabilities."""
    eq = meta.get("total_equity")
    if raw.total_assets and eq is not None and raw.total_liabilities is not None:
        return abs(raw.total_assets - (eq + raw.total_liabilities)) <= raw.total_assets * 0.03
    return True  # can't check → don't force escalation on this alone


def _should_escalate_for_debt(raw: RawFinancials, model: str) -> bool:
    """True when the cheapest tier shouldn't be trusted as the final answer.

    Reconciliation can't catch a *consistent* misread — Haiku read Crescent's
    short-term borrowings as 5.3M instead of 8.5M, yet the sheet still balanced.
    So whenever debt is a material fraction of assets (the figure that actually
    decides the debt screen), confirm with a stronger model. Near-debt-free
    companies stay on the cheap model.
    """
    if _model_tier(model) != "haiku":
        return False
    ta, debt = raw.total_assets, raw.interest_bearing_debt
    return bool(ta and debt is not None and debt > ta * 0.10)


def extract_financials(
    file_bytes: bytes, filename: str, *, provider: str = PROVIDER_ANTHROPIC, api_key: str | None = None,
    model: str = DEFAULT_MODEL, aws_access_key: str | None = None, aws_secret_key: str | None = None,
    aws_region: str | None = None, aws_session_token: str | None = None,
) -> tuple[RawFinancials, dict[str, object]]:
    """Single-model extraction (prepares the document locally first)."""
    client = build_client(provider, api_key=api_key, aws_access_key=aws_access_key, aws_secret_key=aws_secret_key, aws_region=aws_region, aws_session_token=aws_session_token)
    mode, payload = prepare_document(file_bytes, filename)
    raw, meta = _extract_once(client, model, _prepared_block(mode, payload, filename))
    meta["mode"] = mode
    return raw, meta


def _models_to_try(model: str | None, provider: str, ladder: list[str] | None, escalate: bool) -> list[str]:
    """The ordered list of model IDs smart_extract will attempt, cheapest first."""
    if model:
        return [model]
    models = ladder or MODEL_LADDER.get(provider, [DEFAULT_MODEL])
    return models if escalate else models[:1]


def _good_enough_to_stop(financials: RawFinancials, meta: dict, model: str) -> bool:
    """Whether this model's result is solid enough to stop climbing the ladder."""
    return (
        _extraction_is_plausible(financials)
        and _balance_sheet_reconciles(financials, meta)
        and meta.get("foot_liabilities") is not False
        and not _should_escalate_for_debt(financials, model)
    )


def smart_extract(
    file_bytes: bytes, filename: str, *, provider: str = PROVIDER_ANTHROPIC, api_key: str | None = None,
    aws_access_key: str | None = None, aws_secret_key: str | None = None, aws_region: str | None = None,
    aws_session_token: str | None = None, model: str | None = None, escalate: bool = True,
    ladder: list[str] | None = None,
) -> tuple[RawFinancials, dict[str, object]]:
    """Cost-smart extraction: local text/OCR → cheapest model → escalate if needed.

    If ``model`` is given, only that model is used (no escalation). ``ladder``
    overrides the default cheap→expensive sequence (used for discovered Bedrock
    models). Returns the parsed financials plus meta including ``mode``, per-tier
    ``attempts``, ``total_cost_usd``, ``final_model`` and ``escalated``.
    """
    client = build_client(provider, api_key=api_key, aws_access_key=aws_access_key, aws_secret_key=aws_secret_key, aws_region=aws_region, aws_session_token=aws_session_token)
    mode, payload = prepare_document(file_bytes, filename)
    content_block = _prepared_block(mode, payload, filename)

    attempts: list[dict] = []
    total_cost = 0.0
    financials = None
    final_meta: dict = {}
    final_model = None
    last_error = None

    for current_model in _models_to_try(model, provider, ladder, escalate):
        try:
            current_financials, current_meta = _extract_once(client, current_model, content_block)
        except ExtractionError as exc:
            last_error = exc
            attempts.append({"model": current_model, "error": str(exc)[:160]})
            continue

        financials, final_meta, final_model = current_financials, current_meta, current_model
        usage = current_meta.get("usage") or {}
        attempts.append({
            "model": current_model,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cost_usd": current_meta.get("cost_usd"),
        })
        total_cost += current_meta.get("cost_usd") or 0.0
        if _good_enough_to_stop(current_financials, current_meta, current_model):
            break

    if financials is None:
        raise last_error or ExtractionError("No model could extract the document.")

    successful_attempts = [attempt for attempt in attempts if "error" not in attempt]
    meta = dict(final_meta)
    meta.update({
        "mode": mode,
        "attempts": attempts,
        "total_cost_usd": total_cost,
        "final_model": final_model,
        "model": final_model,
        "escalated": len(successful_attempts) > 1,
    })
    return financials, meta


def _as_number(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def period_label(months: object) -> str:
    """Human label for an income-statement span (drives the period guard, #3)."""
    try:
        n = int(months)
    except (TypeError, ValueError):
        return ""
    return {
        12: "Full year (12 months)",
        9: "Nine months (3rd-quarter cumulative)",
        6: "Half year (6 months)",
        3: "One quarter (3 months)",
    }.get(n, f"{n} months")


def is_partial_period(months: object) -> bool:
    """True when income figures cover less than a full year (income ratio is a
    valid same-period ratio, but worth flagging so the user knows it's partial)."""
    try:
        return 0 < int(months) < 12
    except (TypeError, ValueError):
        return False
