from __future__ import annotations

import pandas as pd
import plotly.express as px
import psycopg2
import psycopg2.extras
import streamlit as st

try:
    from .db.connection import (
        get_psycopg2_connection_kwargs,
        has_database_config,
        test_database_connection,
    )
    from .filters import AdvancedFilterPanel
    from .sidebar import render_sidebar
    from .ui import (
        CHART_COLORS,
        apply_global_styles,
        format_count,
        format_currency,
        format_percent,
        format_timestamp,
        render_empty_state,
        render_filter_summary,
        render_metric_row,
        render_page_header,
        render_section_intro,
        style_figure,
    )
except ImportError:
    from db.connection import (
        get_psycopg2_connection_kwargs,
        has_database_config,
        test_database_connection,
    )
    from filters import AdvancedFilterPanel
    from sidebar import render_sidebar
    from ui import (
        CHART_COLORS,
        apply_global_styles,
        format_count,
        format_currency,
        format_percent,
        format_timestamp,
        render_empty_state,
        render_filter_summary,
        render_metric_row,
        render_page_header,
        render_section_intro,
        style_figure,
    )

try:
    from .i18n import t
except ImportError:
    from i18n import t


st.set_page_config(
    page_title="cocoScraper",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_global_styles()


def get_page_meta() -> dict[str, dict[str, str]]:
    return {
        "Dashboard": {
            "eyebrow": t("page_dashboard_eyebrow"),
            "title": t("page_dashboard_title"),
            "description": t("page_dashboard_desc"),
            "sidebar": t("page_dashboard_sidebar"),
        },
        "Comparison": {
            "eyebrow": t("page_comparison_eyebrow"),
            "title": t("page_comparison_title"),
            "description": t("page_comparison_desc"),
            "sidebar": t("page_comparison_sidebar"),
        },
        "History": {
            "eyebrow": t("page_history_eyebrow"),
            "title": t("page_history_title"),
            "description": t("page_history_desc"),
            "sidebar": t("page_history_sidebar"),
        },
        "Logs": {
            "eyebrow": t("page_logs_eyebrow"),
            "title": t("page_logs_title"),
            "description": t("page_logs_desc"),
            "sidebar": t("page_logs_sidebar"),
        },
    }


@st.cache_data(ttl=120, show_spinner=False)
def query(sql: str, *args: object) -> pd.DataFrame:
    conn = psycopg2.connect(**get_psycopg2_connection_kwargs())
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(sql, args if args else None)
            rows = cursor.fetchall()
        return pd.DataFrame([dict(row) for row in rows])
    finally:
        conn.close()


def workspace_snapshot() -> dict[str, str]:
    snapshot = query(
        """
        SELECT
            COUNT(*) AS total_products,
            COUNT(DISTINCT supplier) AS supplier_count,
            MAX(last_scraped_at) AS last_scraped_at
        FROM products;
        """
    )
    if snapshot.empty:
        return {"products": "0", "suppliers": "0", "updated": t("not_available")}

    row = snapshot.iloc[0]
    return {
        "products": format_count(row.get("total_products")),
        "suppliers": format_count(row.get("supplier_count")),
        "updated": format_timestamp(row.get("last_scraped_at")),
    }


def display_table(
    dataframe: pd.DataFrame,
    *,
    supplier_columns: list[str] | None = None,
    use_style: pd.io.formats.style.Styler | None = None,
    height: int | None = None,
) -> None:
    column_config: dict[str, object] = {}
    if "price_unit" in dataframe.columns:
        column_config["price_unit"] = st.column_config.NumberColumn(format="$%.2f")
    if "price_bulk" in dataframe.columns:
        column_config["price_bulk"] = st.column_config.NumberColumn(format="$%.2f")
    if "diff_pct" in dataframe.columns:
        column_config["diff_pct"] = st.column_config.NumberColumn(format="%.2f%%")
    if supplier_columns:
        for column in supplier_columns:
            column_config[column] = st.column_config.NumberColumn(format="$%.2f")

    dataframe_kwargs: dict[str, object] = {
        "hide_index": True,
        "column_config": column_config,
        "use_container_width": True,
    }
    if height is not None:
        dataframe_kwargs["height"] = height

    st.dataframe(
        use_style if use_style is not None else dataframe,
        **dataframe_kwargs,
    )


def render_export_button(
    data: bytes,
    *,
    file_name: str,
    key: str,
    caption: str | None = None,
) -> None:
    info_col, action_col = st.columns([0.9, 0.1])
    with info_col:
        if caption:
            st.caption(caption)
    with action_col:
        st.download_button(
            t("btn_export_csv"),
            data,
            file_name=file_name,
            mime="text/csv",
            use_container_width=True,
            key=key,
        )


def render_browse_page() -> None:
    df = query(
        """
        SELECT
            product_id,
            canonical_name,
            name,
            brand,
            product_type,
            variant,
            supplier,
            category_dept,
            category_sub,
            size,
            price_unit,
            price_bulk,
            stock,
            last_scraped_at AS scraped_at
        FROM products
        ORDER BY price_unit ASC NULLS LAST, supplier ASC, name ASC;
        """
    )

    render_page_header(
        get_page_meta()["Dashboard"]["eyebrow"],
        get_page_meta()["Dashboard"]["title"],
        get_page_meta()["Dashboard"]["description"],
        context=t("dashboard_workspace_ctx", ts=format_timestamp(df['scraped_at'].max())) if not df.empty else None,
    )

    if df.empty:
        render_empty_state(t("dashboard_no_products"))
        return

    df["price_unit"] = pd.to_numeric(df["price_unit"], errors="coerce")
    df["price_bulk"] = pd.to_numeric(df["price_bulk"], errors="coerce")

    filter_panel = AdvancedFilterPanel(df)
    filtered, visible_columns, _active_filters = filter_panel.render()
    csv_data = filtered[visible_columns].to_csv(index=False).encode("utf-8")

    render_export_button(
        csv_data,
        file_name="catalog_slice.csv",
        key="dashboard_export",
        caption=t("dashboard_count", n=format_count(len(filtered))),
    )

    if filtered.empty:
        render_empty_state(t("dashboard_no_match"))
        return

    display_table(filtered[visible_columns], height=980)


def render_comparison_page() -> None:
    df = query(
        """
        SELECT
            canonical_key,
            canonical_name,
            brand,
            product_type,
            size,
            category_dept,
            category_sub,
            supplier,
            price_unit
        FROM products
        WHERE canonical_key IS NOT NULL
          AND canonical_key != '?|?|?|?'
          AND price_unit IS NOT NULL;
        """
    )

    render_page_header(
        get_page_meta()["Comparison"]["eyebrow"],
        get_page_meta()["Comparison"]["title"],
        get_page_meta()["Comparison"]["description"],
        context=t("comparison_ctx", n=format_count(df['supplier'].nunique())) if not df.empty else None,
    )

    if df.empty:
        render_empty_state(t("comparison_no_data"))
        return

    df["price_unit"] = pd.to_numeric(df["price_unit"], errors="coerce")
    if df["supplier"].nunique() < 2:
        st.info(t("comparison_need_more"))
        return

    render_section_intro(
        t("comparison_scope_eyebrow"),
        t("comparison_scope_title"),
        t("comparison_scope_desc"),
    )

    # Row 1: brand / product_type dropdowns
    col_brand, col_type = st.columns(2)
    with col_brand:
        brand_options = ["Todas"] + sorted(df["brand"].dropna().unique().tolist())
        selected_brand = st.selectbox("Marca", brand_options, key="comparison_brand")

    brand_df = df if selected_brand == "Todas" else df[df["brand"] == selected_brand]

    with col_type:
        type_options = ["Todos"] + sorted(brand_df["product_type"].dropna().unique().tolist())
        selected_product_type = st.selectbox("Producto", type_options, key="comparison_product_type")

    # Row 2: description text / size text
    col_desc, col_size = st.columns([0.7, 0.3])
    with col_desc:
        description_search = st.text_input("Descripción", placeholder="ej. Harina Blancaflor Leudante", key="comparison_description")
    with col_size:
        size_search = st.text_input("Tamaño", placeholder="ej. 1 kg, 500 ml", key="comparison_size")

    filtered = brand_df if selected_product_type == "Todos" else brand_df[brand_df["product_type"] == selected_product_type]

    pivot = filtered.pivot_table(
        index=[
            "canonical_key",
            "canonical_name",
            "brand",
            "product_type",
            "size",
            "category_dept",
            "category_sub",
        ],
        columns="supplier",
        values="price_unit",
        aggfunc="min",
    ).reset_index()
    pivot.columns.name = None

    fixed_columns = ["canonical_key", "canonical_name", "brand", "product_type", "size", "category_dept", "category_sub"]
    supplier_columns = [column for column in pivot.columns if column not in fixed_columns]
    # keep all products, including those from a single supplier

    if description_search:
        pivot = pivot[pivot["canonical_name"].astype(str).str.contains(description_search, case=False, na=False)]
    if size_search:
        pivot = pivot[pivot["size"].astype(str).str.contains(size_search, case=False, na=False)]

    if pivot.empty:
        render_empty_state(t("comparison_no_dept"))
        return

    price_data = pivot[supplier_columns]
    pivot["cheapest"] = price_data.idxmin(axis=1)
    pivot["max_price"] = price_data.max(axis=1)
    pivot["min_price"] = price_data.min(axis=1)
    pivot["diff_pct"] = ((pivot["max_price"] - pivot["min_price"]) / pivot["min_price"] * 100).round(2)
    pivot = pivot.sort_values("diff_pct", ascending=False)

    render_metric_row(
        [
            (t("comparison_metric_matched"), format_count(len(pivot)), t("comparison_metric_matched_note")),
            (t("comparison_metric_suppliers"), format_count(len(supplier_columns)), t("comparison_metric_suppliers_note")),
            (t("comparison_metric_spread"), format_percent(pivot["diff_pct"].median()), t("comparison_metric_spread_note")),
            (t("comparison_metric_max"), format_percent(pivot["diff_pct"].max()), t("comparison_metric_max_note")),
        ]
    )

    render_section_intro(
        t("comparison_scope_eyebrow"),
        t("comparison_matrix_title"),
        t("comparison_matrix_desc"),
    )

    export_frame = pivot.drop(columns=["max_price", "min_price", "cheapest", "canonical_key", "category_dept", "category_sub"])
    render_export_button(
        export_frame.to_csv(index=False).encode("utf-8"),
        file_name="comparison_matrix.csv",
        key="comparison_export",
    )

    def highlight_minimum(row: pd.Series) -> list[str]:
        styles = [""] * len(row)
        cheapest = pivot.loc[row.name, "cheapest"]
        if cheapest and cheapest in row.index:
            styles[row.index.get_loc(cheapest)] = "background-color: #e6f0ed; color: #1d5b50; font-weight: 600"
        return styles

    styled_frame = export_frame.style.apply(highlight_minimum, axis=1)
    display_table(export_frame, supplier_columns=supplier_columns, use_style=styled_frame)


def render_history_page() -> None:
    products_df = query(
        """
        SELECT
            product_id,
            name,
            supplier,
            brand,
            product_type,
            size
        FROM products
        WHERE product_id IS NOT NULL
          AND price_unit IS NOT NULL
        ORDER BY product_id;
        """
    )

    render_page_header(
        get_page_meta()["History"]["eyebrow"],
        get_page_meta()["History"]["title"],
        get_page_meta()["History"]["description"],
        context=t("history_ctx", n=format_count(products_df['product_id'].nunique())) if not products_df.empty else None,
    )

    if products_df.empty:
        render_empty_state(t("history_no_data"))
        return

    render_metric_row(
        [
            (t("history_metric_tracked"), format_count(products_df["product_id"].nunique()), t("history_metric_tracked_note")),
            (t("history_metric_suppliers"), format_count(products_df["supplier"].nunique()), t("history_metric_suppliers_note")),
            (t("history_metric_brands"), format_count(products_df["brand"].nunique()), t("history_metric_brands_note")),
            (t("history_metric_types"), format_count(products_df["product_type"].nunique()), t("history_metric_types_note")),
        ]
    )

    render_section_intro(
        t("history_selection_eyebrow"),
        t("history_selection_title"),
        t("history_selection_desc"),
    )

    search = st.text_input(
        t("history_search_label"),
        placeholder=t("history_search_ph"),
        key="history_search",
    )
    if search:
        mask = (
            products_df["product_id"].astype(str).str.contains(search, case=False, na=False)
            | products_df["name"].astype(str).str.contains(search, case=False, na=False)
            | products_df["brand"].astype(str).str.contains(search, case=False, na=False)
            | products_df["product_type"].astype(str).str.contains(search, case=False, na=False)
        )
        matches = products_df[mask]
    else:
        matches = products_df.head(200)

    render_filter_summary(
        [f'Search: "{search}"'] if search else [],
        t("history_showing", n=format_count(len(matches))),
    )

    if matches.empty:
        render_empty_state(t("history_no_match"))
        return

    options = {
        f"{row['product_id']} | {row['name']} | {row['supplier']}": row["product_id"]
        for _, row in matches.iterrows()
    }
    selected_labels = st.multiselect(
        t("history_products_label"),
        list(options.keys()),
        key="history_products",
        placeholder=t("history_products_ph"),
    )

    if not selected_labels:
        render_empty_state(t("history_select_prompt"))
        return

    selected_ids = [options[label] for label in selected_labels]
    placeholders = ", ".join(["%s"] * len(selected_ids))
    history = query(
        f"""
        SELECT
            h.first_seen,
            h.last_seen,
            h.price_unit,
            p.product_id,
            p.supplier,
            p.name,
            p.brand,
            p.product_type,
            p.size,
            p.price_bulk
        FROM price_history h
        JOIN products p ON p.sku = h.sku AND p.supplier = h.supplier
        WHERE p.product_id IN ({placeholders})
        ORDER BY h.first_seen ASC;
        """,
        *selected_ids,
    )

    if history.empty:
        render_empty_state(t("history_no_rows"))
        return

    history["price_unit"] = pd.to_numeric(history["price_unit"], errors="coerce")
    history["price_bulk"] = pd.to_numeric(history["price_bulk"], errors="coerce")
    history["label"] = history["supplier"].astype(str) + " | " + history["product_id"].astype(str)

    render_metric_row(
        [
            (t("history_metric_selected"), format_count(len(selected_labels)), t("history_metric_selected_note")),
            (t("history_metric_rows"), format_count(len(history)), t("history_metric_rows_note")),
            (t("history_metric_chart_suppliers"), format_count(history["supplier"].nunique()), t("history_metric_chart_suppliers_note")),
            (t("history_metric_latest"), format_timestamp(history["last_seen"].max()), t("history_metric_latest_note")),
        ]
    )

    render_section_intro(
        t("history_chart_eyebrow"),
        t("history_chart_title"),
        t("history_chart_desc"),
    )

    figure = px.line(
        history,
        x="first_seen",
        y="price_unit",
        color="label",
        markers=True,
        line_shape="hv",
        color_discrete_sequence=CHART_COLORS,
        labels={
            "first_seen": t("history_chart_date"),
            "price_unit": t("history_chart_price"),
            "label": t("history_chart_product"),
        },
    )
    figure.update_traces(marker=dict(size=7, line=dict(width=1, color="#ffffff")), line=dict(width=2.4))
    figure.update_yaxes(tickformat=".2f")
    style_figure(figure)
    st.plotly_chart(figure, use_container_width=True, config={"displayModeBar": False})

    render_section_intro(
        t("history_audit_eyebrow"),
        t("history_audit_title"),
        t("history_audit_desc"),
    )

    render_export_button(
        history[
            [
                "first_seen",
                "last_seen",
                "product_id",
                "supplier",
                "name",
                "size",
                "price_unit",
                "price_bulk",
            ]
        ].to_csv(index=False).encode("utf-8"),
        file_name="price_history.csv",
        key="history_export",
    )

    display_table(
        history[
            [
                "first_seen",
                "last_seen",
                "product_id",
                "supplier",
                "name",
                "size",
                "price_unit",
                "price_bulk",
            ]
        ]
    )


def render_logs_page() -> None:
    runs = query(
        """
        SELECT
            supplier,
            started_at,
            finished_at,
            status,
            products_scraped,
            snapshots_written,
            error_message
        FROM run_log
        ORDER BY started_at DESC
        LIMIT 20;
        """
    )

    render_page_header(
        get_page_meta()["Logs"]["eyebrow"],
        get_page_meta()["Logs"]["title"],
        get_page_meta()["Logs"]["description"],
        context=t("logs_ctx") if not runs.empty else None,
    )

    if runs.empty:
        render_empty_state(t("logs_no_data"))
        return

    zero_snapshot_runs = runs[(runs["status"] == "success") & (runs["snapshots_written"] == 0)]
    success_rate = (runs["status"] == "success").mean() * 100
    latest_activity = runs["finished_at"].combine_first(runs["started_at"]).max()

    render_metric_row(
        [
            (t("logs_metric_runs"), format_count(len(runs)), t("logs_metric_runs_note")),
            (t("logs_metric_success"), format_percent(success_rate), t("logs_metric_success_note")),
            (t("logs_metric_zero"), format_count(len(zero_snapshot_runs)), t("logs_metric_zero_note")),
            (t("logs_metric_activity"), format_timestamp(latest_activity), t("logs_metric_activity_note")),
        ]
    )

    if not zero_snapshot_runs.empty:
        st.warning(t("logs_zero_warning", n=len(zero_snapshot_runs)))

    latest_failure = runs[runs["status"] == "failed"].head(1)
    if not latest_failure.empty:
        failed_row = latest_failure.iloc[0]
        render_filter_summary(
            [t("logs_failed_supplier", supplier=failed_row['supplier']), t("logs_failed_started", ts=format_timestamp(failed_row['started_at']))],
            "No recent failed runs.",
        )

    render_section_intro(
        t("logs_table_eyebrow"),
        t("logs_table_title"),
        t("logs_table_desc"),
    )

    def color_status(value: object) -> str:
        if value == "success":
            return "background-color: #e8f2eb; color: #2f6b4f; font-weight: 600"
        if value == "failed":
            return "background-color: #f8eaec; color: #973c43; font-weight: 600"
        if value == "running":
            return "background-color: #f7ede0; color: #9b6b22; font-weight: 600"
        return ""

    display_table(runs, use_style=runs.style.map(color_status, subset=["status"]))


def main() -> None:
    if not has_database_config():
        render_sidebar(get_page_meta(), None, t("db_not_configured_sidebar"))
        render_empty_state(t("db_not_configured"))
        st.caption(t("db_not_configured_caption"))
        return

    snapshot = None
    snapshot_error = None
    try:
        snapshot = workspace_snapshot()
    except Exception as exc:
        snapshot_error = f"Workspace snapshot unavailable: {exc}"
        try:
            connected_at = test_database_connection()
        except Exception as connection_exc:
            render_sidebar(get_page_meta(), None, t("db_connection_unavailable_sidebar"))
            render_empty_state(f"Database connection failed: {connection_exc}")
            st.caption("Check the DATABASE_URL secret or environment variable and try again.")
            return

        render_sidebar(get_page_meta(), None, snapshot_error)
        error_text = str(exc).lower()
        if "does not exist" in error_text or "undefinedtable" in error_text or "relation " in error_text:
            render_empty_state(t("db_tables_not_ready"))
            st.code(t("db_schema_hint"))
        else:
            render_empty_state(t("db_query_failed", exc=exc))
        st.caption(t("db_connection_ok", ts=format_timestamp(connected_at) if connected_at else "connected"))
        return

    page = render_sidebar(get_page_meta(), snapshot, snapshot_error)

    try:
        if page == "Dashboard":
            render_browse_page()
        elif page == "Comparison":
            render_comparison_page()
        elif page == "History":
            render_history_page()
        elif page == "Logs":
            render_logs_page()
        else:
            st.error(f"Unknown page: {page}")
    except Exception as exc:
        st.error(f"Could not load data: {exc}")


main()
