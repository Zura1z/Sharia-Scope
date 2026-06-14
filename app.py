from __future__ import annotations

from datetime import date
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from allshariah_core import (
    CANONICAL_COLUMNS,
    calculate_purification,
    evaluate_company,
    format_number,
    format_percent,
    load_data,
    normalize_percent,
    status_from_evaluation,
    validate_data,
)


APP_DIR = Path(__file__).resolve().parent
DEFAULT_DATA = APP_DIR / "data" / "allshariah_template.csv"


st.set_page_config(
    page_title="AllShariah V1",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded",
)


st.markdown(
    """
    <style>
    :root {
        --as-ink: #102033;
        --as-muted: #5d6878;
        --as-line: #d9e0ea;
        --as-soft: #f5f7fa;
        --as-blue: #1f5f8b;
        --as-green: #0f7a45;
        --as-red: #a61b1b;
        --as-amber: #9a6a00;
    }
    header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
    .stDeployButton, #MainMenu, footer { display: none !important; }
    .stApp { background: #f6f8fb; }
    section[data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid var(--as-line); }
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
        color: var(--as-muted);
    }
    .main .block-container { padding-top: 1.3rem; max-width: 1220px; }
    h1, h2, h3 { letter-spacing: 0 !important; color: var(--as-ink); }
    div[data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid var(--as-line);
        border-radius: 0.45rem;
        padding: 0.75rem 0.85rem;
        min-height: 5.4rem;
    }
    div[data-testid="stMetricLabel"] p { color: var(--as-muted); font-size: 0.82rem; }
    div[data-testid="stMetricValue"] { color: var(--as-ink); font-size: 1.35rem; }
    div[data-testid="stTabs"] button p { font-weight: 700; }
    .app-header {
        background: #ffffff;
        border: 1px solid var(--as-line);
        border-radius: 0.6rem;
        padding: 1.1rem 1.25rem;
        margin-bottom: 1rem;
    }
    .app-title {
        color: var(--as-ink);
        font-size: 2rem;
        font-weight: 800;
        line-height: 1.1;
        margin: 0 0 0.35rem 0;
    }
    .app-subtitle {
        color: var(--as-muted);
        font-size: 0.98rem;
        margin: 0;
    }
    .pill-row { display: flex; flex-wrap: wrap; gap: 0.45rem; margin-top: 0.85rem; }
    .pill {
        border: 1px solid #c8d4e2;
        background: #f8fafc;
        color: #26384d;
        border-radius: 999px;
        padding: 0.22rem 0.55rem;
        font-size: 0.78rem;
        font-weight: 700;
    }
    .panel {
        background: #ffffff;
        border: 1px solid var(--as-line);
        border-radius: 0.55rem;
        padding: 1rem;
        margin-bottom: 0.9rem;
    }
    .panel-title {
        color: var(--as-ink);
        font-size: 1rem;
        font-weight: 800;
        margin-bottom: 0.35rem;
    }
    .panel-copy {
        color: var(--as-muted);
        font-size: 0.9rem;
        margin-bottom: 0;
    }
    .small-muted { color: var(--as-muted); font-size: 0.9rem; }
    .status-badge {
        color: white;
        display: inline-block;
        padding: 0.45rem 0.7rem;
        border-radius: 0.45rem;
        font-weight: 700;
        letter-spacing: 0;
    }
    .metric-card {
        border: 1px solid var(--as-line);
        border-radius: 0.45rem;
        padding: 0.85rem;
        min-height: 10.25rem;
        background: #ffffff;
    }
    .metric-title { font-weight: 800; color: var(--as-ink); margin-bottom: 0.35rem; }
    .metric-value { font-size: 1.45rem; font-weight: 800; margin-bottom: 0.2rem; color: var(--as-ink); }
    .metric-threshold { color: var(--as-muted); font-size: 0.86rem; }
    .metric-pass { color: var(--as-green); font-weight: 800; }
    .metric-fail { color: var(--as-red); font-weight: 800; }
    .metric-review { color: var(--as-amber); font-weight: 800; }
    .note-box {
        border-left: 4px solid var(--as-blue);
        background: #f4f7fb;
        padding: 0.8rem 0.9rem;
        border-radius: 0.35rem;
    }
    .empty-panel {
        border: 1px solid var(--as-line);
        border-radius: 0.5rem;
        padding: 1rem 1rem 0.9rem 1rem;
        background: #ffffff;
        min-height: 8rem;
    }
    .step-label {
        color: var(--as-ink);
        font-weight: 800;
        margin-bottom: 0.35rem;
    }
    .source-card {
        border: 1px solid var(--as-line);
        background: #ffffff;
        border-radius: 0.55rem;
        padding: 0.85rem;
        margin-bottom: 1rem;
    }
    .source-card strong { color: var(--as-ink); }
    .compact-caption { color: var(--as-muted); font-size: 0.82rem; }
    .screening-table-title {
        color: var(--as-ink);
        font-size: 1.05rem;
        font-weight: 800;
        margin: 0 0 0.35rem 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def render_header() -> None:
    st.markdown(
        """
        <div class="app-header">
            <div class="app-title">AllShariah V1</div>
            <p class="app-subtitle">Local PSX Shariah compliance screener for CSV/XLSX screening files.</p>
            <div class="pill-row">
                <span class="pill">PSX/KMI rules</span>
                <span class="pill">CSV/XLSX input</span>
                <span class="pill">No database</span>
                <span class="pill">Dividend purification</span>
                <span class="pill">Local report</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_status_badge(label: str, color: str) -> None:
    st.markdown(
        f'<span class="status-badge" style="background:{color};">{label}</span>',
        unsafe_allow_html=True,
    )


def render_metric_card(label: str, value: str, threshold: str, state: str, meaning: str) -> None:
    if state == "pass":
        class_name = "metric-pass"
        state_label = "Pass"
    elif state == "fail":
        class_name = "metric-fail"
        state_label = "Fail"
    else:
        class_name = "metric-review"
        state_label = "Needs data"
    st.markdown(
        f"""
            <div class="metric-card">
                <div class="metric-title">{label}</div>
                <div class="metric-value">{value}</div>
            <div class="metric-threshold">{threshold}</div>
                <div class="{class_name}">{state_label}</div>
                <div class="small-muted">{meaning}</div>
            </div>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner=False)
def load_default_data(path: str) -> pd.DataFrame:
    return load_data(Path(path))


def schema_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["ticker", "Required", "PSX ticker symbol."],
            ["company_name", "Required", "Company name from the screening source."],
            ["objective_status", "Optional", "Business/objective status from PSX/KMI source."],
            ["debt_ratio", "Optional numeric", "Debt ratio; accepts 4.97 or 4.97%."],
            ["investment_ratio", "Optional numeric", "Non-compliant investment ratio."],
            ["income_ratio", "Optional numeric", "Non-compliant income ratio used for purification."],
            ["illiquid_assets_ratio", "Optional numeric", "Illiquid assets ratio."],
            ["net_liquid_assets_ratio", "Optional numeric", "Net liquid assets value/check input."],
            ["share_price", "Optional numeric", "Share price used for NLA comparison."],
            ["final_shariah_status", "Required", "Compliant, Non-Compliant, NC by Nature, or Review Required."],
            ["source_document", "Optional", "PDF/file name used as source."],
            ["source_period", "Optional", "Review period from the source document."],
            ["notes", "Optional", "Exceptions, no-opinion notes, provisional rates, or other source comments."],
        ],
        columns=["Field", "Status", "Purpose"],
    )


def safe_text(value: object) -> str:
    text = "" if value is None else str(value)
    return escape(text)


def build_company_report_html(
    selected: pd.Series,
    evaluation,
    status_label: str,
    status_color: str,
    purification_result: tuple[float, float] | None,
    income_ratio: float | None,
) -> str:
    metric_rows = []
    if evaluation.metric_results:
        for result in evaluation.metric_results:
            if result.key == "net_liquid_assets_ratio":
                value = format_number(result.value)
            else:
                value = format_percent(result.value)
            if result.passed is True:
                outcome = "Pass"
                outcome_color = "#0f7a45"
            elif result.passed is False:
                outcome = "Fail"
                outcome_color = "#a61b1b"
            else:
                outcome = "Needs data"
                outcome_color = "#9a6a00"
            metric_rows.append(
                f"""
                <tr>
                    <td>{safe_text(result.label)}</td>
                    <td>{safe_text(value)}</td>
                    <td>{safe_text(result.threshold)}</td>
                    <td style="color:{outcome_color}; font-weight:700;">{outcome}</td>
                    <td>{safe_text(result.meaning)}</td>
                </tr>
                """
            )
    else:
        metric_rows.append(
            """
            <tr>
                <td colspan="5">Ratio dashboard skipped for this source row.</td>
            </tr>
            """
        )

    if evaluation.failure_reasons:
        reason_items = "".join(f"<li>{safe_text(reason)}</li>" for reason in evaluation.failure_reasons)
    else:
        reason_items = "<li>No threshold breaches found in the loaded row.</li>"

    notes = str(selected.get("notes", "") or "").strip()
    notes_html = safe_text(notes) if notes else "No special source notes for this row."

    if purification_result is None:
        purification_html = """
        <p>Purification was not calculated because the non-compliant income ratio is missing.</p>
        """
    else:
        total_dividend, purification_amount = purification_result
        if total_dividend == 0:
            purification_html = """
            <p>Purification was not calculated because shares owned or dividend per share was not entered.</p>
            """
        else:
            purification_html = f"""
            <table>
                <tr><th>Income ratio used</th><td>{safe_text(format_percent(income_ratio))}</td></tr>
                <tr><th>Total dividend</th><td>PKR {total_dividend:,.2f}</td></tr>
                <tr><th>Purification amount</th><td>PKR {purification_amount:,.2f}</td></tr>
            </table>
            """

    return f"""<!doctype html>
<html>
<head>
    <meta charset="utf-8">
    <title>AllShariah Report - {safe_text(selected.get("ticker", ""))}</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            color: #102033;
            margin: 36px;
            line-height: 1.45;
        }}
        h1 {{ margin-bottom: 4px; }}
        h2 {{ margin-top: 26px; color: #1f5f8b; }}
        .muted {{ color: #5d6878; }}
        .badge {{
            display: inline-block;
            color: #fff;
            background: {status_color};
            padding: 8px 12px;
            border-radius: 6px;
            font-weight: 700;
            margin-top: 8px;
        }}
        .summary {{
            border: 1px solid #d9e0ea;
            border-radius: 8px;
            padding: 14px;
            background: #f8fafc;
            margin-top: 18px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }}
        th, td {{
            border: 1px solid #d9e0ea;
            padding: 8px;
            vertical-align: top;
            text-align: left;
        }}
        th {{ background: #f1f5f9; }}
        .note {{
            border-left: 4px solid #1f5f8b;
            background: #f4f7fb;
            padding: 10px 12px;
        }}
        .disclaimer {{
            margin-top: 28px;
            font-size: 12px;
            color: #5d6878;
            border-top: 1px solid #d9e0ea;
            padding-top: 12px;
        }}
        @media print {{
            body {{ margin: 20mm; }}
        }}
    </style>
</head>
<body>
    <h1>AllShariah Compliance Report</h1>
    <div class="muted">Generated locally on {date.today().isoformat()}</div>

    <div class="summary">
        <h2>{safe_text(selected.get("company_name", ""))}</h2>
        <div class="muted">Ticker: {safe_text(selected.get("ticker", ""))}</div>
        <div class="badge">{safe_text(status_label)}</div>
        <table>
            <tr><th>Source status</th><td>{safe_text(selected.get("final_shariah_status", "N/A"))}</td></tr>
            <tr><th>Review period</th><td>{safe_text(selected.get("source_period", "N/A"))}</td></tr>
            <tr><th>Source document</th><td>{safe_text(selected.get("source_document", "N/A"))}</td></tr>
        </table>
    </div>

    <h2>Screening Ratios</h2>
    <table>
        <tr>
            <th>Metric</th>
            <th>Value</th>
            <th>Threshold</th>
            <th>Result</th>
            <th>Meaning</th>
        </tr>
        {''.join(metric_rows)}
    </table>

    <h2>Status Reason</h2>
    <ul>{reason_items}</ul>

    <h2>Dividend Purification</h2>
    {purification_html}

    <h2>Source Notes</h2>
    <div class="note">{notes_html}</div>

    <div class="disclaimer">
        Educational prototype only. This report depends on manually prepared source data and is not
        investment advice, a religious ruling, or an official PSX/KMI screening service.
    </div>
</body>
</html>
"""


def render_start_screen() -> None:
    st.markdown(
        """
        <div class="panel">
            <div class="panel-title">No dataset loaded</div>
            <p class="panel-copy">Upload a screening file from the sidebar, or switch to bundled sample data when you want to test the workflow.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    intake, template_col = st.columns([0.9, 1.4])
    with intake:
        st.markdown(
            """
            <div class="empty-panel">
                <div class="step-label">Intake</div>
                CSV and Excel files stay local. Required identity fields are ticker, company name, and final Shariah status.
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(
            """
            <div class="empty-panel">
                <div class="step-label">Screening</div>
                Ratio checks follow the supplied PSX/KMI structure. Special source notes remain visible.
            </div>
            """,
            unsafe_allow_html=True,
        )
    with template_col:
        data_tab, rules_tab = st.tabs(["Data template", "Methodology"])
        with data_tab:
            st.dataframe(schema_dataframe(), use_container_width=True, hide_index=True)
            st.caption("Percent fields accept values like 4.97 or 4.97%.")
        with rules_tab:
            st.table(
                pd.DataFrame(
                    [
                        ["Debt ratio", "D/A < 37%"],
                        ["Non-compliant investments", "NCInv/TA < 33%"],
                        ["Non-compliant income", "NCInc/TR < 5%"],
                        ["Illiquid assets", "IA/TA >= 25%"],
                        ["Net liquid assets", "NLA < share price"],
                    ],
                    columns=["Metric", "Threshold"],
                )
            )


def render_source_card(source_label: str) -> None:
    st.markdown(
        f"""
        <div class="source-card">
            <strong>Active dataset</strong><br>
            <span class="compact-caption">{source_label}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_quality_messages(errors, warnings) -> None:
    if not errors and not warnings:
        st.success("Data structure looks usable.")
    for error in errors:
        st.error(error.message)
    for warning in warnings:
        st.warning(warning.message)


def render_company_picker(filtered: pd.DataFrame) -> str:
    st.markdown('<div class="screening-table-title">Company lookup</div>', unsafe_allow_html=True)
    selection_options = ["Choose a company..."] + filtered["search_label"].tolist()
    return st.selectbox("Select company", selection_options, index=0, label_visibility="collapsed")


def render_unselected_table(filtered: pd.DataFrame) -> None:
    st.markdown(
        """
        <div class="panel">
            <div class="panel-title">Screening universe</div>
            <p class="panel-copy">Choose a company from the lookup above to open its compliance dashboard.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.dataframe(
        filtered[
            [
                "ticker",
                "company_name",
                "final_shariah_status",
                "source_period",
                "notes",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )


render_header()

with st.sidebar:
    st.markdown("### Data")
    data_mode = st.radio(
        "Source",
        ["Upload dataset", "Sample data"],
        index=0,
        horizontal=True,
    )
    uploaded_file = None
    if data_mode == "Upload dataset":
        uploaded_file = st.file_uploader("CSV or XLSX", type=["csv", "xlsx", "xls"])
        st.caption("Files are read locally by the app.")
    else:
        st.caption("Sample rows are included only for workflow testing.")

    st.download_button(
        "Download CSV template",
        data=",".join(CANONICAL_COLUMNS) + "\n",
        file_name="allshariah_data_template.csv",
        mime="text/csv",
        use_container_width=True,
    )
    st.markdown("---")
    st.markdown("### Reference")
    st.caption("Primary: PSX/KMI PDFs. Supporting: S&P Shariah methodology for purification context.")

try:
    if uploaded_file is not None:
        data = load_data(uploaded_file)
        source_label = f"Uploaded file: {uploaded_file.name}"
    elif data_mode == "Sample data":
        data = load_default_data(str(DEFAULT_DATA))
        source_label = "Bundled sample data"
    else:
        render_start_screen()
        st.stop()
except Exception as exc:
    st.error(f"Could not load data: {exc}")
    st.stop()

errors, warnings = validate_data(data)

if errors:
    render_quality_messages(errors, warnings)
    st.stop()

if data.empty:
    st.warning("The loaded file has no rows.")
    st.stop()

render_source_card(source_label)

overview = st.columns(4)
overview[0].metric("Rows", f"{len(data):,}")
overview[1].metric("Tickers", f"{data['ticker'].nunique():,}")
overview[2].metric(
    "Compliant",
    f"{data['final_shariah_status'].str.strip().str.lower().eq('compliant').sum():,}",
)
overview[3].metric("Warnings", f"{len(warnings):,}")

screen_tab, quality_tab = st.tabs(["Screener", "Data quality"])

with quality_tab:
    render_quality_messages(errors, warnings)
    st.dataframe(data.head(50), use_container_width=True, hide_index=True)

with screen_tab:
    data["search_label"] = data.apply(
        lambda row: f"{row['ticker']} — {row['company_name']}", axis=1
    )
    control_left, control_right = st.columns([2.2, 1])
    with control_left:
        query = st.text_input(
            "Search ticker or company",
            placeholder="Search ticker or company",
            label_visibility="collapsed",
        )
    with control_right:
        status_filter = st.selectbox(
            "Filter status",
            ["All", "Compliant", "Non-Compliant", "Review Required"],
            index=0,
            label_visibility="collapsed",
        )

    filtered = data.copy()
    if query.strip():
        q = query.strip().lower()
        filtered = filtered[
            filtered["ticker"].str.lower().str.contains(q, na=False)
            | filtered["company_name"].str.lower().str.contains(q, na=False)
        ]

    if status_filter != "All":
        filtered = filtered[
            filtered["final_shariah_status"].str.lower().str.contains(
                status_filter.lower().replace("-", "[- ]"),
                regex=True,
                na=False,
            )
        ]

    if filtered.empty:
        st.warning("No companies match the current search/filter.")
        st.stop()

    selection = render_company_picker(filtered)
    if selection == "Choose a company...":
        render_unselected_table(filtered)
        st.stop()
    selected = filtered.loc[filtered["search_label"] == selection].iloc[0]
    evaluation = evaluate_company(selected)
    status_label, status_color = status_from_evaluation(evaluation)

    summary_cols = st.columns([1.2, 1, 1, 1])
    with summary_cols[0]:
        st.markdown(
            f"""
            <div class="panel">
                <div class="panel-title">{selected["company_name"]}</div>
                <p class="panel-copy">Ticker: {selected["ticker"]}</p>
                <span class="status-badge" style="background:{status_color};">{status_label}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with summary_cols[1]:
        st.metric("Source Status", selected.get("final_shariah_status", "N/A") or "N/A")
    with summary_cols[2]:
        st.metric("Review Period", selected.get("source_period", "N/A") or "N/A")
    with summary_cols[3]:
        st.metric("Source", selected.get("source_document", "N/A") or "N/A")

    st.markdown("### Ratio dashboard")
    if evaluation.metric_results:
        columns = st.columns(3)
        for index, result in enumerate(evaluation.metric_results):
            state = "review"
            if result.passed is True:
                state = "pass"
            elif result.passed is False:
                state = "fail"
            value = (
                format_number(result.value)
                if result.key in {"net_liquid_assets_ratio"}
                else format_percent(result.value)
            )
            with columns[index % 3]:
                render_metric_card(result.label, value, result.threshold, state, result.meaning)
    else:
        st.info("Ratio dashboard is skipped for NC by Nature or no-opinion rows.")

    lower_left, lower_right = st.columns([1.1, 1])
    with lower_left:
        st.markdown("### Status reason")
        if evaluation.failure_reasons:
            for reason in evaluation.failure_reasons:
                st.write(f"- {reason}")
        else:
            st.success("No threshold breaches found in the loaded row.")

        st.markdown("### Source notes")
        notes = str(selected.get("notes", "") or "").strip()
        if notes:
            st.markdown(f'<div class="note-box">{notes}</div>', unsafe_allow_html=True)
        else:
            st.caption("No special source notes for this row.")

    with lower_right:
        st.markdown("### Purification")
        income_ratio = normalize_percent(selected.get("income_ratio"))
        shares_owned = st.number_input("Shares owned", min_value=0.0, value=0.0, step=10.0)
        dividend_per_share = st.number_input(
            "Dividend per share (PKR)", min_value=0.0, value=0.0, step=1.0
        )

        calculation = calculate_purification(shares_owned, dividend_per_share, income_ratio)
        if calculation is None:
            st.warning("Purification cannot be calculated because non-compliant income ratio is missing.")
        elif shares_owned == 0 or dividend_per_share == 0:
            st.info("Enter shares owned and dividend per share.")
        else:
            total_dividend, purification_amount = calculation
            st.metric("Income Ratio Used", format_percent(income_ratio))
            st.metric("Total Dividend", f"PKR {total_dividend:,.2f}")
            st.metric("Purification Amount", f"PKR {purification_amount:,.2f}")

        st.markdown("### Report")
        report_html = build_company_report_html(
            selected,
            evaluation,
            status_label,
            status_color,
            calculation,
            income_ratio,
        )
        report_filename = f"{str(selected.get('ticker', 'company')).lower()}_allshariah_report.html"
        st.download_button(
            "Download compliance report",
            data=report_html,
            file_name=report_filename,
            mime="text/html",
            use_container_width=True,
        )
        st.caption("Open the HTML report in a browser, or print/save it as PDF.")

st.caption(
    "Educational prototype only. AllShariah V1 depends on manually prepared source data and is not "
    "investment advice, a religious ruling, or an official PSX/KMI screening service."
)
