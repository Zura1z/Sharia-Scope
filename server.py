"""FastAPI backend for Sharia Scope.

Run with: uvicorn server:app --port 8501 --reload

Environment variables:
  ANTHROPIC_API_KEY   — required for AI extraction
  FIREBASE_SA_JSON    — optional; Firebase service-account JSON as a string
"""
from __future__ import annotations

import base64
import dataclasses
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

# Load .env from the project root (handles multi-line values like FIREBASE_SA_JSON)
_ENV_FILE = Path(__file__).resolve().parent / ".env"
if _ENV_FILE.exists():
    _raw = _ENV_FILE.read_text()
    _key = None
    _buf: list[str] = []
    for _line in _raw.splitlines():
        if _key is None:
            if "=" in _line and not _line.lstrip().startswith("#"):
                _key, _, _rest = _line.partition("=")
                _key = _key.strip()
                _buf = [_rest]
        else:
            _buf.append(_line)
            # end of multi-line value when we hit a new KEY= line or EOF
            # We rely on the simple heuristic: new KEY= resets the accumulator
        # Commit when we see a new key starting or at end
        # Simpler: just collect all lines for a key until next key line
    # Re-parse properly: split on first KEY= at the start of a line
    import re as _re
    for _m in _re.finditer(r'^([A-Z][A-Z0-9_]*)=(.*?)(?=\n[A-Z][A-Z0-9_]*=|\Z)', _raw, _re.S | _re.M):
        _k, _v = _m.group(1), _m.group(2).strip().strip("'\"")
        if _k not in os.environ:
            os.environ[_k] = _v

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import ai_extract
import market_data
import storage
from allshariah_core import (
    RawFinancials,
    compute_ratios,
    screen_metrics,
)
from report import build_pdf_report, build_purification_summary

APP_DIR = Path(__file__).resolve().parent
APP_VERSION = "1.3"
RULE_VERSION = "PSX-KMI-Dec2025"

# Public production URL used for canonical / OG / sitemap absolute links. Pinned
# (not derived from request headers) so it stays correct + https behind Replit's
# proxy. Override with the PUBLIC_BASE_URL env var for a custom domain.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://sharia-scope.replit.app").rstrip("/")

app = FastAPI(title="Sharia Scope", version=APP_VERSION)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY")


def _bedrock_ready() -> bool:
    """True if AWS Bedrock is usable: explicitly opted in, or AWS creds are present."""
    if os.environ.get("AI_PROVIDER", "").strip().lower() == ai_extract.PROVIDER_BEDROCK:
        return True
    aws = ai_extract.resolve_aws()
    return bool(aws.get("aws_access_key") and aws.get("aws_secret_key"))


def _ai_provider() -> str:
    """Which AI provider the server should use for extraction.

    Set AI_PROVIDER=bedrock to force Bedrock; otherwise Anthropic is used when an
    Anthropic key exists, falling back to Bedrock if only AWS creds are present.
    """
    if os.environ.get("AI_PROVIDER", "").strip().lower() == ai_extract.PROVIDER_BEDROCK:
        return ai_extract.PROVIDER_BEDROCK
    if _api_key():
        return ai_extract.PROVIDER_ANTHROPIC
    if _bedrock_ready():
        return ai_extract.PROVIDER_BEDROCK
    return ai_extract.PROVIDER_ANTHROPIC


def _ai_ready() -> bool:
    """Server-side AI readiness — an Anthropic env key OR a configured Bedrock."""
    return bool(_api_key()) or _bedrock_ready()


def _fb_cred() -> dict | None:
    raw = os.environ.get("FIREBASE_SA_JSON", "").strip()
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return storage.resolve_credentials(None, base_dir=APP_DIR)


def _dt_safe(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _company_tag(company: str | None, ticker: str | None) -> str:
    """'Company (TICKER)' from whatever is known — names files after their content."""
    company = (company or "").strip()
    ticker = (ticker or "").strip().upper()
    if company and ticker:
        return f"{company} ({ticker})"
    return company or ticker or ""


def _branded_name(company: str | None, ticker: str | None, suffix: str, ext: str) -> str:
    """Branded, content-describing download name, e.g.
    'Sharia Scope - Engro Corporation (ENGRO) - Compliance Report.pdf'."""
    tag = _company_tag(company, ticker)
    base = f"Sharia Scope - {tag} - {suffix}" if tag else f"Sharia Scope - {suffix}"
    safe = re.sub(r"[^\w\s().\-]", "", base).strip()
    return f"{safe}.{ext}"


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@app.get("/api/status")
def get_status() -> dict:
    fb = _fb_cred()
    return {
        "ai_ready": _ai_ready(),
        "ai_provider": _ai_provider(),
        "storage_configured": bool(fb),
        "app_version": APP_VERSION,
        "rule_version": RULE_VERSION,
    }


# ---------------------------------------------------------------------------
# Extract
# ---------------------------------------------------------------------------

@app.post("/api/extract")
async def extract(
    file: UploadFile = File(...),
    x_anthropic_key: str | None = Header(None, alias="X-Anthropic-Key"),
) -> dict:
    provider = _ai_provider()
    file_bytes = await file.read()

    if provider == ai_extract.PROVIDER_BEDROCK:
        aws = ai_extract.resolve_aws()   # reads AWS_* from the environment
        kwargs = dict(
            provider=ai_extract.PROVIDER_BEDROCK,
            aws_access_key=aws.get("aws_access_key"),
            aws_secret_key=aws.get("aws_secret_key"),
            aws_region=aws.get("aws_region"),
            aws_session_token=aws.get("aws_session_token"),
            model=os.environ.get("BEDROCK_MODEL") or ai_extract.DEFAULT_BEDROCK_MODEL,
        )
    else:
        # Prefer the server's env key; fall back to a key the user supplied in-app.
        # The user key is used only for this request — never logged or persisted.
        api_key = _api_key() or (x_anthropic_key.strip() if x_anthropic_key else None)
        if not api_key:
            raise HTTPException(400, "No AI key available — set ANTHROPIC_API_KEY (or AWS creds with AI_PROVIDER=bedrock) on the server, or enter your own Anthropic key in the app.")
        kwargs = dict(provider=ai_extract.PROVIDER_ANTHROPIC, api_key=api_key)

    try:
        raw, meta = ai_extract.smart_extract(file_bytes, file.filename or "upload", **kwargs)
    except ai_extract.ExtractionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Extraction failed: {exc}") from exc

    result = dataclasses.asdict(raw)
    result["extraction_meta"] = meta
    return result


# ---------------------------------------------------------------------------
# Price
# ---------------------------------------------------------------------------

@app.get("/api/price/{ticker}")
def get_price(ticker: str) -> dict:
    if not market_data.available():
        raise HTTPException(503, "yfinance not installed")
    quote = market_data.fetch_price(ticker)
    if not quote:
        raise HTTPException(404, f"No price found for {ticker}")
    return quote


# ---------------------------------------------------------------------------
# Saved runs
# ---------------------------------------------------------------------------

@app.get("/api/runs")
def list_runs() -> list[dict]:
    fb = _fb_cred()
    if not fb:
        return []
    try:
        runs = storage.list_runs(fb, limit=200)
        for r in runs:
            for k, v in r.items():
                r[k] = _dt_safe(v)
        return runs
    except storage.StorageError as exc:
        raise HTTPException(500, str(exc)) from exc


class SaveRunBody(BaseModel):
    record: dict
    source_name: str | None = None
    source_b64: str | None = None
    report_b64: str | None = None


@app.post("/api/runs")
def save_run(body: SaveRunBody) -> dict:
    fb = _fb_cred()
    if not fb:
        raise HTTPException(503, "Firebase not configured")
    source = None
    if body.source_b64 and body.source_name:
        source = {
            "bytes": base64.b64decode(body.source_b64),
            "name": body.source_name,
        }
    report_bytes = base64.b64decode(body.report_b64) if body.report_b64 else None
    try:
        result = storage.save_run(
            fb,
            record=body.record,
            source=source,
            report_bytes=report_bytes,
        )
        return result
    except storage.StorageError as exc:
        raise HTTPException(500, str(exc)) from exc


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str) -> dict:
    fb = _fb_cred()
    if not fb:
        raise HTTPException(503, "Firebase not configured")
    try:
        storage.delete_run(fb, run_id)
        return {"ok": True}
    except storage.StorageError as exc:
        raise HTTPException(500, str(exc)) from exc


def _get_run_doc(fb: dict, run_id: str) -> dict:
    doc = storage.get_run(fb, run_id)
    if doc is None:
        raise HTTPException(404, "Run not found")
    return doc


@app.get("/api/runs/{run_id}/source")
def get_run_source(run_id: str) -> Response:
    fb = _fb_cred()
    if not fb:
        raise HTTPException(503, "Firebase not configured")
    doc = _get_run_doc(fb, run_id)
    path = doc.get("source_path")
    if not path:
        raise HTTPException(404, "No source document stored for this run")
    try:
        data = storage.download_blob(fb, path)
    except storage.StorageError as exc:
        raise HTTPException(500, str(exc)) from exc
    orig = doc.get("source_filename", "source.pdf")
    ext = orig.rsplit(".", 1)[-1].lower() if "." in orig else "pdf"
    filename = _branded_name(doc.get("company_name"), doc.get("ticker"), "Source", ext)
    media_type = "application/pdf" if ext == "pdf" else "application/octet-stream"
    return Response(content=data, media_type=media_type,
                    headers={"Content-Disposition": f'inline; filename="{filename}"'})


@app.get("/api/runs/{run_id}/report")
def get_run_report(run_id: str) -> Response:
    fb = _fb_cred()
    if not fb:
        raise HTTPException(503, "Firebase not configured")
    doc = _get_run_doc(fb, run_id)
    path = doc.get("report_path")
    if not path:
        raise HTTPException(404, "No report stored for this run")
    try:
        data = storage.download_blob(fb, path)
    except storage.StorageError as exc:
        raise HTTPException(500, str(exc)) from exc
    filename = _branded_name(doc.get("company_name"), doc.get("ticker"), "Compliance Report", "pdf")
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{filename}"'})


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

class PdfBody(BaseModel):
    form: dict
    meta: dict
    purification: dict | None = None
    audit: dict | None = None


def _raw_from_form(f: dict) -> RawFinancials:
    return RawFinancials(
        company_name=f.get("company_name", ""),
        ticker=f.get("ticker", ""),
        business_compliant=bool(f.get("business_compliant", True)),
        business_activity=f.get("business_activity", ""),
        total_assets=_float(f.get("total_assets")),
        interest_bearing_debt=_float(f.get("interest_bearing_debt")),
        noncompliant_investments=_float(f.get("noncompliant_investments")),
        noncompliant_income=_float(f.get("noncompliant_income")),
        total_revenue=_float(f.get("total_revenue")),
        illiquid_assets=_float(f.get("illiquid_assets")),
        total_liabilities=_float(f.get("total_liabilities")),
        number_of_shares=_float(f.get("number_of_shares")),
        market_price_per_share=_float(f.get("market_price_per_share")),
    )


@app.post("/api/pdf")
def generate_pdf(body: PdfBody) -> Response:
    raw = _raw_from_form(body.form)
    ratios = compute_ratios(raw)
    evaluation = screen_metrics(ratios)
    audit = body.audit or {
        "data_source": "manual",
        "model": "n/a",
        "verified": False,
        "rule_version": RULE_VERSION,
        "app_version": APP_VERSION,
        "evidence": [],
    }
    try:
        pdf_bytes = build_pdf_report(
            evaluation=evaluation,
            company_name=raw.company_name,
            ticker=raw.ticker,
            meta=body.meta,
            purification=body.purification,
            generated_on=datetime.now().strftime("%Y-%m-%d %H:%M"),
            audit=audit,
        )
    except Exception as exc:
        raise HTTPException(500, f"PDF generation failed: {exc}") from exc
    filename = _branded_name(raw.company_name, raw.ticker, "Compliance Report", "pdf")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class PurifBody(BaseModel):
    form: dict
    meta: dict
    purification: dict


@app.post("/api/purification-pdf")
def generate_purification_pdf(body: PurifBody) -> Response:
    """On-demand, non-persisted one-pager with the investor's dividend purification."""
    raw = _raw_from_form(body.form)
    ratios = compute_ratios(raw)
    evaluation = screen_metrics(ratios)
    try:
        pdf_bytes = build_purification_summary(
            evaluation=evaluation,
            company_name=raw.company_name,
            ticker=raw.ticker,
            meta=body.meta,
            purification=body.purification,
            generated_on=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
    except Exception as exc:
        raise HTTPException(500, f"PDF generation failed: {exc}") from exc
    filename = _branded_name(raw.company_name, raw.ticker, "Purification Summary", "pdf")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# SEO — robots & sitemap (host-aware, so any deploy domain works unchanged)
# ---------------------------------------------------------------------------

_SEO_PLACEHOLDER_ORIGIN = "https://sharia-scope.app"


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """Serve index.html with the canonical/OG absolute URLs rewritten to the
    pinned production origin — social/crawler scrapers don't run JS, so the
    tags must already be correct (and https) in the raw HTML."""
    html = (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")
    html = html.replace(_SEO_PLACEHOLDER_ORIGIN, PUBLIC_BASE_URL)
    return HTMLResponse(html)


@app.get("/robots.txt")
def robots_txt() -> Response:
    body = f"User-agent: *\nAllow: /\n\nSitemap: {PUBLIC_BASE_URL}/sitemap.xml\n"
    return Response(content=body, media_type="text/plain")


@app.get("/sitemap.xml")
def sitemap_xml() -> Response:
    base = PUBLIC_BASE_URL
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{base}/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>\n"
        "</urlset>\n"
    )
    return Response(content=body, media_type="application/xml")


# ---------------------------------------------------------------------------
# Static files — must be last (catches everything else)
# ---------------------------------------------------------------------------

app.mount("/", StaticFiles(directory=str(APP_DIR / "static"), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8501))
    uvicorn.run(app, host="0.0.0.0", port=port)
