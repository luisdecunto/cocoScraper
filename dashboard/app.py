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
            SELECT p.product_id, p.name, p.brand, p.product_type, p.supplier,
                   p.category_dept, p.category_sub, p.size_value, p.size_unit,
                   s.price_unit, s.price_bulk, s.stock, s.scraped_at
            FROM products p
            JOIN price_snapshots s ON s.sku = p.sku AND s.supplier = p.supplier
            WHERE s.scraped_at = (
                SELECT MAX(scraped_at) FROM price_snapshots
                WHERE sku = p.sku AND supplier = p.supplier
            )
            ORDER BY s.price_unit ASC NULLS LAST;
        """)

        col1, col2, col3 = st.columns(3)
        with col1:
            suppliers = ["All"] + sorted(df["supplier"].dropna().unique().tolist())
            sel_supplier = st.multiselect("Supplier", suppliers[1:], key="t1_supplier")
        with col2:
            depts = sorted(df["category_dept"].dropna().unique().tolist())
            sel_dept = st.multiselect("Department", depts, key="t1_dept")
        with col3:
            search = st.text_input("Search by product_id or name", key="t1_search")

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

        st.caption(f"{len(filtered)} rows")
        # Display key columns: product_id, brand, type, category, prices
        display_cols = ["product_id", "brand", "product_type", "category_dept",
                       "category_sub", "size_value", "size_unit", "supplier",
                       "price_unit", "price_bulk", "stock", "scraped_at"]
        st.dataframe(filtered[display_cols], width="stretch", hide_index=True)

    except Exception as e:
        st.error(f"Could not load data: {e}")


# ------------------------------------------------------------------ #
# Tab 2 — Price Comparison                                            #
# ------------------------------------------------------------------ #

with tab2:
    st.subheader("Cross-Supplier Comparison")

    try:
        df2 = query("""
            SELECT p.product_id, p.name, p.brand, p.product_type, p.canonical_key,
                   p.category_dept, p.category_sub, p.supplier,
                   s.price_unit, s.scraped_at
            FROM products p
            JOIN price_snapshots s ON s.sku = p.sku AND s.supplier = p.supplier
            WHERE s.scraped_at = (
                SELECT MAX(scraped_at) FROM price_snapshots
                WHERE sku = p.sku AND supplier = p.supplier
            );
        """)

        suppliers_in_data = df2["supplier"].nunique()
        if suppliers_in_data < 2:
            st.info("Add more suppliers to enable comparison.")
        else:
            depts2 = ["All"] + sorted(df2["category_dept"].dropna().unique().tolist())
            sel_cat2 = st.selectbox("Department", depts2, key="t2_category")

            filtered2 = df2 if sel_cat2 == "All" else df2[df2["category_dept"] == sel_cat2]

            pivot = filtered2.pivot_table(
                index=["product_id", "brand", "product_type", "category_dept", "category_sub"],
                columns="supplier",
                values="price_unit",
                aggfunc="first",
            ).reset_index()
            pivot.columns.name = None

            supplier_cols = [c for c in pivot.columns if c not in ("product_id", "brand", "product_type", "category_dept", "category_sub")]

            if supplier_cols:
                price_data = pivot[supplier_cols]
                pivot["cheapest"] = price_data.idxmin(axis=1)
                pivot["max_price"] = price_data.max(axis=1)
                pivot["min_price"] = price_data.min(axis=1)
                pivot["diff_pct"] = (
                    (pivot["max_price"] - pivot["min_price"]) / pivot["min_price"] * 100
                ).round(2)
                pivot = pivot.drop(columns=["max_price", "min_price"])

                def highlight_min(row):
                    styles = [""] * len(row)
                    cheapest = row.get("cheapest")
                    if cheapest and cheapest in row.index:
                        idx = row.index.get_loc(cheapest)
                        styles[idx] = "background-color: #00B050; color: white"
                    return styles

                st.dataframe(
                    pivot.style.apply(highlight_min, axis=1),
                    width="stretch",
                    hide_index=True,
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
            SELECT DISTINCT p.product_id, p.name, p.sku, p.brand, p.product_type
            FROM products p
            JOIN price_snapshots s ON s.sku = p.sku AND s.supplier = p.supplier
            ORDER BY p.product_id;
        """)

        search3 = st.text_input("Search by product_id, name, or SKU", key="t3_search")

        if search3:
            mask = (
                products_df["product_id"].str.contains(search3, case=False, na=False) |
                products_df["name"].str.contains(search3, case=False, na=False) |
                products_df["sku"].str.contains(search3, case=False, na=False)
            )
            matches = products_df[mask]
        else:
            matches = products_df

        if matches.empty:
            st.info("No products found.")
        else:
            options = {f"{r['product_id']} — {r['name']} ({r['brand']})": r["sku"]
                      for _, r in matches.iterrows()}
            selected_label = st.selectbox("Select product", list(options.keys()), key="t3_product")
            selected_sku = options[selected_label]

            history = query("""
                SELECT s.scraped_at, s.price_unit, s.price_bulk, s.supplier, p.product_id
                FROM price_snapshots s
                JOIN products p ON p.sku = s.sku AND p.supplier = s.supplier
                WHERE s.sku = %s
                ORDER BY s.scraped_at ASC;
            """, selected_sku)

            if not history.empty:
                fig = px.line(
                    history,
                    x="scraped_at",
                    y="price_unit",
                    color="supplier",
                    title=f"Price history — {selected_label}",
                    labels={"scraped_at": "Date", "price_unit": "Unit Price"},
                    markers=True,
                )
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(history, width="stretch", hide_index=True)
            else:
                st.info("No snapshot history for this product.")

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
