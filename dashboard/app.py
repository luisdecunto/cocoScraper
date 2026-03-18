from __future__ import annotations

import json
import unicodedata
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import psycopg2
import psycopg2.extras
import streamlit as st
import streamlit.components.v1 as st_components
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode

try:
    from .db.connection import (
        get_psycopg2_connection_kwargs,
        has_database_config,
        test_database_connection,
    )
    from .filters import AdvancedFilterPanel, NO_STOCK_VALUES, normalize_text
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
    from filters import AdvancedFilterPanel, NO_STOCK_VALUES, normalize_text
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
        "Feedback": {
            "eyebrow": t("page_feedback_eyebrow"),
            "title": t("page_feedback_title"),
            "description": t("page_feedback_desc"),
            "sidebar": t("page_feedback_sidebar"),
        },
    }


def _fold(s: str) -> str:
    """Accent-fold + lowercase for dedup comparisons."""
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()


def _dedup_sorted(values: list[str]) -> list[str]:
    """Return unique values, collapsing accent/case variants (keep first seen)."""
    seen: dict[str, str] = {}
    for v in sorted(values):
        key = _fold(v)
        if key not in seen:
            seen[key] = v
    return sorted(seen.values())


def query(sql: str, *args: object) -> pd.DataFrame:
    conn = psycopg2.connect(**get_psycopg2_connection_kwargs())
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(sql, args if args else None)
            rows = cursor.fetchall()
        return pd.DataFrame([dict(row) for row in rows])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Cached data loaders — all heavy DB reads go through these.
# TTL=120s keeps data fresh while absorbing rapid Streamlit reruns.
# The sidebar Refresh button calls st.cache_data.clear() to force reload.
# ---------------------------------------------------------------------------
_CACHE_TTL = 600  # seconds — 10 min; reduce for fresher data, increase to cut egress


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def _load_browse_data() -> pd.DataFrame:
    return query(
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


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def _load_comparison_data() -> pd.DataFrame:
    return query(
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
            price_unit,
            price_bulk,
            stock,
            units_per_package,
            name,
            url,
            sku,
            product_id
        FROM products
        WHERE canonical_key IS NOT NULL
          AND canonical_key != '?|?|?|?';
        """
    )


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def _load_history_products() -> pd.DataFrame:
    """Only returns products that have at least one row in price_history."""
    return query(
        """
        SELECT
            p.product_id,
            p.canonical_key,
            p.canonical_name,
            p.name,
            p.supplier,
            p.brand,
            p.product_type,
            p.size,
            p.price_unit
        FROM products p
        WHERE p.canonical_key IS NOT NULL
          AND p.canonical_key != '?|?|?|?'
          AND EXISTS (
              SELECT 1 FROM price_history ph
              WHERE ph.sku = p.sku AND ph.supplier = p.supplier
          )
        ORDER BY p.canonical_name ASC, p.supplier ASC;
        """
    )


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
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


def _ensure_feedback_table() -> None:
    """Create comparison_feedback if it doesn't exist (idempotent)."""
    conn = psycopg2.connect(**get_psycopg2_connection_kwargs())
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS comparison_feedback (
                    id              BIGSERIAL    PRIMARY KEY,
                    created_at      TIMESTAMPTZ  DEFAULT NOW(),
                    canonical_names TEXT[]       NOT NULL,
                    comment         TEXT         NOT NULL
                );
            """)
        conn.commit()
    finally:
        conn.close()


def _insert_feedback(canonical_names: list[str], comment: str) -> None:
    _ensure_feedback_table()
    conn = psycopg2.connect(**get_psycopg2_connection_kwargs())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO comparison_feedback (canonical_names, comment) VALUES (%s, %s)",
                (canonical_names, comment),
            )
        conn.commit()
    finally:
        conn.close()


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def _load_feedback_log() -> pd.DataFrame:
    return query(
        """
        SELECT id, created_at, canonical_names, comment
        FROM comparison_feedback
        ORDER BY created_at DESC
        LIMIT 200;
        """
    )


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
    df = _load_browse_data()

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
    df = _load_comparison_data()

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
    # Treat out-of-stock products as having no price (will show N/A)
    out_of_stock = df["stock"].map(normalize_text).isin(NO_STOCK_VALUES)
    df.loc[out_of_stock, "price_unit"] = float("nan")
    if df["supplier"].nunique() < 2:
        st.info(t("comparison_need_more"))
        return

    render_section_intro(
        t("comparison_scope_eyebrow"),
        t("comparison_scope_title"),
        t("comparison_scope_desc"),
    )

    # Only show brands/types that have at least one product with a price
    df_priced = df[df["price_unit"].notna()]

    col_brand, col_type, col_size, col_desc, col_toggle = st.columns([1.2, 1.2, 1.2, 1.2, 0.8])
    with col_brand:
        brand_options = _dedup_sorted(df_priced["brand"].dropna().unique().tolist())
        selected_brands = st.multiselect("Marca", brand_options, key="comparison_brand", placeholder="Todas")

    folded_brands = {_fold(b) for b in selected_brands}
    brand_df = df if not selected_brands else df[df["brand"].apply(lambda x: _fold(str(x))).isin(folded_brands)]

    with col_type:
        priced_brand_df = df_priced if not selected_brands else df_priced[df_priced["brand"].apply(lambda x: _fold(str(x))).isin(folded_brands)]
        type_options = _dedup_sorted(priced_brand_df["product_type"].dropna().unique().tolist())
        selected_types = st.multiselect("Producto", type_options, key="comparison_product_type", placeholder="Todos")

    folded_types = {_fold(t) for t in selected_types}
    filtered_by_type = brand_df if not selected_types else brand_df[brand_df["product_type"].apply(lambda x: _fold(str(x))).isin(folded_types)]

    with col_size:
        size_options = _dedup_sorted(
            filtered_by_type[filtered_by_type["price_unit"].notna()]["size"].dropna().unique().tolist()
        )
        selected_sizes = st.multiselect("Tamaño", size_options, key="comparison_size", placeholder="Todos")

    with col_desc:
        description_search = st.text_input("Descripción", placeholder="ej. Blancaflor Leudante", key="comparison_description")
    with col_toggle:
        st.caption(" ")
        hide_no_price = st.toggle("Ocultar sin precio", value=True, key="comparison_hide_no_price")

    filtered = filtered_by_type
    if selected_sizes:
        filtered = filtered[filtered["size"].isin(selected_sizes)]

    # Group by canonical_key only (accent/case-insensitive matching key).
    # Display fields (brand, product_type, size, etc.) may differ across suppliers
    # for the same product — take the first occurrence per canonical_key.
    display_cols = ["canonical_key", "canonical_name", "brand", "product_type", "size", "category_dept", "category_sub"]
    display_df = filtered.groupby("canonical_key", sort=False)[display_cols].first().reset_index(drop=True)

    all_suppliers = sorted(df["supplier"].dropna().unique().tolist())

    # Which (canonical_key, supplier) pairs exist in the DB (regardless of price)?
    presence = (
        df.groupby(["canonical_key", "supplier"]).size().unstack(fill_value=0).gt(0)
    ).reindex(columns=all_suppliers, fill_value=False)

    pivot = filtered.pivot_table(
        index="canonical_key",
        columns="supplier",
        values="price_unit",
        aggfunc="min",
        dropna=False,
    ).reset_index()
    pivot.columns.name = None
    pivot = pivot.merge(display_df, on="canonical_key", how="left")

    # Ensure every supplier always has a column, even if all values are NaN
    for sup in all_suppliers:
        if sup not in pivot.columns:
            pivot[sup] = float("nan")

    fixed_columns = ["canonical_key", "canonical_name", "brand", "product_type", "size", "category_dept", "category_sub"]
    supplier_columns = all_suppliers

    if description_search:
        pivot = pivot[pivot["canonical_name"].astype(str).str.contains(description_search, case=False, na=False)]
    if hide_no_price:
        pivot = pivot[pivot[supplier_columns].notna().any(axis=1)]

    if pivot.empty:
        render_empty_state(t("comparison_no_dept"))
        return

    # Suppliers with at least one price in the current filtered view
    priced_supplier_cols = {sup for sup in supplier_columns if pivot[sup].notna().any()}

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

    export_columns = ["canonical_name"] + supplier_columns + ["brand", "product_type", "size", "diff_pct"]
    export_frame = pivot[[c for c in export_columns if c in pivot.columns]].copy()

    # Build display frame: narrower than export (no brand/product_type/size)
    display_columns = ["canonical_name"] + supplier_columns + ["diff_pct"]
    display_frame = pivot[[c for c in display_columns if c in pivot.columns]].copy()
    ck_values = pivot["canonical_key"].values
    for sup in supplier_columns:
        if sup in presence.columns:
            has_product = presence.reindex(index=ck_values)[sup].values
        else:
            has_product = [False] * len(display_frame)
        price_col = display_frame[sup]
        display_frame[sup] = [
            f"${v:,.2f}" if pd.notna(v) else ("N/A" if p else "")
            for v, p in zip(price_col, has_product)
        ]

    render_export_button(
        export_frame.to_csv(index=False).encode("utf-8"),
        file_name="comparison_matrix.csv",
        key="comparison_export",
    )

    # Build detail lookup: canonical_key -> {supplier -> [row_dict, ...]}
    detail_cols = ["canonical_key", "supplier", "name", "url", "brand", "product_type",
                   "size", "price_unit", "price_bulk", "stock", "units_per_package",
                   "sku", "product_id"]
    detail_lookup: dict[str, dict[str, list]] = {}
    for _, r in filtered[[c for c in detail_cols if c in filtered.columns]].iterrows():
        ck = r["canonical_key"]
        sup = r["supplier"]
        detail_lookup.setdefault(ck, {}).setdefault(sup, []).append(r.to_dict())

    # Flagged rows survive filter reruns via session state
    if "comp_flagged_cks" not in st.session_state:
        st.session_state["comp_flagged_cks"] = set()

    # AgGrid setup
    ag_frame = display_frame.copy()
    ag_frame["_ck"] = pivot["canonical_key"].values
    ag_frame["_cheapest"] = pivot["cheapest"].values
    ag_frame["_clicked_col"] = ""  # populated by onCellClicked JS handler
    ag_frame["🚩"] = ag_frame["_ck"].isin(st.session_state["comp_flagged_cks"])

    min_cell_style = JsCode("""function(params) {
        if (params.data._cheapest && params.colDef.field === params.data._cheapest
                && params.value && params.value.startsWith('$')) {
            return {'background': '#e6f0ed', 'color': '#1d5b50', 'fontWeight': '600'};
        }
        return {};
    }""")

    gb = GridOptionsBuilder.from_dataframe(
        ag_frame[[c for c in ag_frame.columns if not c.startswith("_")]]
    )
    gb.configure_default_column(resizable=True, sortable=True, filter=False, min_width=60,
                                cellStyle={"fontSize": "12px"})
    gb.configure_column(
        "🚩",
        editable=True,
        cellRenderer="agCheckboxCellRenderer",
        cellEditor="agCheckboxCellEditor",
        width=40,
        minWidth=40,
        maxWidth=40,
        pinned="left",
        headerName="",
        sortable=False,
        suppressSizeToFit=True,
    )
    for sup in supplier_columns:
        gb.configure_column(sup, cellStyle=min_cell_style, hide=(sup not in priced_supplier_cols))
    gb.configure_selection(selection_mode="single", use_checkbox=False)
    grid_options = gb.build()
    # Pass hidden columns so they're available in cellStyle and detail panel
    grid_options["columnDefs"] += [
        {"field": "_ck", "hide": True},
        {"field": "_cheapest", "hide": True},
        {"field": "_clicked_col", "hide": True},
    ]
    # Stamp clicked column into row data before selection fires.
    # Skip setSelected for the flag column so checkbox toggling isn't disrupted.
    grid_options["onCellClicked"] = JsCode("""function(e) {
        var field = e.colDef.field || '';
        e.node.data['_clicked_col'] = field;
        if (field !== '\uD83D\uDEA9') {
            e.node.setSelected(true, true);
        }
    }""")

    # Restore user-resized column widths from the previous render
    saved_col_state = st.session_state.get("comp_col_state", [])
    if saved_col_state:
        grid_options["onGridReady"] = JsCode(
            "function(p){var s=%s;"
            "var a=p.columnApi||p.api;"
            "if(a&&a.applyColumnState)a.applyColumnState({state:s,applyOrder:false});}"
            % json.dumps(saved_col_state)
        )

    col_table, col_detail = st.columns([4, 1])

    with col_table:
        grid_response = AgGrid(
            ag_frame,
            gridOptions=grid_options,
            update_mode=GridUpdateMode.VALUE_CHANGED | GridUpdateMode.SELECTION_CHANGED,
            height=600,
            fit_columns_on_grid_load=False,
            allow_unsafe_jscode=True,
            use_container_width=True,
            custom_css={
                ".ag-header-cell-label": {"font-size": "12px !important"},
                ".ag-cell": {"font-size": "12px !important", "padding-left": "2px !important", "padding-right": "2px !important"},
                ".ag-header-cell": {"padding-left": "2px !important", "padding-right": "2px !important"},
                ".ag-row": {"line-height": "24px !important"},
                ".ag-header-row": {"height": "32px !important"},
            },
        )
        # Persist column state so widths survive filter reruns
        col_state = grid_response.get("column_state")
        if col_state is not None and len(col_state) > 0:
            st.session_state["comp_col_state"] = (
                col_state.to_dict("records")
                if hasattr(col_state, "to_dict")
                else list(col_state)
            )
        # Read checkbox flag state back from the grid and persist it
        resp_data = grid_response.get("data")
        if resp_data is not None and "🚩" in resp_data.columns and "_ck" in resp_data.columns:
            st.session_state["comp_flagged_cks"] = set(
                resp_data.loc[resp_data["🚩"] == True, "_ck"].tolist()
            )

    with col_detail:
        selected = grid_response.get("selected_rows")
        if selected is not None and len(selected) > 0:
            row = selected[0] if isinstance(selected, list) else selected.iloc[0]
            ck = row.get("_ck", "")
            canonical = row.get("canonical_name", ck)
            st.markdown(f"**{canonical}**")
            details = detail_lookup.get(ck, {})
            clicked_col = row.get("_clicked_col", "")
            if clicked_col in supplier_columns:
                # User clicked a specific supplier cell — show only that supplier
                active_suppliers = [clicked_col]
            else:
                # User clicked canonical_name or diff_pct — show all priced suppliers
                active_suppliers = [
                    sup for sup in supplier_columns
                    if str(row.get(sup, "")) not in ("", "N/A", "nan", "None")
                ]
            for sup in active_suppliers:
                products = details.get(sup)
                if not products:
                    continue
                st.divider()
                st.markdown(f"**{sup}**")
                for p in products:
                    def _val(key):
                        v = p.get(key)
                        return v if (v is not None and pd.notna(v)) else None
                    if _val("name"):
                        st.caption(str(_val("name")))
                    fields = [
                        ("SKU", _val("sku")),
                        ("ID producto", _val("product_id")),
                        ("Marca", _val("brand")),
                        ("Tipo", _val("product_type")),
                        ("Tamaño", _val("size")),
                        ("Precio unit.", f"${float(_val('price_unit')):,.2f}" if _val("price_unit") else None),
                        ("Precio bulto", f"${float(_val('price_bulk')):,.2f}" if _val("price_bulk") else None),
                        ("Uds. x bulto", _val("units_per_package")),
                        ("Stock", _val("stock")),
                    ]
                    for label, value in fields:
                        if value:
                            st.markdown(f"<small>**{label}:** {value}</small>", unsafe_allow_html=True)
                    url_str = _val("url")
                    if url_str:
                        st.markdown(f"[Ver producto →]({url_str})")
        else:
            st.caption("← Clic en una fila")

    # Flagging panel — shows only when rows are checked
    st.divider()
    flagged_cks = st.session_state.get("comp_flagged_cks", set())
    flagged_names = (
        pivot[pivot["canonical_key"].isin(flagged_cks)]["canonical_name"]
        .dropna().unique().tolist()
    )
    if flagged_names:
        preview = ", ".join(flagged_names[:4]) + ("…" if len(flagged_names) > 4 else "")
        st.markdown(f"**🚩 {len(flagged_names)} producto(s) marcado(s):** {preview}")
        with st.form("comp_flag_form"):
            comment = st.text_area(
                "Comentario",
                placeholder="Ej: estos productos no son equivalentes, el tamaño es diferente",
            )
            col_send, col_clear, _ = st.columns([1, 1, 6])
            with col_send:
                submitted = st.form_submit_button("Enviar")
            with col_clear:
                cleared = st.form_submit_button("Limpiar")
        if submitted:
            if not comment.strip():
                st.warning("Escribí un comentario antes de enviar.")
            else:
                _insert_feedback(flagged_names, comment.strip())
                st.session_state["comp_flagged_cks"] = set()
                st.cache_data.clear()
                st.toast("Reporte guardado. ¡Gracias!", icon="✅")
                st.rerun()
        if cleared:
            st.session_state["comp_flagged_cks"] = set()
            st.rerun()
    else:
        st.caption("☐ Marcá filas con 🚩 para reportar un problema")


def render_history_page() -> None:
    products_df = _load_history_products()

    render_page_header(
        get_page_meta()["History"]["eyebrow"],
        get_page_meta()["History"]["title"],
        get_page_meta()["History"]["description"],
        context=t("history_ctx", n=format_count(products_df["canonical_key"].nunique())) if not products_df.empty else None,
    )

    if products_df.empty:
        render_empty_state(t("history_no_data"))
        return

    render_metric_row(
        [
            (t("history_metric_tracked"), format_count(products_df["canonical_key"].nunique()), t("history_metric_tracked_note")),
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

    # Cascading filters
    col_brand, col_type, col_size, col_sup, col_desc = st.columns([1, 1, 1, 1, 1.2])

    with col_brand:
        brand_opts = _dedup_sorted(products_df["brand"].dropna().unique().tolist())
        sel_brands = st.multiselect("Marca", brand_opts, key="history_brand", placeholder="Todas")

    folded_brands = {_fold(b) for b in sel_brands}
    h1 = products_df if not sel_brands else products_df[products_df["brand"].apply(lambda x: _fold(str(x))).isin(folded_brands)]

    with col_type:
        type_opts = _dedup_sorted(h1["product_type"].dropna().unique().tolist())
        sel_types = st.multiselect("Producto", type_opts, key="history_type", placeholder="Todos")

    folded_types = {_fold(tp) for tp in sel_types}
    h2 = h1 if not sel_types else h1[h1["product_type"].apply(lambda x: _fold(str(x))).isin(folded_types)]

    with col_size:
        size_opts = _dedup_sorted(h2["size"].dropna().unique().tolist())
        sel_sizes = st.multiselect("Tamaño", size_opts, key="history_size", placeholder="Todos")

    h3 = h2 if not sel_sizes else h2[h2["size"].isin(sel_sizes)]

    with col_sup:
        sup_opts = _dedup_sorted(h3["supplier"].dropna().unique().tolist())
        sel_suppliers = st.multiselect("Proveedor", sup_opts, key="history_supplier", placeholder="Todos")

    h4 = h3 if not sel_suppliers else h3[h3["supplier"].isin(sel_suppliers)]

    with col_desc:
        desc_search = st.text_input("Descripción", placeholder="ej. Blancaflor Leudante", key="history_desc")

    if desc_search:
        h4 = h4[h4["canonical_name"].astype(str).str.contains(desc_search, case=False, na=False)]

    # Unique canonical products from the filtered set
    # Mark canonical keys where NO supplier currently has a price (all unavailable)
    h4_numeric = h4.copy()
    h4_numeric["price_unit"] = pd.to_numeric(h4_numeric["price_unit"], errors="coerce")
    ck_has_price = h4_numeric.groupby("canonical_key")["price_unit"].apply(lambda x: x.notna().any())

    canonical_opts_df = (
        h4.dropna(subset=["canonical_key"])
        .groupby("canonical_key")["canonical_name"]
        .first()
        .reset_index()
        .sort_values("canonical_name")
    )
    # Build display labels: append "(sin precio)" for currently-unavailable products
    canonical_opts_df["label"] = canonical_opts_df.apply(
        lambda r: r["canonical_name"] if ck_has_price.get(r["canonical_key"], False)
                  else f"{r['canonical_name']} (sin precio)",
        axis=1,
    )
    canonical_labels = canonical_opts_df["label"].tolist()
    ck_by_label = dict(zip(canonical_opts_df["label"], canonical_opts_df["canonical_key"]))

    if not canonical_labels:
        render_empty_state(t("history_no_match"))
        return

    render_filter_summary([], t("history_showing", n=format_count(len(canonical_labels))))

    selected_names = st.multiselect(
        t("history_products_label"),
        canonical_labels,
        key="history_products",
        placeholder=t("history_products_ph"),
    )

    if not selected_names:
        render_empty_state(t("history_select_prompt"))
        return

    selected_cks = [ck_by_label[n] for n in selected_names]
    placeholders = ", ".join(["%s"] * len(selected_cks))
    history = query(
        f"""
        SELECT
            h.first_seen,
            h.last_seen,
            h.price_unit,
            p.product_id,
            p.canonical_name,
            p.supplier,
            p.name,
            p.brand,
            p.product_type,
            p.size,
            p.price_bulk
        FROM price_history h
        JOIN products p ON p.sku = h.sku AND p.supplier = h.supplier
        WHERE p.canonical_key IN ({placeholders})
        ORDER BY h.first_seen ASC;
        """,
        *selected_cks,
    )

    if history.empty:
        render_empty_state(t("history_no_rows"))
        return

    history["price_unit"] = pd.to_numeric(history["price_unit"], errors="coerce")
    history["price_bulk"] = pd.to_numeric(history["price_bulk"], errors="coerce")
    # Label: "supplier | canonical_name" so multiple products stay distinguishable
    history["label"] = history["supplier"].astype(str) + " | " + history["canonical_name"].fillna(history["name"]).astype(str)

    render_metric_row(
        [
            (t("history_metric_selected"), format_count(len(selected_names)), t("history_metric_selected_note")),
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

    # Expand each price period into two rows (first_seen + last_seen) so every
    # period renders as a visible horizontal segment. Single-period products
    # (same first/last date) become a visible dot via markers mode.
    starts = history[["label", "price_unit", "first_seen"]].rename(columns={"first_seen": "date"})
    ends   = history[["label", "price_unit", "last_seen"]].rename(columns={"last_seen": "date"})
    history_plot = pd.concat([starts, ends]).sort_values(["label", "date"]).reset_index(drop=True)
    history_plot["date"] = pd.to_datetime(history_plot["date"])
    history_plot["price_unit"] = pd.to_numeric(history_plot["price_unit"], errors="coerce")

    plottable = history_plot.dropna(subset=["price_unit"])
    if plottable.empty:
        st.info("Los productos seleccionados no tienen precios registrados en el historial.")
    else:
        figure = go.Figure()
        labels_ordered = sorted(plottable["label"].unique())
        for i, label in enumerate(labels_ordered):
            subset = plottable[plottable["label"] == label].sort_values("date")
            color = CHART_COLORS[i % len(CHART_COLORS)]
            # Use lines+markers when there are multiple dates, markers-only for a single date
            multi_date = subset["date"].nunique() > 1
            figure.add_trace(go.Scatter(
                x=subset["date"],
                y=subset["price_unit"],
                mode="lines+markers" if multi_date else "markers",
                name=label,
                line=dict(shape="hv", width=2.4, color=color),
                marker=dict(size=8, color=color, line=dict(width=1, color="#ffffff")),
            ))
        figure.update_layout(
            height=420,
            xaxis_title=t("history_chart_date"),
            yaxis_title=t("history_chart_price"),
        )
        figure.update_yaxes(tickformat=".2f")
        style_figure(figure)
        st.plotly_chart(figure, use_container_width=True, config={"displayModeBar": False})

    render_section_intro(
        t("history_audit_eyebrow"),
        t("history_audit_title"),
        t("history_audit_desc"),
    )

    render_export_button(
        history[["first_seen", "last_seen", "canonical_name", "supplier", "name", "size", "price_unit", "price_bulk"]]
        .to_csv(index=False).encode("utf-8"),
        file_name="price_history.csv",
        key="history_export",
    )

    display_table(
        history[["first_seen", "last_seen", "canonical_name", "supplier", "name", "size", "price_unit", "price_bulk"]]
    )


@st.cache_data(ttl=_CACHE_TTL, show_spinner=False)
def _load_run_log() -> pd.DataFrame:
    return query(
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


def render_logs_page() -> None:
    runs = _load_run_log()

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


def render_feedback_page() -> None:
    _ensure_feedback_table()
    log = _load_feedback_log()

    render_page_header(
        t("page_feedback_eyebrow"),
        t("page_feedback_title"),
        t("page_feedback_desc"),
        context=t("feedback_ctx", n=format_count(len(log))) if not log.empty else None,
    )

    if log.empty:
        render_empty_state(t("feedback_no_data"))
        return

    render_metric_row(
        [
            (t("feedback_metric_total"), format_count(len(log)), t("feedback_metric_total_note")),
            (t("feedback_metric_recent"), format_timestamp(log["created_at"].max()), t("feedback_metric_recent_note")),
        ]
    )

    render_section_intro(
        t("feedback_log_eyebrow"),
        t("feedback_log_title"),
        t("feedback_log_desc"),
    )

    display = log.copy()
    display["#"] = display["id"]
    display["productos"] = display["canonical_names"].apply(
        lambda x: ", ".join(x) if isinstance(x, list) else str(x)
    )
    display["fecha"] = display["created_at"].apply(format_timestamp)

    render_export_button(
        display[["#", "fecha", "productos", "comment"]].rename(columns={"comment": "comentario"})
        .to_csv(index=False).encode("utf-8"),
        file_name="feedback_log.csv",
        key="feedback_export",
    )

    display_table(display[["#", "fecha", "productos", "comment"]].rename(columns={"comment": "comentario"}))


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
        elif page == "Feedback":
            render_feedback_page()
        else:
            st.error(f"Unknown page: {page}")
    except Exception as exc:
        st.error(f"Could not load data: {exc}")


main()
