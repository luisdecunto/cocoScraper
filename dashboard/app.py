"""
cocoScraper — Exploration Dashboard
Run with: streamlit run dashboard/app.py
"""

import os

import pandas as pd
import plotly.express as px
import psycopg2
import psycopg2.extras
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="cocoScraper", layout="wide")
st.title("cocoScraper — Price Explorer")


# ------------------------------------------------------------------ #
# Database connection                                                  #
# ------------------------------------------------------------------ #

def query(sql: str, *args) -> pd.DataFrame:
    """Run a query and return a DataFrame."""
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", 5432)),
        dbname=os.getenv("DB_NAME", "prices"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASS"),
    )
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, args if args else None)
            rows = cur.fetchall()
        return pd.DataFrame([dict(r) for r in rows])
    finally:
        conn.close()


# ------------------------------------------------------------------ #
# Tabs                                                                 #
# ------------------------------------------------------------------ #

tab1, tab2, tab3, tab4 = st.tabs([
    "Latest Prices", "Comparison", "Price History", "Run Log"
])


# ------------------------------------------------------------------ #
# Tab 1 — Latest Prices                                               #
# ------------------------------------------------------------------ #

with tab1:
    st.subheader("Latest Prices")

    try:
        df = query("""
            SELECT product_id, name, brand, product_type, variant,
                   supplier, category_dept, category_sub, size,
                   price_unit, price_bulk, stock, last_scraped_at AS scraped_at
            FROM products
            ORDER BY price_unit ASC NULLS LAST;
        """)

        col1, col2, col3, col4 = st.columns([2, 2, 2, 1])
        with col1:
            suppliers = ["All"] + sorted(df["supplier"].dropna().unique().tolist())
            sel_supplier = st.multiselect("Supplier", suppliers[1:], key="t1_supplier")
        with col2:
            depts = sorted(df["category_dept"].dropna().unique().tolist())
            sel_dept = st.multiselect("Department", depts, key="t1_dept")
        with col3:
            search = st.text_input("Search by product_id or name", key="t1_search")
        with col4:
            hide_no_stock = st.checkbox("Hide out of stock", value=True, key="t1_hide_no_stock")

        filtered = df.copy()
        if sel_supplier:
            filtered = filtered[filtered["supplier"].isin(sel_supplier)]
        if sel_dept:
            filtered = filtered[filtered["category_dept"].isin(sel_dept)]
        if search:
            filtered = filtered[
                (filtered["product_id"].str.contains(search, case=False, na=False)) |
                (filtered["name"].str.contains(search, case=False, na=False))
            ]
        _NO_STOCK_VALUES = {"sin stock", "disponibilidad crítica", "disponibilidad critica"}
        if hide_no_stock:
            filtered = filtered[~filtered["stock"].str.lower().str.strip().isin(_NO_STOCK_VALUES)]

        df["price_unit"] = pd.to_numeric(df["price_unit"], errors="coerce")
        df["price_bulk"] = pd.to_numeric(df["price_bulk"], errors="coerce")

        st.caption(f"{len(filtered)} rows")
        display_cols = ["product_id", "name", "brand", "product_type", "variant",
                        "size", "category_dept", "category_sub", "supplier",
                        "price_unit", "price_bulk", "stock", "scraped_at"]
        st.dataframe(
            filtered[display_cols],
            width="stretch",
            hide_index=True,
            column_config={
                "price_unit": st.column_config.NumberColumn(format="$%.2f"),
                "price_bulk": st.column_config.NumberColumn(format="$%.2f"),
            },
        )

    except Exception as e:
        st.error(f"Could not load data: {e}")


# ------------------------------------------------------------------ #
# Tab 2 — Price Comparison                                            #
# ------------------------------------------------------------------ #

with tab2:
    st.subheader("Cross-Supplier Comparison")

    try:
        df2 = query("""
            SELECT canonical_key, brand, product_type, size,
                   category_dept, category_sub, supplier, price_unit
            FROM products
            WHERE canonical_key IS NOT NULL
              AND canonical_key != '?|?|?'
              AND price_unit IS NOT NULL;
        """)

        df2["price_unit"] = pd.to_numeric(df2["price_unit"], errors="coerce")

        suppliers_in_data = df2["supplier"].nunique()
        if suppliers_in_data < 2:
            st.info("Add more suppliers to enable comparison.")
        else:
            depts2 = ["All"] + sorted(df2["category_dept"].dropna().unique().tolist())
            sel_cat2 = st.selectbox("Department", depts2, key="t2_category")

            filtered2 = df2 if sel_cat2 == "All" else df2[df2["category_dept"] == sel_cat2]

            pivot = filtered2.pivot_table(
                index=["canonical_key", "brand", "product_type", "size", "category_dept", "category_sub"],
                columns="supplier",
                values="price_unit",
                aggfunc="min",
            ).reset_index()
            pivot.columns.name = None

            # Only keep rows that appear in 2+ suppliers
            supplier_cols = [c for c in pivot.columns if c not in ("canonical_key", "brand", "product_type", "size", "category_dept", "category_sub")]
            pivot = pivot[pivot[supplier_cols].notna().sum(axis=1) >= 2]

            if supplier_cols and not pivot.empty:
                price_data = pivot[supplier_cols]
                pivot["cheapest"] = price_data.idxmin(axis=1)
                pivot["max_price"] = price_data.max(axis=1)
                pivot["min_price"] = price_data.min(axis=1)
                pivot["diff_pct"] = (
                    (pivot["max_price"] - pivot["min_price"]) / pivot["min_price"] * 100
                ).round(2)
                pivot = pivot.drop(columns=["max_price", "min_price", "canonical_key"])
                st.caption(f"{len(pivot)} cross-supplier matches")

                def highlight_min(row):
                    styles = [""] * len(row)
                    cheapest = row.get("cheapest")
                    if cheapest and cheapest in row.index:
                        idx = row.index.get_loc(cheapest)
                        styles[idx] = "background-color: #00B050; color: white"
                    return styles

                price_col_config = {
                    col: st.column_config.NumberColumn(format="$%.2f")
                    for col in supplier_cols
                }
                price_col_config["diff_pct"] = st.column_config.NumberColumn(format="%.2f%%")
                st.dataframe(
                    pivot.style.apply(highlight_min, axis=1),
                    width="stretch",
                    hide_index=True,
                    column_config=price_col_config,
                )

    except Exception as e:
        st.error(f"Could not load data: {e}")


# ------------------------------------------------------------------ #
# Tab 3 — Price History                                               #
# ------------------------------------------------------------------ #

with tab3:
    st.subheader("Price History")

    try:
        products_df = query("""
            SELECT product_id, name, supplier, brand, product_type, size
            FROM products
            WHERE product_id IS NOT NULL AND price_unit IS NOT NULL
            ORDER BY product_id;
        """)

        search3 = st.text_input("Search by name, brand, or product_type", key="t3_search")

        if search3:
            mask = (
                products_df["product_id"].str.contains(search3, case=False, na=False) |
                products_df["name"].str.contains(search3, case=False, na=False) |
                products_df["brand"].str.contains(search3, case=False, na=False) |
                products_df["product_type"].str.contains(search3, case=False, na=False)
            )
            found = products_df[mask]
        else:
            found = products_df.head(200)

        if found.empty:
            st.info("No products found.")
        else:
            options = {
                f"{r['product_id']} — {r['name']} [{r['supplier']}]": r["product_id"]
                for _, r in found.iterrows()
            }
            selected_labels = st.multiselect(
                "Select products to compare (pick from any supplier)",
                list(options.keys()),
                key="t3_product",
            )

            if selected_labels:
                selected_ids = [options[lbl] for lbl in selected_labels]
                placeholders = ", ".join(["%s"] * len(selected_ids))
                history = query(f"""
                    SELECT h.first_seen, h.last_seen, h.price_unit,
                           p.product_id, p.supplier, p.name, p.brand,
                           p.product_type, p.size, p.price_bulk
                    FROM price_history h
                    JOIN products p ON p.sku = h.sku AND p.supplier = h.supplier
                    WHERE p.product_id IN ({placeholders})
                    ORDER BY h.first_seen ASC;
                """, *selected_ids)

                if not history.empty:
                    history["price_unit"] = pd.to_numeric(history["price_unit"], errors="coerce")
                    history["price_bulk"] = pd.to_numeric(history["price_bulk"], errors="coerce")
                    history["label"] = history["product_id"] + " · " + history["name"].str[:40]
                    fig = px.line(
                        history,
                        x="first_seen",
                        y="price_unit",
                        color="label",
                        title="Price history (each point = price change date)",
                        labels={"first_seen": "Date", "price_unit": "Unit Price", "label": "Product"},
                        markers=True,
                        line_shape="hv",
                    )
                    fig.update_yaxes(tickformat=".2f")
                    st.plotly_chart(fig, use_container_width=True)
                    st.dataframe(
                        history[["first_seen", "last_seen", "product_id", "supplier", "name", "size", "price_unit", "price_bulk"]],
                        width="stretch",
                        hide_index=True,
                        column_config={
                            "price_unit": st.column_config.NumberColumn(format="$%.2f"),
                            "price_bulk": st.column_config.NumberColumn(format="$%.2f"),
                        },
                    )
            else:
                st.info("Search and select one or more products above.")

    except Exception as e:
        st.error(f"Could not load data: {e}")


# ------------------------------------------------------------------ #
# Tab 4 — Run Log                                                     #
# ------------------------------------------------------------------ #

with tab4:
    st.subheader("Scrape Run Log")

    try:
        runs = query("""
            SELECT supplier, started_at, finished_at, status,
                   products_scraped, snapshots_written, error_message
            FROM run_log
            ORDER BY started_at DESC
            LIMIT 20;
        """)

        zero_snap = runs[(runs["status"] == "success") & (runs["snapshots_written"] == 0)]
        if not zero_snap.empty:
            st.warning(
                f"⚠️ {len(zero_snap)} run(s) completed successfully but wrote 0 snapshots. "
                "Login may have failed silently or the site structure changed."
            )

        def color_status(val):
            if val == "success":
                return "background-color: #00B050; color: white"
            elif val == "failed":
                return "background-color: #FF0000; color: white"
            elif val == "running":
                return "background-color: #FFC000; color: black"
            return ""

        st.dataframe(
            runs.style.map(color_status, subset=["status"]),
            width="stretch",
            hide_index=True,
        )

    except Exception as e:
        st.error(f"Could not load data: {e}")
