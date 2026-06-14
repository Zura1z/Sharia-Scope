"""Live market-price lookup for PSX tickers.

The NLA-per-share screen needs a *market price*, which is never in the financial
statements — so we fetch it from Yahoo Finance (PSX symbols use the ``.KA``
suffix, e.g. ``LUCK.KA``). The price always carries its as-of trading date so the
user knows how stale it is and can override it by hand.

Kept deliberately free of Streamlit so it can be unit-tested and cached by the
caller. Returns ``None`` (never raises) when a price can't be resolved, so the
screener degrades gracefully to manual entry.
"""

from __future__ import annotations


def available() -> bool:
    """True if the yfinance library is importable."""
    try:
        import yfinance  # noqa: F401
    except ImportError:
        return False
    return True


def _normalize_company(name: str) -> str:
    """Strip suffixes/punctuation so 'Crescent Textile Mills Limited' == '... Ltd'."""
    low = (name or "").lower()
    for token in ("limited", "ltd.", "ltd", "(the)", "the ", "company", "co.", "&"):
        low = low.replace(token, " ")
    return " ".join(low.replace(".", " ").replace(",", " ").split())


def resolve_ticker(extracted_ticker: str, company_name: str = "", index_pairs=None) -> str:
    """Best PSX symbol for a price lookup.

    The document's stated ticker can be a brand symbol Yahoo doesn't know
    (a report said 'CRESTEX' where PSX/Yahoo use 'CRTM'). When an authoritative
    ``(ticker, company_name)`` index is supplied, a confident company-name match
    wins; otherwise we keep the extracted ticker.
    """
    target = _normalize_company(company_name)
    if index_pairs and target:
        for tk, nm in index_pairs:
            if not tk:
                continue
            cand = _normalize_company(nm)
            if cand and (cand == target or cand in target or target in cand):
                return str(tk).strip().upper()
    return (extracted_ticker or "").strip().upper()


def yahoo_symbol(ticker: str) -> str:
    """Map a PSX ticker to its Yahoo Finance symbol (``CRTM`` -> ``CRTM.KA``)."""
    t = (ticker or "").strip().upper()
    if not t:
        return ""
    # Drop any exchange suffix the user may have typed, then add Karachi's.
    base = t.split(".")[0].strip()
    return f"{base}.KA" if base else ""


def fetch_price(ticker: str, *, timeout: float = 12.0) -> dict | None:
    """Return ``{price, currency, as_of, source, symbol}`` for a PSX ticker, or None.

    ``as_of`` is the close date of the most recent available bar (YYYY-MM-DD) so
    the caller can show *which day* the price is from. Never raises.
    """
    symbol = yahoo_symbol(ticker)
    if not symbol or not available():
        return None
    try:
        import yfinance as yf

        tk = yf.Ticker(symbol)
        hist = tk.history(period="5d", timeout=timeout)
        if hist is None or len(hist) == 0 or "Close" not in hist:
            return None
        last = hist.dropna(subset=["Close"]).tail(1)
        if len(last) == 0:
            return None
        price = float(last["Close"].iloc[-1])
        if not price or price <= 0:
            return None
        as_of = last.index[-1].strftime("%Y-%m-%d")
        return {
            "price": round(price, 2),
            "currency": "PKR",
            "as_of": as_of,
            "source": "Yahoo Finance",
            "symbol": symbol,
        }
    except Exception:
        return None
