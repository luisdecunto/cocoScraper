"""
Revisar page — Manual classification approval workflow.

Allows admins to review pending product classifications (brand, type, size, etc.),
approve them, edit them, or reject them for re-classification.
"""

from __future__ import annotations

import psycopg2
import psycopg2.extras
import streamlit as st
import pandas as pd
from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode

try:
    from .db.connection import get_psycopg2_connection_kwargs, has_database_config
    from .i18n import t
    from .ui import render_page_header, format_timestamp
except ImportError:
    from db.connection import get_psycopg2_connection_kwargs, has_database_config
    from i18n import t
    from ui import render_page_header, format_timestamp


def get_pending_count(conn) -> dict[str, int]:
    """Get count of pending classifications by supplier and confidence."""
    query = """
    SELECT supplier, classification_confidence, COUNT(*) as count
    FROM products
    WHERE classification_status = 'pending'
    GROUP BY supplier, classification_confidence
    ORDER BY supplier, classification_confidence
    """
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(query)
        rows = cur.fetchall()

    result = {}
    for row in rows:
        key = f"{row['supplier']}:{row['classification_confidence']}"
        result[key] = row['count']
    return result


def fetch_pending_products(
    conn,
    supplier: str | None = None,
    confidence: str | None = None,
    limit: int = 100,
) -> pd.DataFrame:
    """Fetch pending products for review."""
    filters = ["classification_status = 'pending'"]
    params = []

    if supplier:
        filters.append("supplier = %s")
        params.append(supplier)

    if confidence:
        filters.append("classification_confidence = %s")
        params.append(confidence)

    where_clause = " AND ".join(filters)

    query = f"""
    SELECT sku, supplier, name, brand, product_type, variant, size,
           canonical_name, classification_confidence, updated_at
    FROM products
    WHERE {where_clause}
    ORDER BY classification_confidence DESC, updated_at ASC
    LIMIT %s
    """
    params.append(limit)

    df = pd.read_sql_query(query, conn, params=params)
    return df


def approve_product(conn, sku: str, supplier: str) -> bool:
    """Approve a product classification."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE products SET classification_status = 'approved' "
                "WHERE sku = %s AND supplier = %s",
                (sku, supplier),
            )
            conn.commit()
        return True
    except Exception as e:
        st.error(f"Error approving {supplier}:{sku} — {e}")
        return False


def reject_product(conn, sku: str, supplier: str) -> bool:
    """Reject a product classification (reset for re-classification)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE products
                   SET classification_status = NULL,
                       canonical_name = NULL,
                       canonical_key = NULL,
                       brand = NULL,
                       product_type = NULL,
                       variant = NULL,
                       size = NULL,
                       size_value = NULL,
                       size_unit = NULL,
                       category_dept = NULL,
                       category_sub = NULL,
                       classification_confidence = NULL
                   WHERE sku = %s AND supplier = %s
                """,
                (sku, supplier),
            )
            conn.commit()
        return True
    except Exception as e:
        st.error(f"Error rejecting {supplier}:{sku} — {e}")
        return False


def update_product_classification(
    conn,
    sku: str,
    supplier: str,
    brand: str | None,
    product_type: str | None,
    variant: str | None,
    size: str | None,
) -> bool:
    """Update classification fields and mark as approved."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE products
                   SET brand = %s,
                       product_type = %s,
                       variant = %s,
                       size = %s,
                       classification_status = 'approved'
                   WHERE sku = %s AND supplier = %s
                """,
                (brand, product_type, variant, size, sku, supplier),
            )
            conn.commit()
        return True
    except Exception as e:
        st.error(f"Error updating {supplier}:{sku} — {e}")
        return False


def approve_bulk_high_confidence(conn, supplier: str | None = None) -> int:
    """Approve all high-confidence pending products."""
    filters = ["classification_status = 'pending'", "classification_confidence = 'high'"]
    params = []

    if supplier:
        filters.append("supplier = %s")
        params.append(supplier)

    where_clause = " AND ".join(filters)

    try:
        with conn.cursor() as cur:
            query = f"UPDATE products SET classification_status = 'approved' WHERE {where_clause}"
            cur.execute(query, params)
            conn.commit()
            return cur.rowcount
    except Exception as e:
        st.error(f"Error bulk-approving — {e}")
        return 0


def main():
    """Revisar page — classification approval workflow."""
    st.set_page_config(
        page_title="Revisar — cocoScraper",
        page_icon="✓",
        layout="wide",
    )

    # Check database config
    if not has_database_config():
        st.error("Database not configured. Check env vars or config file.")
        return

    render_page_header("Sistema", "Revisar", "Aprobar clasificaciones de productos")

    # Get connection
    try:
        kwargs = get_psycopg2_connection_kwargs()
        conn = psycopg2.connect(**kwargs)
    except Exception as e:
        st.error(f"Database connection failed: {e}")
        return

    try:
        # Fetch all pending products for stats
        df_all = fetch_pending_products(
            conn,
            supplier=None,
            confidence=None,
            limit=500,
        )

        if df_all.empty:
            st.info("No hay productos pendientes de aprobación. ✓")
            return

        # Header stats (showing all pending, not filtered)
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total pendiente", len(df_all))
        with col2:
            high_conf = len(df_all[df_all['classification_confidence'] == 'high'])
            st.metric("Confianza alta", high_conf)
        with col3:
            low_conf = len(df_all[df_all['classification_confidence'] == 'low'])
            st.metric("Confianza baja", low_conf)

        st.divider()

        # Interactive table
        st.markdown("### Revisión manual")
        st.caption("💡 **Cómo usar**: 1) Filtra productos abajo. 2) Haz click en celdas para editar inline (Marca, Tipo, Variante, Tamaño). 3) Usa 'Aprobar todos los visibles' para aprobar de golpe, o checkea filas y usa 'Aprobar seleccionadas'.")

        # Filters
        filter_col1, filter_col2, filter_col3 = st.columns(3)

        suppliers = ["Todos"]
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT DISTINCT supplier FROM products ORDER BY supplier")
            suppliers.extend([row['supplier'] for row in cur.fetchall()])

        with filter_col1:
            selected_supplier = st.selectbox("Proveedor", suppliers, key="revisar_supplier")
            supplier_filter = None if selected_supplier == "Todos" else selected_supplier

        with filter_col2:
            confidence_options = ["Todos", "Alta", "Baja"]
            selected_confidence = st.selectbox("Confianza", confidence_options, key="revisar_confidence")
            confidence_map = {"Todos": None, "Alta": "high", "Baja": "low"}
            confidence_filter = confidence_map[selected_confidence]

        with filter_col3:
            search_text = st.text_input("Buscar por nombre (raw)", key="revisar_search")

        # Fetch with filters
        df = fetch_pending_products(
            conn,
            supplier=supplier_filter,
            confidence=confidence_filter,
            limit=500,
        )

        if search_text.strip():
            df = df[df['name'].str.contains(search_text, case=False, na=False)]

        if df.empty:
            st.info("No hay productos que coincidan con los filtros. ✓")
            return

        st.caption(f"Mostrando **{len(df)}** productos")

        # Bulk actions (applied to current filtered view)
        bulk_col1, bulk_col2 = st.columns([1, 3])
        with bulk_col1:
            if st.button("✓ Aprobar todos los visibles (alta confianza)", use_container_width=True):
                visible_high = df[df['classification_confidence'] == 'high']
                count = 0
                for _, row in visible_high.iterrows():
                    if approve_product(conn, row['sku'], row['supplier']):
                        count += 1
                st.success(f"✓ {count} producto(s) aprobado(s)")
                st.rerun()

        # Build editable table
        edit_data = []
        for idx, row in df.iterrows():
            edit_data.append({
                "SKU": row['sku'],
                "Proveedor": row['supplier'],
                "Nombre (raw)": row['name'],
                "Marca": row['brand'] or "",
                "Tipo": row['product_type'] or "",
                "Variante": row['variant'] or "",
                "Tamaño": row['size'] or "",
                "Confianza": "Alta" if row['classification_confidence'] == 'high' else "Baja",
                "Acción": "Aprobar",
            })

        edit_df = pd.DataFrame(edit_data)

        # Use AgGrid for interactive editing
        gb = GridOptionsBuilder.from_dataframe(edit_df)
        gb.configure_column("SKU", width=80, pinned="left")
        gb.configure_column("Proveedor", width=100, pinned="left")
        gb.configure_column("Nombre (raw)", width=250)
        gb.configure_column("Marca", editable=True, width=120)
        gb.configure_column("Tipo", editable=True, width=120)
        gb.configure_column("Variante", editable=True, width=150)
        gb.configure_column("Tamaño", editable=True, width=100)
        gb.configure_column("Confianza", width=80, pinned=False)
        gb.configure_column("Acción", width=120, editable=False)
        gb.configure_selection("multiple", use_checkbox=True)

        grid_options = gb.build()
        grid_response = AgGrid(
            edit_df,
            gridOptions=grid_options,
            update_mode=GridUpdateMode.VALUE_CHANGED,
            allow_unsafe_jscode=True,
            theme="streamlit",
            height=600,
        )

        st.divider()

        # Row-level actions for checked rows
        col1, col2, col3, col4 = st.columns(4)

        selected_rows = grid_response["selected_rows"]
        if isinstance(selected_rows, list) and len(selected_rows) > 0:
            with col1:
                if st.button(f"✓ Aprobar seleccionadas ({len(selected_rows)})", use_container_width=True):
                    for row in selected_rows:
                        sku = row['SKU']
                        supplier = row['Proveedor']
                        # Check if any fields were edited
                        original = df[(df['sku'] == sku) & (df['supplier'] == supplier)].iloc[0]

                        # If values changed, use update; otherwise just approve
                        if (row.get('Marca') and row['Marca'] != (original['brand'] or "")) or \
                           (row.get('Tipo') and row['Tipo'] != (original['product_type'] or "")) or \
                           (row.get('Variante') and row['Variante'] != (original['variant'] or "")) or \
                           (row.get('Tamaño') and row['Tamaño'] != (original['size'] or "")):
                            update_product_classification(
                                conn,
                                sku, supplier,
                                row.get('Marca') or None,
                                row.get('Tipo') or None,
                                row.get('Variante') or None,
                                row.get('Tamaño') or None,
                            )
                        else:
                            approve_product(conn, sku, supplier)

                    st.success(f"✓ {len(selected_rows)} producto(s) procesado(s)")
                    st.rerun()

            with col2:
                if st.button(f"✗ Rechazar seleccionadas ({len(selected_rows)})", use_container_width=True):
                    for row in selected_rows:
                        reject_product(conn, row['SKU'], row['Proveedor'])
                    st.info(f"✗ {len(selected_rows)} producto(s) rechazado(s) para re-clasificar")
                    st.rerun()

        st.divider()
        st.markdown("**Tip**: Selecciona filas, edita marcas/tipos/variantes en la tabla, y haz click en Aprobar. Los cambios se guardan automáticamente.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
