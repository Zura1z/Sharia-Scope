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
            "interest_bearing_debt": dict(_NUM, description="Short- + long-term interest-based borrowings/loans/leases."),
            "noncompliant_investments": dict(_NUM, description="ONLY investments in conventional/interest-based instruments (bonds, T-bills, conventional bank deposits/funds) and shares of companies that are themselves Shariah non-compliant. EXCLUDE investments in Shariah-compliant subsidiaries/associates. If the split is unclear, report what you can and note the assumption."),
            "noncompliant_income": dict(_NUM, description="Interest income plus income from other non-Shariah sources."),
            "total_revenue": dict(_NUM, description="Total revenue/turnover plus other income, used as the income-ratio denominator."),
            "illiquid_assets": dict(_NUM, description="Total assets minus liquid assets (cash, bank, receivables, short-term marketable investments)."),
            "total_liabilities": _NUM,
            "number_of_shares": dict(_NUM, description="Number of ordinary shares outstanding."),
            "market_price_per_share": dict(_NUM, description="Latest market price per share, if stated."),
            "currency_unit": {"type": ["string", "null"], "description": "e.g. 'PKR 000' or 'PKR mn'."},
            "period": {"type": ["string", "null"], "description": "Reporting period, e.g. 'Year ended 30 June 2025'."},
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
            "total_assets", "interest_bearing_debt", "noncompliant_investments",
            "noncompliant_income", "total_revenue", "illiquid_assets",
            "total_liabilities", "number_of_shares", "market_price_per_share",
            "currency_unit", "period", "extraction_notes", "evidence",
        ],
        "additionalProperties": False,
    },
}

# Published per-1M-token list prices (Anthropic first-party). Bedrock prices differ
# by region, so cost is shown only for the first-party API.
MODEL_PRICES = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def estimate_cost(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    price = MODEL_PRICES.get(model)
    if not price or input_tokens is None or output_tokens is None:
        return None
    return input_tokens / 1_000_000 * price[0] + output_tokens / 1_000_000 * price[1]

SYSTEM_PROMPT = (
    "You are a financial analyst preparing inputs for PSX/KMI Shariah screening. "
    "Read the attached financial statements and extract the exact line items needed. "
    "Use the company's STANDALONE (unconsolidated) accounts — PSX/KMI Shariah screening "
    "is based on these, not the consolidated group accounts — and the most recent period. "
    "Report amounts as plain numbers in a single consistent unit. If a figure genuinely is "
    "not in the document, return null for it and explain in extraction_notes — never guess "
    "or fabricate."
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


def extract_financials(
    file_bytes: bytes,
    filename: str,
    *,
    provider: str = PROVIDER_ANTHROPIC,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    aws_access_key: str | None = None,
    aws_secret_key: str | None = None,
    aws_region: str | None = None,
    aws_session_token: str | None = None,
) -> tuple[RawFinancials, dict[str, object]]:
    """Extract raw financials from a statement file via Claude.

    Works against the Anthropic API or AWS Bedrock. Returns the parsed
    :class:`RawFinancials` plus a metadata dict (currency unit, period,
    extraction notes) for display.
    """
    import anthropic

    client = build_client(
        provider,
        api_key=api_key,
        aws_access_key=aws_access_key,
        aws_secret_key=aws_secret_key,
        aws_region=aws_region,
        aws_session_token=aws_session_token,
    )
    try:
        response = client.messages.create(
            model=model,
            max_tokens=8000,  # room for the flat fields + the per-field evidence array
            system=SYSTEM_PROMPT,
            tools=[EXTRACTION_TOOL],
            tool_choice={"type": "tool", "name": "report_financials"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        _content_block(file_bytes, filename),
                        {"type": "text", "text": "Extract the Shariah-screening line items from this document."},
                    ],
                }
            ],
        )
    except anthropic.AuthenticationError as exc:
        raise ExtractionError("Claude rejected the API key (authentication error).") from exc
    except anthropic.APIStatusError as exc:
        raise ExtractionError(f"Claude API error: {exc.message}") from exc
    except anthropic.APIError as exc:  # connection/other
        raise ExtractionError(f"Could not reach the Claude API: {exc}") from exc
    except ExtractionError:
        raise
    except Exception as exc:  # e.g. botocore credential/region errors on Bedrock
        raise ExtractionError(f"Extraction failed ({type(exc).__name__}): {exc}") from exc

    payload = next((b.input for b in response.content if b.type == "tool_use"), None)
    if payload is None:
        raise ExtractionError("Claude did not return structured financials. Try again or enter values manually.")
    if isinstance(payload, str):
        payload = json.loads(payload)

    raw = RawFinancials(
        company_name=payload.get("company_name") or "",
        ticker=payload.get("ticker") or "",
        business_compliant=bool(payload.get("business_compliant", True)),
        business_activity=payload.get("business_activity") or "",
        total_assets=_num(payload.get("total_assets")),
        interest_bearing_debt=_num(payload.get("interest_bearing_debt")),
        noncompliant_investments=_num(payload.get("noncompliant_investments")),
        noncompliant_income=_num(payload.get("noncompliant_income")),
        total_revenue=_num(payload.get("total_revenue")),
        illiquid_assets=_num(payload.get("illiquid_assets")),
        total_liabilities=_num(payload.get("total_liabilities")),
        number_of_shares=_num(payload.get("number_of_shares")),
        market_price_per_share=_num(payload.get("market_price_per_share")),
    )
    usage = getattr(response, "usage", None)
    in_tok = getattr(usage, "input_tokens", None)
    out_tok = getattr(usage, "output_tokens", None)
    cost = estimate_cost(model, in_tok, out_tok) if provider == PROVIDER_ANTHROPIC else None
    meta = {
        "currency_unit": payload.get("currency_unit") or "",
        "period": payload.get("period") or "",
        "extraction_notes": payload.get("extraction_notes") or "",
        "model": model,
        "evidence": payload.get("evidence") or [],
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok} if in_tok is not None else None,
        "cost_usd": cost,
    }
    return raw, meta


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
