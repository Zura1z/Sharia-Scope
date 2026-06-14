"""Generate an institutional-grade PDF compliance tear-sheet with reportlab."""

from __future__ import annotations

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
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

_STATUS_COLORS = {
    "compliant": colors.HexColor("#0f7a45"),
    "non_compliant": colors.HexColor("#a61b1b"),
    "review": colors.HexColor("#9a6a00"),
}
_INK = colors.HexColor("#1f2430")
_MUTE = colors.HexColor("#5b6472")
_LINE = colors.HexColor("#d4d8e0")


def esc(value: object) -> str:
    """XML-escape any text before it reaches a ReportLab Paragraph.

    Without this, a company name or note containing ``<`` or ``&`` (e.g.
    'Alpha <b broken & Co') is parsed as markup and crashes PDF generation.
    """
    return escape(str(value)) if value is not None else ""


def build_pdf_report(
    *,
    evaluation: CompanyEvaluation,
    company_name: str,
    ticker: str,
    meta: dict[str, str],
    purification: dict[str, str] | None,
    generated_on: str,
    audit: dict | None = None,
) -> bytes:
    """Render a one-page tear-sheet and return the PDF bytes."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title=f"Sharia Scope — {company_name}",
    )
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Title"], fontSize=20, textColor=_INK, spaceAfter=2, alignment=TA_LEFT)
    sub = ParagraphStyle("Sub", parent=styles["Normal"], fontSize=10, textColor=_MUTE)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, textColor=_INK, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=9.5, textColor=_INK, leading=14)
    small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=8, textColor=_MUTE, leading=11)
    meta_label = ParagraphStyle("MetaL", parent=styles["Normal"], fontSize=8.5, textColor=_MUTE, leading=11)
    meta_value = ParagraphStyle("MetaV", parent=styles["Normal"], fontSize=8.5, textColor=_INK, leading=11)

    status_color = _STATUS_COLORS.get(evaluation.status, _MUTE)
    story: list = []

    story.append(Paragraph("Sharia Scope — Compliance Tear-Sheet", sub))
    story.append(Paragraph(esc(company_name) or "Unnamed company", h1))
    story.append(Paragraph(f"Ticker: {esc(ticker) or '—'}", sub))
    story.append(Spacer(1, 6))

    badge = Table(
        [[Paragraph(f"<b>{esc(evaluation.status_label)}</b>", ParagraphStyle("B", parent=body, textColor=colors.white, fontSize=11))]],
        colWidths=[None],
    )
    badge.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), status_color),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(badge)
    story.append(Spacer(1, 10))

    # Metadata grid — values are wrapped Paragraphs so long text never overlaps.
    def lbl(text: str):
        return Paragraph(esc(text), meta_label)

    def val(text: str):
        return Paragraph(esc(text) or "—", meta_value)

    _months = {12: "Full year (12m)", 9: "Nine months (Q3)", 6: "Half year (6m)", 3: "One quarter (3m)"}
    income_basis = _months.get(meta.get("reporting_period_months"), "")
    price_asof = meta.get("market_price_as_of", "")
    if price_asof:
        price_asof = f"{price_asof} · {meta.get('market_price_source', '') or 'market'}"
    meta_rows = [
        [lbl("Review period"), val(meta.get("period", "")), lbl("Source"), val(meta.get("source", ""))],
        [lbl("Currency unit"), val(meta.get("currency_unit", "")), lbl("Generated"), val(generated_on)],
        [lbl("Income basis"), val(income_basis), lbl("Price as of"), val(price_asof)],
    ]
    meta_table = Table(meta_rows, colWidths=[26 * mm, 56 * mm, 22 * mm, 56 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=0.6, color=_LINE))

    # Ratio dashboard
    story.append(Paragraph("Screening Ratios", h2))
    if evaluation.metric_results:
        data = [["Metric", "Value", "Threshold", "Result"]]
        styling = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f3f8")),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("TEXTCOLOR", (0, 0), (-1, 0), _MUTE),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, _LINE),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        for i, m in enumerate(evaluation.metric_results, start=1):
            data.append([Paragraph(esc(m.label), body), _fmt_metric_value(m), m.threshold, _result_text(m)])
            cell = (3, i)
            if m.passed is True:
                styling.append(("TEXTCOLOR", cell, cell, _STATUS_COLORS["compliant"]))
            elif m.passed is False:
                styling.append(("TEXTCOLOR", cell, cell, _STATUS_COLORS["non_compliant"]))
            else:
                styling.append(("TEXTCOLOR", cell, cell, _STATUS_COLORS["review"]))
        table = Table(data, colWidths=[58 * mm, 30 * mm, 42 * mm, 32 * mm])
        table.setStyle(TableStyle(styling))
        story.append(table)
    else:
        story.append(Paragraph("Ratio screening skipped (non-compliant by business nature).", body))

    # Reason for status
    story.append(Paragraph("Reason for Status", h2))
    if evaluation.failure_reasons:
        for reason in evaluation.failure_reasons:
            story.append(Paragraph(f"• {esc(reason)}", body))
    else:
        story.append(Paragraph("All screening thresholds are met.", body))

    # Purification
    if purification:
        story.append(Paragraph("Dividend Purification", h2))
        pur_rows = [
            ["Shares owned", purification.get("shares", "—")],
            ["Dividend per share", purification.get("dps", "—")],
            ["Non-compliant income ratio used", purification.get("income_ratio", "—")],
            ["Total dividend", purification.get("total_dividend", "—")],
            ["Amount to purify (donate)", purification.get("purification_amount", "—")],
        ]
        pur_table = Table(pur_rows, colWidths=[70 * mm, 50 * mm])
        pur_table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("TEXTCOLOR", (0, 0), (0, -1), _MUTE),
                    ("TEXTCOLOR", (1, 0), (1, -1), _INK),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.3, _LINE),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(pur_table)

    # Notes
    if evaluation.notes:
        story.append(Paragraph("Source Notes", h2))
        story.append(Paragraph(esc(evaluation.notes), body))

    # Audit provenance + evidence appendix
    if audit:
        story.append(Paragraph("Audit", h2))
        audit_rows = [
            ["Data source", audit.get("data_source", "—")],
            ["AI model", audit.get("model") or "—"],
            ["Verified", {True: "Yes", False: "No (draft)"}.get(audit.get("verified"), "N/A")],
            ["Rule version", audit.get("rule_version", "—")],
            ["App version", audit.get("app_version", "—")],
        ]
        atab = Table([[lbl, str(val)] for lbl, val in audit_rows], colWidths=[45 * mm, 120 * mm])
        atab.setStyle(
            TableStyle([
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("TEXTCOLOR", (0, 0), (0, -1), _MUTE),
                ("TEXTCOLOR", (1, 0), (1, -1), _INK),
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, _LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ])
        )
        story.append(atab)

        evidence = audit.get("evidence") or []
        if evidence:
            story.append(Paragraph("Extraction Evidence", h2))
            edata = [["Field", "Value", "Page", "Conf.", "Source label"]]
            for e in evidence:
                edata.append([
                    esc(str(e.get("field", ""))),
                    esc(str(e.get("value", ""))),
                    esc(str(e.get("source_page", ""))),
                    esc(str(e.get("confidence", ""))),
                    Paragraph(esc(str(e.get("source_label", ""))), small),
                ])
            etab = Table(edata, colWidths=[38 * mm, 24 * mm, 18 * mm, 16 * mm, 69 * mm])
            etab.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f3f8")),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                    ("TEXTCOLOR", (0, 0), (-1, 0), _MUTE),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.3, _LINE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ])
            )
            story.append(etab)

    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.6, color=_LINE))
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            "Educational prototype. Verdicts are computed from the entered/extracted figures using the "
            "PSX/KMI screening thresholds and are not investment advice, a religious ruling, or an official "
            "PSX/KMI screening service. Verify every figure against audited financial statements.",
            small,
        )
    )

    doc.build(story)
    return buffer.getvalue()


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
