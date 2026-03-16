from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Iterable, Sequence

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

try:
    from .i18n import t
except ImportError:
    from i18n import t


CHART_COLORS = [
    "#1f1f1f",
    "#2f5597",
    "#38761d",
    "#a64d79",
    "#7f6000",
    "#595959",
]


def apply_global_styles() -> None:
    st.markdown(
        """
        <style>
        @import url("https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700&display=swap");

        :root {
            --bg: #ffffff;
            --surface: #ffffff;
            --surface-2: #fafafa;
            --border: #d9d9d9;
            --border-strong: #bfbfbf;
            --text-primary: #1f1f1f;
            --text-secondary: #595959;
            --accent: #2f5597;
            --accent-light: #edf2fa;
            --primary: #1f1f1f;
            --primary-foreground: #ffffff;
            --success: #38761d;
            --success-bg: #edf6e9;
            --error: #a61c00;
            --error-bg: #fbeaea;
            --warning: #7f6000;
            --warning-bg: #fff8e6;
            --sidebar-width: 184px;
            --radius: 0px;
            --text-2xs: 0.625rem;
            --text-xs: 0.6875rem;
            --text-sm: 0.75rem;
            --text-base: 0.8125rem;
        }

        html {
            font-size: 13px;
        }

        html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"] {
            background: var(--bg);
            color: var(--text-primary);
            font-family: "Public Sans", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
            font-size: var(--text-base);
        }

        [data-testid="stHeader"],
        [data-testid="stToolbar"],
        [data-testid="stDecoration"],
        [data-testid="collapsedControl"],
        [data-testid="stSidebarCollapseButton"] {
            display: none !important;
        }

        [data-testid="block-container"] {
            max-width: none;
            padding: 0.18rem 0.35rem 0.3rem;
        }

        [data-testid="stVerticalBlock"] {
            gap: 0.46rem;
        }

        [data-testid="stHorizontalBlock"] {
            gap: 0.5rem;
        }

        [data-testid="element-container"] {
            margin-bottom: 0.18rem;
        }

        [data-testid="stSidebar"] {
            width: var(--sidebar-width) !important;
            min-width: var(--sidebar-width) !important;
            max-width: var(--sidebar-width) !important;
        }

        [data-testid="stSidebar"][aria-expanded="false"] {
            margin-left: 0 !important;
        }

        [data-testid="stSidebar"][aria-expanded="false"] > div:first-child {
            width: var(--sidebar-width) !important;
            min-width: var(--sidebar-width) !important;
            max-width: var(--sidebar-width) !important;
        }

        [data-testid="stSidebar"] > div:first-child {
            background: var(--surface);
            border-right: 1px solid var(--border);
        }

        [data-testid="stSidebarUserContent"] {
            padding-top: 0.45rem;
        }

        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"],
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] span,
        [data-testid="stSidebar"] div {
            color: var(--text-primary);
        }

        .sidebar-brand {
            padding: 0.12rem 0.72rem 0.62rem;
            border-bottom: 1px solid var(--border);
            margin: 0 0 0.62rem;
        }

        .sidebar-brand-title {
            margin: 0;
            font-size: 0.7rem;
            font-weight: 800;
            letter-spacing: 0.06em;
            text-transform: uppercase;
        }

        .sidebar-brand-subtitle {
            margin: 0.18rem 0 0;
            color: var(--text-secondary);
            font-size: var(--text-xs);
            line-height: 1.25;
        }

        .sidebar-nav {
            display: flex;
            flex-direction: column;
            gap: 0.05rem;
            padding: 0 0.3rem;
        }

        .sidebar-nav .nav-item {
            display: flex;
            align-items: center;
            gap: 0.42rem;
            margin: 0;
            padding: 0.54rem 0.62rem;
            border-left: 2px solid transparent;
            border-radius: var(--radius);
            background: transparent;
            transition: background-color 120ms ease, color 120ms ease;
            color: var(--text-secondary);
            text-decoration: none;
            font-size: var(--text-sm);
            font-weight: 600;
            line-height: 1.2;
        }

        .sidebar-nav .nav-item:hover {
            background: var(--surface-2);
        }

        .sidebar-nav .nav-item.active {
            background: var(--accent-light);
            border-left-color: var(--accent);
            color: var(--accent);
        }

        .sidebar-nav .nav-item span:last-child {
            min-width: 0;
        }

        .sidebar-nav-icon {
            width: 0.8rem;
            flex: 0 0 0.8rem;
            text-align: center;
            color: currentColor;
            font-size: 0.64rem;
            line-height: 1;
        }

        .sidebar-divider {
            height: 1px;
            background: var(--border);
            margin: 0.56rem 0.3rem;
        }

        .sidebar-meta {
            margin: 0.14rem 0.45rem 0.4rem;
            padding: 0.44rem 0.45rem;
            border: 1px solid var(--border);
            border-radius: var(--radius);
            background: var(--surface-2);
            display: grid;
            gap: 0.26rem;
        }

        .sidebar-meta-row {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 0.35rem;
            font-size: var(--text-xs);
            line-height: 1.25;
        }

        .sidebar-meta-row strong {
            color: var(--text-secondary);
            font-weight: 600;
        }

        .sidebar-footer-note {
            margin: 0.16rem 0.45rem 0.32rem;
            color: var(--text-secondary);
            font-size: var(--text-xs);
            line-height: 1.25;
        }

        h1, h2, h3, h4, h5, h6 {
            color: var(--text-primary);
            letter-spacing: 0;
            margin-bottom: 0.12rem;
            font-weight: 600;
            font-size: var(--text-sm);
            line-height: 1.15;
        }

        .page-meta {
            margin: 0.06rem 0 0.28rem;
            padding: 0;
            color: var(--text-secondary);
            font-size: var(--text-2xs);
            font-weight: 600;
            line-height: 1.25;
        }

        .section-inline {
            margin: 0.26rem 0 0.14rem;
            color: var(--text-secondary);
            font-size: var(--text-2xs);
            line-height: 1.3;
            white-space: normal;
        }

        .metric-inline {
            margin: 0.14rem 0;
            color: var(--text-secondary);
            font-size: var(--text-2xs);
            line-height: 1.3;
            white-space: normal;
            overflow: visible;
        }

        .filter-inline {
            margin: 0.14rem 0 0.22rem;
            color: var(--text-secondary);
            font-size: var(--text-2xs);
            line-height: 1.3;
            white-space: normal;
            overflow: visible;
        }

        .empty-inline {
            margin-top: 0.2rem;
            border: 1px solid var(--border);
            border-radius: var(--radius);
            background: var(--surface-2);
            color: var(--text-secondary);
            padding: 0.3rem 0.4rem;
            font-size: var(--text-xs);
            line-height: 1.25;
        }

        div[data-baseweb="input"] > div,
        div[data-baseweb="select"] > div,
        div[data-baseweb="textarea"] > div {
            border-radius: var(--radius);
            border-color: var(--border);
            background: var(--surface);
            min-height: 30px;
            box-shadow: none;
        }

        div[data-baseweb="input"] > div:hover,
        div[data-baseweb="select"] > div:hover,
        div[data-baseweb="textarea"] > div:hover {
            border-color: var(--border-strong);
        }

        div[data-baseweb="tag"] {
            border-radius: var(--radius);
            border: 1px solid var(--border);
            background: var(--surface-2);
            color: var(--text-primary);
            font-size: var(--text-2xs);
            line-height: 1.1;
        }

        .stButton > button,
        .stDownloadButton > button {
            min-height: 30px;
            border-radius: var(--radius);
            border: 1px solid var(--border);
            background: var(--surface-2);
            color: var(--text-primary);
            font-size: var(--text-2xs);
            font-weight: 600;
            line-height: 1;
            padding: 0.2rem 0.35rem;
        }

        .stButton > button:hover,
        .stDownloadButton > button:hover {
            background: var(--surface);
            border-color: var(--border-strong);
        }

        .stButton > button[kind="primary"] {
            background: var(--primary);
            border-color: var(--primary);
            color: var(--primary-foreground);
        }

        [data-testid="stExpander"] details {
            border: 1px solid var(--border);
            border-radius: var(--radius);
            background: var(--surface);
        }

        [data-testid="stExpander"] summary {
            padding: 0.45rem 0.5rem;
            font-size: var(--text-2xs);
            font-weight: 700;
            color: var(--text-primary);
        }

        [data-testid="stExpander"] summary:hover {
            background: var(--surface-2);
        }

        [data-testid="stDataFrame"] {
            border: 1px solid var(--border);
            border-radius: var(--radius);
            background: var(--surface);
            overflow: hidden;
        }

        [data-testid="stDataFrame"] [role="grid"] {
            font-size: var(--text-2xs);
        }

        div[data-testid="stAlert"] {
            border-radius: var(--radius);
            border: 1px solid var(--border);
            font-size: var(--text-xs);
            line-height: 1.2;
            padding: 0.2rem 0.35rem;
        }

        [data-testid="stWidgetLabel"] > div {
            font-size: var(--text-2xs);
            color: var(--text-primary);
            font-weight: 600;
            margin-bottom: 0.12rem;
            line-height: 1.25;
        }

        .stCaption {
            font-size: var(--text-2xs);
            color: var(--text-secondary);
        }

        @media (max-width: 1024px) {
            [data-testid="block-container"] {
                padding: 0.1rem 0.2rem 0.24rem;
            }
        }

        /* ── Mobile sidebar slide ─────────────────────────────── */
        /* collapsedControl is hidden on ALL sizes — we use our own injected button */
        [data-testid="collapsedControl"] {
            display: none !important;
        }

        @media (max-width: 768px) {
            /* Sidebar: full-height overlay that slides in from left */
            [data-testid="stSidebar"] {
                position: fixed !important;
                top: 0 !important;
                left: 0 !important;
                height: 100vh !important;
                z-index: 999999 !important;
                transform: translateX(-110%) !important;
                transition: transform 220ms cubic-bezier(0.2, 0, 0, 1) !important;
                box-shadow: 2px 0 12px rgba(0,0,0,0.18) !important;
                width: var(--sidebar-width) !important;
                min-width: var(--sidebar-width) !important;
                max-width: var(--sidebar-width) !important;
            }

            [data-testid="stSidebar"][aria-expanded="true"] {
                transform: translateX(0) !important;
            }

            /* Main content takes full width on mobile */
            [data-testid="stAppViewContainer"] > section:last-child,
            [data-testid="block-container"] {
                margin-left: 0 !important;
                width: 100% !important;
                max-width: 100% !important;
                padding-left: 0.5rem !important;
                padding-right: 0.5rem !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_page_header(
    eyebrow: str,
    title: str,
    description: str,
    context: str | None = None,
) -> None:
    _ = eyebrow
    _ = title
    _ = description
    if context:
        st.markdown(
            f'<div class="page-meta">{escape(context)}</div>',
            unsafe_allow_html=True,
        )


def render_metric_row(metrics: Sequence[tuple[str, str, str]]) -> None:
    if not metrics:
        return

    chunks = [f"{label}: {value}" for label, value, _note in metrics]
    st.markdown(
        f'<div class="metric-inline">{escape(" | ".join(chunks))}</div>',
        unsafe_allow_html=True,
    )


def render_section_intro(eyebrow: str, title: str, description: str) -> None:
    _ = eyebrow
    if not title and not description:
        return
    text = title if not description else f"{title} - {description}"
    st.markdown(
        f'<div class="section-inline">{escape(text)}</div>',
        unsafe_allow_html=True,
    )


def render_filter_summary(filters: Iterable[str], empty_label: str) -> None:
    filters_clean = [item for item in filters if item]
    label = " | ".join(filters_clean) if filters_clean else empty_label
    st.markdown(
        f'<div class="filter-inline">{escape(label)}</div>',
        unsafe_allow_html=True,
    )


def render_empty_state(message: str) -> None:
    st.markdown(
        f'<div class="empty-inline">{escape(message)}</div>',
        unsafe_allow_html=True,
    )


def style_figure(fig: go.Figure) -> go.Figure:
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#ffffff",
        margin=dict(l=8, r=8, t=4, b=8),
        font=dict(
            family='"Public Sans", "Segoe UI", "Helvetica Neue", Arial, sans-serif',
            color="#1f1f1f",
            size=9,
        ),
        legend=dict(
            title=None,
            orientation="h",
            yanchor="bottom",
            y=1.01,
            xanchor="left",
            x=0,
            font=dict(size=8),
        ),
        hoverlabel=dict(
            bgcolor="#ffffff",
            bordercolor="#d9d9d9",
            font=dict(color="#1f1f1f", size=9),
        ),
    )
    fig.update_xaxes(
        showgrid=False,
        linecolor="#d9d9d9",
        tickfont=dict(color="#595959", size=8),
        title_font=dict(color="#595959", size=8),
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor="#efefef",
        zeroline=False,
        linecolor="#d9d9d9",
        tickfont=dict(color="#595959", size=8),
        title_font=dict(color="#595959", size=8),
    )
    return fig


def format_count(value: object) -> str:
    if value is None or pd.isna(value):
        return "0"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def format_currency(value: object) -> str:
    if value is None or pd.isna(value):
        return t("no_price")
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def format_percent(value: object, decimals: int = 1) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    try:
        return f"{float(value):,.{decimals}f}%"
    except (TypeError, ValueError):
        return str(value)


def format_timestamp(value: object) -> str:
    if value is None or pd.isna(value):
        return t("not_available")

    if isinstance(value, pd.Timestamp):
        timestamp = value.to_pydatetime()
    elif isinstance(value, datetime):
        timestamp = value
    else:
        return str(value)

    return timestamp.strftime("%d %b %Y, %H:%M")
