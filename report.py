"""Branded PDF compliance report for Sharia Scope.

Two-page layout:
  Page 1 — verdict header, metadata strip, ratio card grid, purification block
  Page 2 — methodology guide, glossary, footer
"""
from __future__ import annotations

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from allshariah_core import CompanyEvaluation, MetricResult, format_number, format_percent

# ── Brand palette ──────────────────────────────────────────────────────────────
_GREEN     = colors.HexColor("#0C5C45")
_GREEN_DK  = colors.HexColor("#093D2F")
_GOLD      = colors.HexColor("#C7A24B")
_GOLD_LT   = colors.HexColor("#FAF3E4")
_BG        = colors.HexColor("#F4F5F2")
_WHITE     = colors.white
_INK       = colors.HexColor("#11201B")
_INK_3     = colors.HexColor("#4A5E55")
_INK_4     = colors.HexColor("#6C7B73")
_BORDER    = colors.HexColor("#D8E2DC")
_PASS      = colors.HexColor("#0E7A57")
_PASS_BG   = colors.HexColor("#E8F5EF")
_FAIL      = colors.HexColor("#B42318")
_FAIL_BG   = colors.HexColor("#FFF0EE")
_WARN      = colors.HexColor("#7A5210")
_WARN_BG   = colors.HexColor("#FBF1E0")

PAGE_W, PAGE_H = A4
MARGIN     = 18 * mm
CONTENT_W  = PAGE_W - 2 * MARGIN


def esc(value: object) -> str:
    return escape(str(value)) if value is not None else ""


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(text, style)


def _sty(name: str, **kw) -> ParagraphStyle:
    from reportlab.lib.styles import getSampleStyleSheet
    base = getSampleStyleSheet()["Normal"]
    return ParagraphStyle(name, parent=base, **kw)


# Pre-build styles once (avoids re-creating on every call, harmless if called multiple times)
def _build_styles() -> dict:
    return {
        "lbl":        _sty("lbl",        fontSize=7,    textColor=_INK_4, fontName="Helvetica-Bold",
                            leading=10),
        "lbl_white":  _sty("lbl_white",  fontSize=7,    textColor=_WHITE, fontName="Helvetica-Bold",
                            leading=10),
        "lbl_gold":   _sty("lbl_gold",   fontSize=7,    textColor=_GOLD,  fontName="Helvetica-Bold",
                            leading=10),
        "h1_white":   _sty("h1_white",   fontSize=22,   textColor=_WHITE, fontName="Helvetica-Bold",
                            leading=27, spaceAfter=2),
        "sub_white":  _sty("sub_white",  fontSize=9,    textColor=colors.HexColor("#AACCBB"),
                            leading=12),
        "h2":         _sty("h2",         fontSize=11,   textColor=_INK,   fontName="Helvetica-Bold",
                            leading=14, spaceBefore=12, spaceAfter=4),
        "h3":         _sty("h3",         fontSize=9,    textColor=_INK,   fontName="Helvetica-Bold",
                            leading=12, spaceAfter=2),
        "body":       _sty("body",       fontSize=9,    textColor=_INK,   leading=13),
        "body_mute":  _sty("body_mute",  fontSize=8.5,  textColor=_INK_4, leading=12),
        "meta_val":   _sty("meta_val",   fontSize=8.5,  textColor=_INK,   fontName="Helvetica-Bold",
                            leading=12),
        "small":      _sty("small",      fontSize=7.5,  textColor=_INK_4, leading=11),
        "pass_val":   _sty("pass_val",   fontSize=17,   textColor=_PASS,  fontName="Helvetica-Bold",
                            leading=21),
        "fail_val":   _sty("fail_val",   fontSize=17,   textColor=_FAIL,  fontName="Helvetica-Bold",
                            leading=21),
        "neu_val":    _sty("neu_val",    fontSize=17,   textColor=_INK_4, fontName="Helvetica-Bold",
                            leading=21),
        "badge_pass": _sty("badge_pass", fontSize=7.5,  textColor=_PASS,  fontName="Helvetica-Bold",
                            leading=9, alignment=TA_RIGHT),
        "badge_fail": _sty("badge_fail", fontSize=7.5,  textColor=_FAIL,  fontName="Helvetica-Bold",
                            leading=9, alignment=TA_RIGHT),
        "badge_neu":  _sty("badge_neu",  fontSize=7.5,  textColor=_INK_4, fontName="Helvetica-Bold",
                            leading=9, alignment=TA_RIGHT),
        "threshold":  _sty("threshold",  fontSize=7.5,  textColor=_INK_4, leading=10),
        "meaning":    _sty("meaning",    fontSize=7.5,  textColor=_INK_4, leading=11, spaceAfter=0),
        "gterm":      _sty("gterm",      fontSize=8.5,  textColor=_INK,   fontName="Helvetica-Bold",
                            leading=12),
        "gdef":       _sty("gdef",       fontSize=8,    textColor=_INK_3, leading=12),
        "disclaimer": _sty("disclaimer", fontSize=7,    textColor=_INK_4, leading=10),
        "brand_link": _sty("brand_link", fontSize=7.5,  textColor=_GREEN, fontName="Helvetica-Bold",
                            leading=10),
        "status_badge":_sty("status_badge", fontSize=9, textColor=_WHITE, fontName="Helvetica-Bold",
                            leading=11, alignment=TA_CENTER),
        "pass_count": _sty("pass_count", fontSize=10,   textColor=_PASS,  fontName="Helvetica-Bold",
                            leading=12, alignment=TA_RIGHT),
        "fail_count": _sty("fail_count", fontSize=10,   textColor=_FAIL,  fontName="Helvetica-Bold",
                            leading=12, alignment=TA_RIGHT),
    }


def _ratio_card(m: MetricResult, S: dict, card_w: float) -> Table:
    """Build one metric card as a nested Table."""
    is_pass = m.passed is True
    is_fail = m.passed is False

    BADGE_W = 15 * mm
    INNER_W = card_w  # card already accounts for outer padding

    val_sty    = S["pass_val"] if is_pass else (S["fail_val"] if is_fail else S["neu_val"])
    badge_sty  = S["badge_pass"] if is_pass else (S["badge_fail"] if is_fail else S["badge_neu"])
    badge_txt  = "✓ PASS" if is_pass else ("✗ FAIL" if is_fail else "— N/A")
    accent_col = _PASS if is_pass else (_FAIL if is_fail else _BORDER)
    card_bg    = _PASS_BG if is_pass else (_FAIL_BG if is_fail else _BG)

    val_str = format_number(m.value) if m.key == "net_liquid_assets_ratio" else format_percent(m.value)

    card = Table(
        [
            [_p(esc(m.label).upper(), S["lbl"]), _p(f"<b>{badge_txt}</b>", badge_sty)],
            [_p(esc(val_str), val_sty), ""],
            [_p(esc(m.threshold), S["threshold"]), ""],
            [_p(esc(m.meaning), S["meaning"]), ""],
        ],
        colWidths=[INNER_W - BADGE_W, BADGE_W],
    )
    card.setStyle(TableStyle([
        # colored top accent stripe
        ("LINEABOVE",     (0, 0), (-1, 0), 3,   accent_col),
        # card background
        ("BACKGROUND",    (0, 0), (-1, -1), card_bg),
        # span value/threshold/meaning across both columns
        ("SPAN",          (0, 1), (-1, 1)),
        ("SPAN",          (0, 2), (-1, 2)),
        ("SPAN",          (0, 3), (-1, 3)),
        # padding
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("TOPPADDING",    (0, 0), (1, 0),   5),
        ("BOTTOMPADDING", (0, 0), (1, 0),   3),
        ("TOPPADDING",    (0, 1), (-1, 1),  3),
        ("BOTTOMPADDING", (0, 1), (-1, 1),  2),
        ("TOPPADDING",    (0, 2), (-1, 2),  1),
        ("BOTTOMPADDING", (0, 2), (-1, 2),  2),
        ("TOPPADDING",    (0, 3), (-1, 3),  1),
        ("BOTTOMPADDING", (0, 3), (-1, 3),  5),
        # alignment
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("ALIGN",         (1, 0), (1, 0),   "RIGHT"),
    ]))
    return card


def build_pdf_report(
    *,
    evaluation: CompanyEvaluation,
    company_name: str,
    ticker: str,
    meta: dict,
    purification: dict | None,
    generated_on: str,
    audit: dict | None = None,
) -> bytes:
    """Render a branded, educational PDF and return the bytes."""
    buffer  = BytesIO()
    doc     = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        title=f"Sharia Scope — {company_name}",
    )
    S = _build_styles()
    story: list = []

    is_compliant    = evaluation.status == "compliant"
    is_nc           = evaluation.status == "non_compliant"
    verdict_bg      = _PASS if is_compliant else (_FAIL if is_nc else _WARN)
    verdict_label   = evaluation.status_label or ("Compliant" if is_compliant else "Non-Compliant")
    period          = meta.get("period", "—") or "—"
    source          = meta.get("source", "—") or "—"
    currency        = meta.get("currency_unit", "—") or "—"

    # ── 1. HEADER ──────────────────────────────────────────────────────────────
    # Full-width dark green block: eyebrow · company name · ticker + period
    # Verdict badge sits in the top-right cell.

    header_data = [
        # row 0: eyebrow label + verdict badge
        [
            _p("SHARIA SCOPE · PSX KMI SCREENING", S["lbl_gold"]),
            _p(f"<b>{esc(verdict_label).upper()}</b>", S["status_badge"]),
        ],
        # row 1: company name (spans)
        [_p(esc(company_name) or "Unnamed Company", S["h1_white"]), ""],
        # row 2: ticker · period (spans)
        [_p(f"{esc(ticker) or '—'} · {esc(period)}", S["sub_white"]), ""],
    ]
    BADGE_COL_W = 38 * mm
    header = Table(header_data, colWidths=[CONTENT_W - BADGE_COL_W, BADGE_COL_W])
    header.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1),  _GREEN_DK),
        # badge cell background
        ("BACKGROUND",    (1, 0), (1, 0),    verdict_bg),
        # span company name & ticker rows
        ("SPAN",          (0, 1), (-1, 1)),
        ("SPAN",          (0, 2), (-1, 2)),
        # padding — eyebrow row
        ("LEFTPADDING",   (0, 0), (-1, 0),   5 * mm),
        ("RIGHTPADDING",  (0, 0), (-1, 0),   0),
        ("TOPPADDING",    (0, 0), (-1, 0),   5 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, 0),   4 * mm),
        # padding — company name
        ("LEFTPADDING",   (0, 1), (-1, 1),   5 * mm),
        ("TOPPADDING",    (0, 1), (-1, 1),   0),
        ("BOTTOMPADDING", (0, 1), (-1, 1),   1),
        # padding — ticker row
        ("LEFTPADDING",   (0, 2), (-1, 2),   5 * mm),
        ("TOPPADDING",    (0, 2), (-1, 2),   0),
        ("BOTTOMPADDING", (0, 2), (-1, 2),   5 * mm),
        # badge cell alignment
        ("VALIGN",        (1, 0), (1, 0),    "MIDDLE"),
        ("ALIGN",         (1, 0), (1, 0),    "CENTER"),
    ]))
    story.append(header)

    # ── 2. METADATA STRIP ──────────────────────────────────────────────────────
    _months = {12: "Full year", 9: "Nine months", 6: "Half year", 3: "One quarter"}
    try:
        income_basis = _months.get(int(meta.get("reporting_period_months") or 0), "—")
    except (ValueError, TypeError):
        income_basis = "—"

    price_asof = meta.get("market_price_as_of", "") or ""
    if price_asof:
        src = meta.get("market_price_source", "") or "market"
        price_asof = f"{price_asof} · {src}"

    def _lbl(t): return _p(t, S["lbl"])
    def _val(t): return _p(esc(t) or "—", S["meta_val"])

    meta_rows = [
        [_lbl("REVIEW PERIOD"), _val(period),
         _lbl("SOURCE"),         _val(source),
         _lbl("GENERATED"),      _val(generated_on or "—")],
        [_lbl("CURRENCY"),       _val(currency),
         _lbl("INCOME BASIS"),   _val(income_basis),
         _lbl("PRICE AS OF"),    _val(price_asof or "—")],
    ]
    meta_table = Table(
        meta_rows,
        colWidths=[24*mm, 36*mm, 20*mm, 42*mm, 22*mm, 30*mm],
    )
    meta_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), _BG),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW",     (0, 0), (-1, 0),  0.4, _BORDER),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 10))

    # ── 3. SCREENING RESULTS ───────────────────────────────────────────────────
    metrics      = evaluation.metric_results
    pass_count   = sum(1 for m in metrics if m.passed is True)
    total_count  = len(metrics)
    score_sty    = S["pass_count"] if is_compliant else S["fail_count"]

    section_row = Table(
        [[_p("<b>Screening Results</b>", S["h2"]),
          _p(f"<b>{pass_count}/{total_count}</b> tests passed", score_sty)]],
        colWidths=[CONTENT_W * 0.55, CONTENT_W * 0.45],
    )
    section_row.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "BOTTOM"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(section_row)

    if metrics:
        # Build 3-column card grid
        GAP     = 1.5 * mm
        CARD_W  = (CONTENT_W - 2 * GAP) / 3   # ≈ 57 mm
        INNER_W = CARD_W - 2 * GAP             # inner content after outer cell padding

        cards  = [_ratio_card(m, S, INNER_W) for m in metrics]
        rows   = []
        for i in range(0, len(cards), 3):
            batch = cards[i:i+3]
            while len(batch) < 3:
                batch.append("")
            rows.append(batch)

        grid = Table(rows, colWidths=[CARD_W, CARD_W, CARD_W])
        grid.setStyle(TableStyle([
            ("LEFTPADDING",   (0, 0), (-1, -1), GAP),
            ("RIGHTPADDING",  (0, 0), (-1, -1), GAP),
            ("TOPPADDING",    (0, 0), (-1, -1), GAP),
            ("BOTTOMPADDING", (0, 0), (-1, -1), GAP),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(grid)
    else:
        story.append(_p(
            "Ratio screening skipped — business activity is not Shariah-compliant.",
            S["body_mute"],
        ))

    # Failure reasons
    if evaluation.failure_reasons:
        story.append(Spacer(1, 6))
        reasons_data = [[
            _p(
                "   ".join(
                    f"<font color='#B42318'><b>✗</b></font> {esc(r)}"
                    for r in evaluation.failure_reasons
                ),
                ParagraphStyle(
                    "reasons",
                    parent=S["body"],
                    fontSize=8.5,
                    leading=13,
                    leftIndent=0,
                    rightIndent=0,
                ),
            )
        ]]
        reasons_table = Table(reasons_data, colWidths=[CONTENT_W])
        reasons_table.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), _FAIL_BG),
            ("LEFTPADDING",   (0, 0), (-1, -1), 5 * mm),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 5 * mm),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LINEABOVE",     (0, 0), (-1, 0),  2, _FAIL),
        ]))
        story.append(reasons_table)

    # ── 4. PURIFICATION ────────────────────────────────────────────────────────
    if purification:
        story.append(Spacer(1, 12))
        story.append(HRFlowable(width="100%", thickness=0.5, color=_BORDER))
        story.append(Spacer(1, 8))

        pur_header = Table(
            [[_p("<b>Dividend Purification</b>", S["h2"]),
              _p(
                  "Compliant investors must donate this share of dividends to charity.",
                  S["small"],
              )]],
            colWidths=[56 * mm, CONTENT_W - 56 * mm],
        )
        pur_header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM")]))
        story.append(pur_header)
        story.append(Spacer(1, 4))

        pur_rows = [
            [_lbl("NC INCOME RATIO"),   _val(purification.get("income_ratio", "—")),
             _lbl("DIVIDEND/SHARE"),    _val(purification.get("dps", "—")),
             _lbl("PURIFICATION/SHARE"),
             _p(f"<b>{esc(purification.get('purification_amount','—'))}</b>",
                ParagraphStyle("pu", parent=S["body"], fontSize=12,
                               textColor=_WARN, fontName="Helvetica-Bold", leading=15))],
            [_lbl("SHARES HELD"),       _val(purification.get("shares", "—")),
             _lbl("TOTAL DIVIDEND"),    _val(purification.get("total_dividend", "—")),
             "", ""],
        ]
        pur_table = Table(
            pur_rows,
            colWidths=[28*mm, 28*mm, 28*mm, 30*mm, 34*mm, 26*mm],
        )
        pur_table.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), _GOLD_LT),
            ("LINEABOVE",     (0, 0), (-1, 0),  2,   _GOLD),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(pur_table)

    # ── 5. METHODOLOGY ─────────────────────────────────────────────────────────
    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", thickness=0.5, color=_BORDER))
    story.append(Spacer(1, 8))
    story.append(_p("<b>Understanding These Results</b>", S["h2"]))
    story.append(_p(
        "Sharia Scope applies <b>AAOIFI Shariah Standard No. 21</b> thresholds as adopted by the "
        "Pakistan Stock Exchange for its KMI index. All five tests must pass for a stock to be "
        "deemed Shariah-compliant. Thresholds use trailing twelve-month (annualised) financials.",
        S["body_mute"],
    ))
    story.append(Spacer(1, 6))

    METHOD = [
        ("1", "Business Activity",
         "Core operations must be halal. Banks, conventional insurance, alcohol, tobacco, weapons, "
         "and adult content are excluded outright — ratios are not assessed if this test fails."),
        ("2", "Debt Ratio  <  37%",
         "Interest-bearing debt (IBD) as a share of total assets. IBD includes bank loans, "
         "conventional bonds, overdrafts, and finance lease liabilities. Debt-heavy "
         "balance sheets depend on Riba-based financing."),
        ("3", "Investment Ratio  <  33%",
         "Non-compliant investments (equity stakes in prohibited companies, conventional funds) "
         "as a share of total assets. A compliant company should not derive significant "
         "value from haram enterprises."),
        ("4", "Income Ratio  <  5%",
         "Non-compliant income (bank interest, late-payment penalties) as a share of total "
         "revenue. Compliant companies that earn minor NC income must purify the "
         "proportional dividend amount by donating it to charity."),
        ("5", "Illiquid Assets  ≥  25%",
         "Property, plant & equipment; intangibles; and inventories as a share of total "
         "assets. Prevents money-trading: if a company's balance sheet is mostly cash and "
         "receivables, its shares resemble selling money for money (Bai al-dayn)."),
    ]
    num_sty = ParagraphStyle("num", parent=S["lbl"], fontSize=8, textColor=_GREEN,
                              fontName="Helvetica-Bold", alignment=TA_CENTER)
    method_rows = [
        [_p(n, num_sty), _p(f"<b>{esc(title)}</b>", S["h3"]), _p(esc(body), S["small"])]
        for n, title, body in METHOD
    ]
    method_table = Table(method_rows, colWidths=[7 * mm, 44 * mm, CONTENT_W - 51 * mm])
    method_table.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("BACKGROUND",    (0, 0), (0, -1),  _BG),
        ("LINEBELOW",     (0, 0), (-1, -2), 0.3, _BORDER),
    ]))
    story.append(method_table)

    # ── 6. GLOSSARY ────────────────────────────────────────────────────────────
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=_BORDER))
    story.append(Spacer(1, 8))
    story.append(_p("<b>Glossary</b>", S["h2"]))

    TERMS = [
        ("Riba",
         "Interest or usury — any unjustified increment in exchange or loan transactions. "
         "Prohibited by Shariah (Quran 2:275). The central reason for screening out debt-heavy "
         "and interest-earning companies."),
        ("AAOIFI",
         "Accounting and Auditing Organisation for Islamic Financial Institutions. "
         "Sets the global Shariah screening standards used by the PSX KMI index."),
        ("KMI / PSX Shariah Index",
         "Karachi Meezan Index. Pakistan's premier Shariah-compliant equity index, "
         "screened semi-annually by PSX using AAOIFI criteria."),
        ("Interest-bearing debt (IBD)",
         "Financing that carries a fixed or variable interest obligation: bank loans, "
         "overdrafts, conventional bonds, and finance lease liabilities."),
        ("Illiquid assets",
         "Property, plant & equipment; intangibles; long-term investments; inventories. "
         "Tangible assets not immediately convertible to cash."),
        ("Bai al-dayn",
         "Prohibited 'sale of debt'. If assets are predominantly cash and receivables, "
         "trading the company's shares resembles selling money for money."),
        ("Purification",
         "When a compliant company earns minor NC income (e.g. bank interest on cash balances), "
         "shareholders must donate the proportional share of dividends to charity."),
        ("NLA per share",
         "(Total assets − Illiquid assets − Total liabilities) ÷ shares. "
         "Compared against market price; if NLA > price, the stock may fail the Bai al-dayn test."),
    ]

    COL_A_W = (CONTENT_W - 8 * mm) / 2
    TERM_W  = 26 * mm
    DEF_W   = COL_A_W - TERM_W

    def _gentry(term, defn):
        return [_p(esc(term), S["gterm"]), _p(esc(defn), S["gdef"])]

    half = (len(TERMS) + 1) // 2
    col1 = TERMS[:half]
    col2 = TERMS[half:]

    # Build equal-length column lists
    while len(col2) < len(col1):
        col2.append(("", ""))

    gl_rows = []
    for (t1, d1), (t2, d2) in zip(col1, col2):
        gl_rows.append(
            _gentry(t1, d1) + [""] + _gentry(t2, d2)
        )

    gl_table = Table(
        gl_rows,
        colWidths=[TERM_W, DEF_W, 8 * mm, TERM_W, DEF_W],
    )
    gl_table.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 2),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 2),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW",     (0, 0), (-1, -2), 0.3, _BORDER),
        ("TEXTCOLOR",     (0, 0), (0, -1),  _GREEN),
        ("TEXTCOLOR",     (3, 0), (3, -1),  _GREEN),
        # subtle divider between columns
        ("LINEBEFORE",    (3, 0), (3, -1),  0.3, _BORDER),
    ]))
    story.append(gl_table)

    # ── 7. FOOTER ──────────────────────────────────────────────────────────────
    story.append(Spacer(1, 14))
    story.append(HRFlowable(width="100%", thickness=0.5, color=_BORDER))
    story.append(Spacer(1, 4))

    footer = Table(
        [[
            _p("<b>Sharia Scope</b>", S["brand_link"]),
            _p(
                "Educational tool — not investment advice, a fatwa, or an official PSX/KMI service. "
                "Verdicts are computed from the entered figures using published AAOIFI thresholds. "
                "Verify every number against audited financial statements before making investment decisions.",
                ParagraphStyle("disc_r", parent=S["disclaimer"], alignment=TA_RIGHT),
            ),
        ]],
        colWidths=[40 * mm, CONTENT_W - 40 * mm],
    )
    footer.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story.append(footer)

    doc.build(story)
    return buffer.getvalue()


def build_purification_summary(
    *,
    evaluation: CompanyEvaluation,
    company_name: str,
    ticker: str,
    meta: dict,
    purification: dict,
    generated_on: str,
) -> bytes:
    """A compact, single-page purification summary for an individual investor.

    Unlike the full compliance report, this is NOT persisted and contains the
    investor's personal dividend figures: a brief metrics recap plus the
    dividend-purification calculation.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=MARGIN, bottomMargin=MARGIN, leftMargin=MARGIN, rightMargin=MARGIN,
        title=f"Sharia Scope — Purification Summary — {company_name}",
    )
    S = _build_styles()
    story: list = []
    period = meta.get("period", "—") or "—"

    # ── Header band ──────────────────────────────────────────────────────────
    header = Table(
        [
            [_p("SHARIA SCOPE · PURIFICATION SUMMARY", S["lbl_gold"])],
            [_p(esc(company_name) or "Unnamed Company", S["h1_white"])],
            [_p(f"{esc(ticker) or '—'} · {esc(period)}", S["sub_white"])],
        ],
        colWidths=[CONTENT_W],
    )
    header.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), _GREEN_DK),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5 * mm),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5 * mm),
        ("TOPPADDING",    (0, 0), (0, 0),   5 * mm),
        ("BOTTOMPADDING", (0, 0), (0, 0),   1),
        ("TOPPADDING",    (0, 1), (0, 1),   0),
        ("BOTTOMPADDING", (0, 1), (0, 1),   1),
        ("TOPPADDING",    (0, 2), (0, 2),   0),
        ("BOTTOMPADDING", (0, 2), (0, 2),   5 * mm),
    ]))
    story.append(header)
    story.append(Spacer(1, 10))

    # ── Metrics recap (compact table) ────────────────────────────────────────
    metrics    = evaluation.metric_results
    pass_count = sum(1 for m in metrics if m.passed is True)
    verdict    = evaluation.status_label or ("Compliant" if evaluation.status == "compliant" else "Non-Compliant")
    story.append(Table(
        [[_p("<b>Compliance Recap</b>", S["h2"]),
          _p(f"<b>{esc(verdict)}</b> · {pass_count}/{len(metrics)} tests passed", S["pass_count"] if evaluation.status == "compliant" else S["fail_count"])]],
        colWidths=[CONTENT_W * 0.5, CONTENT_W * 0.5],
    ))

    res_pass = ParagraphStyle("rp", parent=S["body"], textColor=_PASS, fontName="Helvetica-Bold", alignment=TA_RIGHT)
    res_fail = ParagraphStyle("rf", parent=S["body"], textColor=_FAIL, fontName="Helvetica-Bold", alignment=TA_RIGHT)
    res_neu  = ParagraphStyle("rn", parent=S["body"], textColor=_INK_4, fontName="Helvetica-Bold", alignment=TA_RIGHT)
    val_r    = ParagraphStyle("vr", parent=S["meta_val"], alignment=TA_RIGHT)

    rows = [[_p("METRIC", S["lbl"]), _p("VALUE", S["lbl"]), _p("THRESHOLD", S["lbl"]),
             _p("RESULT", ParagraphStyle("hr", parent=S["lbl"], alignment=TA_RIGHT))]]
    for m in metrics:
        rsty = res_pass if m.passed is True else (res_fail if m.passed is False else res_neu)
        rows.append([
            _p(esc(m.label), S["body"]),
            _p(esc(_fmt_metric_value(m)), val_r),
            _p(esc(m.threshold), S["small"]),
            _p(esc(_result_text(m)), rsty),
        ])
    recap = Table(rows, colWidths=[CONTENT_W - 100 * mm, 28 * mm, 50 * mm, 22 * mm])
    recap.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("BACKGROUND",    (0, 0), (-1, 0),  _BG),
        ("LINEBELOW",     (0, 0), (-1, -1), 0.3, _BORDER),
        ("ALIGN",         (1, 0), (1, -1),  "RIGHT"),
        ("ALIGN",         (3, 0), (3, -1),  "RIGHT"),
    ]))
    story.append(recap)

    # ── Purification block ───────────────────────────────────────────────────
    story.append(Spacer(1, 14))
    story.append(_p("<b>Your Dividend Purification</b>", S["h2"]))
    story.append(_p(
        "The proportion of your dividend income that came from the company's "
        "non-compliant income — donate this amount to charity to purify the investment.",
        S["body_mute"],
    ))
    story.append(Spacer(1, 6))

    big_amt = ParagraphStyle("pu", parent=S["body"], fontSize=13, textColor=_WARN,
                             fontName="Helvetica-Bold", leading=16)
    pur_rows = [
        [_lbl_(S, "NC INCOME RATIO"),   _val_(S, purification.get("income_ratio")),
         _lbl_(S, "DIVIDEND / SHARE"),  _val_(S, purification.get("dps")),
         _lbl_(S, "PURIFICATION / SHARE"),
         _p(f"<b>{esc(purification.get('purification_amount') or '—')}</b>", big_amt)],
        [_lbl_(S, "SHARES HELD"),       _val_(S, purification.get("shares")),
         _lbl_(S, "TOTAL DIVIDEND"),    _val_(S, purification.get("total_dividend")),
         _lbl_(S, "TOTAL PURIFICATION"),
         _p(f"<b>{esc(purification.get('total_purification') or '—')}</b>", big_amt)],
    ]
    pur_table = Table(pur_rows, colWidths=[30*mm, 24*mm, 30*mm, 24*mm, 36*mm, 30*mm])
    pur_table.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), _GOLD_LT),
        ("LINEABOVE",     (0, 0), (-1, 0),  2,   _GOLD),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(pur_table)

    # ── Footer ───────────────────────────────────────────────────────────────
    story.append(Spacer(1, 16))
    story.append(HRFlowable(width="100%", thickness=0.5, color=_BORDER))
    story.append(Spacer(1, 4))
    story.append(Table(
        [[_p("<b>Sharia Scope</b>", S["brand_link"]),
          _p(f"Generated {esc(generated_on)} · Personal purification estimate — not investment advice or a fatwa. "
             "Figures are based on the dividend amounts you entered.",
             ParagraphStyle("disc_r", parent=S["disclaimer"], alignment=TA_RIGHT))]],
        colWidths=[40 * mm, CONTENT_W - 40 * mm],
    ))

    doc.build(story)
    return buffer.getvalue()


def _lbl_(S: dict, text: str):
    return _p(esc(text), S["lbl"])


def _val_(S: dict, text) -> Paragraph:
    return _p(esc(text) if (text not in (None, "")) else "—", S["meta_val"])


def _fmt_metric_value(metric: MetricResult) -> str:
    if metric.key == "net_liquid_assets_ratio":
        return format_number(metric.value)
    return format_percent(metric.value)


def _result_text(metric: MetricResult) -> str:
    if metric.passed is True:
        return "Pass"
    if metric.passed is False:
        return "Fail"
    return "No data"
