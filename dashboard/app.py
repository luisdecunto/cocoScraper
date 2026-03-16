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


st.set_page_config(
    page_title="cocoScraper",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_global_styles()


PAGE_META = {
    "Dashboard": {
        "eyebrow": "Dashboard",
        "title": "Dashboard",
        "description": (
            "Latest supplier prices in one table."
        ),
        "sidebar": "Main catalog table with direct filtering and export.",
    },
    "Comparison": {
        "eyebrow": "Price matrix",
        "title": "Cross-supplier comparison",
        "description": (
            "Compare normalized product matches side by side, identify the cheapest supplier per row, "
            "and surface the largest price spreads."
        ),
        "sidebar": "Use canonical matches to compare supplier pricing and spot the biggest deltas worth investigating.",
    },
    "History": {
        "eyebrow": "Change tracking",
        "title": "Price history",
        "description": (
            "Overlay products from any supplier, inspect their movement over time, and verify the raw "
            "intervals behind the chart."
        ),
        "sidebar": "Search the catalog, pick the products you care about, and inspect how prices evolved over time.",
    },
    "Logs": {
        "eyebrow": "Operational view",
        "title": "Scrape run health",
        "description": (
            "Track recent scrape runs, watch for silent failures, and confirm that each supplier feed is "
            "writing snapshots as expected."
        ),
        "sidebar": "Monitor scrape activity, zero-snapshot runs, and recent failures before they affect analysis quality.",
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
        return {"products": "0", "suppliers": "0", "updated": "Not available"}

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
            "Export CSV",
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
        PAGE_META["Dashboard"]["eyebrow"],
        PAGE_META["Dashboard"]["title"],
        PAGE_META["Dashboard"]["description"],
        context=f"Workspace updated {format_timestamp(df['scraped_at'].max())}" if not df.empty else None,
    )

    if df.empty:
        render_empty_state("No products are available yet. Run at least one supplier scrape to populate the catalog.")
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
        caption=f"{format_count(len(filtered))} products",
    )

    if filtered.empty:
        render_empty_state("No products match the current filters. Reset the panel or widen the search criteria.")
        return

    display_table(filtered[visible_columns], height=980)


def render_comparison_page() -> None:
    df = query(
        """
        SELECT
            canonical_key,
            brand,
            product_type,
            size,
            category_dept,
            category_sub,
            supplier,
            price_unit
        FROM products
        WHERE canonical_key IS NOT NULL
          AND canonical_key != '?|?|?'
          AND price_unit IS NOT NULL;
        """
    )

    render_page_header(
        PAGE_META["Comparison"]["eyebrow"],
        PAGE_META["Comparison"]["title"],
        PAGE_META["Comparison"]["description"],
        context=f"{format_count(df['supplier'].nunique())} suppliers currently contribute comparable products." if not df.empty else None,
    )

    if df.empty:
        render_empty_state("No canonical product matches are available yet. Generate more normalized products before using the comparison matrix.")
        return

    df["price_unit"] = pd.to_numeric(df["price_unit"], errors="coerce")
    if df["supplier"].nunique() < 2:
        render_metric_row(
            [
                ("Matched products", format_count(df["canonical_key"].nunique()), "Comparable records with canonical keys"),
                ("Suppliers", format_count(df["supplier"].nunique()), "Distinct suppliers contributing matched products"),
                ("Median price", format_currency(df["price_unit"].median()), "Across comparable rows"),
                ("Largest dept", df["category_dept"].mode().iat[0] if not df["category_dept"].mode().empty else "n/a", "Department with the most matches"),
            ]
        )
        st.info("Add more suppliers to enable cross-supplier comparison.")
        return

    render_section_intro(
        "Scope",
        "Comparison filters",
        "Filter by department and search text to narrow matched rows.",
    )

    filter_col, search_col = st.columns([0.34, 0.66])
    with filter_col:
        department_options = ["All departments"] + sorted(df["category_dept"].dropna().unique().tolist())
        selected_department = st.selectbox(
            "Department",
            department_options,
            key="comparison_department",
        )
    with search_col:
        comparison_search = st.text_input(
            "Search rows",
            placeholder="brand, product type, size, or subcategory",
            key="comparison_search",
        )

    filtered = df if selected_department == "All departments" else df[df["category_dept"] == selected_department]

    pivot = filtered.pivot_table(
        index=[
            "canonical_key",
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

    fixed_columns = ["canonical_key", "brand", "product_type", "size", "category_dept", "category_sub"]
    supplier_columns = [column for column in pivot.columns if column not in fixed_columns]
    pivot = pivot[pivot[supplier_columns].notna().sum(axis=1) >= 2]

    if comparison_search:
        search_mask = (
            pivot["brand"].astype(str).str.contains(comparison_search, case=False, na=False)
            | pivot["product_type"].astype(str).str.contains(comparison_search, case=False, na=False)
            | pivot["size"].astype(str).str.contains(comparison_search, case=False, na=False)
            | pivot["category_sub"].astype(str).str.contains(comparison_search, case=False, na=False)
            | pivot["category_dept"].astype(str).str.contains(comparison_search, case=False, na=False)
        )
        pivot = pivot[search_mask]

    if pivot.empty:
        render_empty_state("The current department does not have products listed by at least two suppliers.")
        return

    price_data = pivot[supplier_columns]
    pivot["cheapest"] = price_data.idxmin(axis=1)
    pivot["max_price"] = price_data.max(axis=1)
    pivot["min_price"] = price_data.min(axis=1)
    pivot["diff_pct"] = ((pivot["max_price"] - pivot["min_price"]) / pivot["min_price"] * 100).round(2)
    pivot = pivot.sort_values("diff_pct", ascending=False)

    render_metric_row(
        [
            ("Matched products", format_count(len(pivot)), "Rows that appear in at least two supplier columns"),
            ("Suppliers compared", format_count(len(supplier_columns)), "Supplier columns in the current matrix"),
            ("Median spread", format_percent(pivot["diff_pct"].median()), "Typical gap between cheapest and highest price"),
            ("Largest spread", format_percent(pivot["diff_pct"].max()), "Most pronounced gap in the current slice"),
        ]
    )

    render_section_intro(
        "Matrix",
        "Cross-supplier price view",
        "The cheapest price in each row is highlighted. Sort the table or export it for offline review.",
    )

    export_frame = pivot.drop(columns=["max_price", "min_price", "cheapest", "canonical_key"])
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
        PAGE_META["History"]["eyebrow"],
        PAGE_META["History"]["title"],
        PAGE_META["History"]["description"],
        context=f"{format_count(products_df['product_id'].nunique())} products currently have trackable price history." if not products_df.empty else None,
    )

    if products_df.empty:
        render_empty_state("No products with price history are available yet. Run more scrapes before using this view.")
        return

    render_metric_row(
        [
            ("Tracked products", format_count(products_df["product_id"].nunique()), "Products with an id and a current price"),
            ("Suppliers", format_count(products_df["supplier"].nunique()), "Supplier feeds represented in the history view"),
            ("Brands", format_count(products_df["brand"].nunique()), "Distinct brands in the searchable catalog"),
            ("Types", format_count(products_df["product_type"].nunique()), "Product types available to compare"),
        ]
    )

    render_section_intro(
        "Selection",
        "Pick the products to overlay",
        "Search by product id, name, brand, or product type and compare products from any supplier on the same chart.",
    )

    search = st.text_input(
        "Search products",
        placeholder="Search by product id, name, brand, or product type",
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
        f"Showing the first {format_count(len(matches))} products until a search term is applied.",
    )

    if matches.empty:
        render_empty_state("No products matched that search. Try a broader term or clear the field.")
        return

    options = {
        f"{row['product_id']} | {row['name']} | {row['supplier']}": row["product_id"]
        for _, row in matches.iterrows()
    }
    selected_labels = st.multiselect(
        "Products",
        list(options.keys()),
        key="history_products",
        placeholder="Select one or more products to compare",
    )

    if not selected_labels:
        render_empty_state("Select one or more products to load the chart and the raw change intervals.")
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
        render_empty_state("No history rows were found for the selected products.")
        return

    history["price_unit"] = pd.to_numeric(history["price_unit"], errors="coerce")
    history["price_bulk"] = pd.to_numeric(history["price_bulk"], errors="coerce")
    history["label"] = history["supplier"].astype(str) + " | " + history["product_id"].astype(str)

    render_metric_row(
        [
            ("Selected products", format_count(len(selected_labels)), "Products currently overlaid in the chart"),
            ("History rows", format_count(len(history)), "Raw intervals available for the selection"),
            ("Suppliers in chart", format_count(history["supplier"].nunique()), "Supplier lines visible in the current comparison"),
            ("Latest event", format_timestamp(history["last_seen"].max()), "Most recent interval end in the selected set"),
        ]
    )

    render_section_intro(
        "Chart",
        "Price movement over time",
        "Each step marks the date when a product entered a new price interval. The table below keeps the raw intervals visible for audit work.",
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
            "first_seen": "Date",
            "price_unit": "Unit price",
            "label": "Product",
        },
    )
    figure.update_traces(marker=dict(size=7, line=dict(width=1, color="#ffffff")), line=dict(width=2.4))
    figure.update_yaxes(tickformat=".2f")
    style_figure(figure)
    st.plotly_chart(figure, use_container_width=True, config={"displayModeBar": False})

    render_section_intro(
        "Audit trail",
        "Raw price intervals",
        "Use the raw table when validating a single product, checking supplier behavior, or exporting evidence for a review.",
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
        PAGE_META["Logs"]["eyebrow"],
        PAGE_META["Logs"]["title"],
        PAGE_META["Logs"]["description"],
        context=f"Showing the 20 most recent run records." if not runs.empty else None,
    )

    if runs.empty:
        render_empty_state("No scrape runs have been logged yet.")
        return

    zero_snapshot_runs = runs[(runs["status"] == "success") & (runs["snapshots_written"] == 0)]
    success_rate = (runs["status"] == "success").mean() * 100
    latest_activity = runs["finished_at"].combine_first(runs["started_at"]).max()

    render_metric_row(
        [
            ("Recent runs", format_count(len(runs)), "Latest run records in the operational log"),
            ("Success rate", format_percent(success_rate), "Share of recent runs with a success status"),
            ("Zero snapshots", format_count(len(zero_snapshot_runs)), "Successful runs that wrote nothing"),
            ("Last activity", format_timestamp(latest_activity), "Most recent start or finish time"),
        ]
    )

    if not zero_snapshot_runs.empty:
        st.warning(
            f"{len(zero_snapshot_runs)} run(s) completed successfully but wrote 0 snapshots. "
            "This usually means login broke silently or the supplier page structure changed."
        )

    latest_failure = runs[runs["status"] == "failed"].head(1)
    if not latest_failure.empty:
        failed_row = latest_failure.iloc[0]
        render_filter_summary(
            [f"Latest failed supplier: {failed_row['supplier']}", f"Started: {format_timestamp(failed_row['started_at'])}"],
            "No recent failed runs.",
        )

    render_section_intro(
        "Operations",
        "Recent run log",
        "Watch this table for silent login issues, schema drift, or suppliers that stop producing snapshots.",
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
        render_sidebar(PAGE_META, None, "Database not configured yet.")
        render_empty_state(
            "Set DATABASE_URL in Streamlit secrets or in your local environment to load the dashboard."
        )
        st.caption("Local fallback is still supported via DB_HOST, DB_PORT, DB_NAME, DB_USER, and DB_PASS.")
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
            render_sidebar(PAGE_META, None, "Database connection unavailable.")
            render_empty_state(f"Database connection failed: {connection_exc}")
            st.caption("Check the DATABASE_URL secret or environment variable and try again.")
            return

        render_sidebar(PAGE_META, None, snapshot_error)
        error_text = str(exc).lower()
        if "does not exist" in error_text or "undefinedtable" in error_text or "relation " in error_text:
            render_empty_state(
                "The database is reachable, but the pricing tables are not ready yet. Initialize the schema and run at least one scrape."
            )
            st.code("python -m scraper.main db init")
        else:
            render_empty_state(f"Database query failed before the dashboard could load: {exc}")
        st.caption(
            f"Connection test succeeded with SELECT NOW(): {format_timestamp(connected_at) if connected_at else 'connected'}"
        )
        return

    page = render_sidebar(PAGE_META, snapshot, snapshot_error)

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
