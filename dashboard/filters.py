from __future__ import annotations

import unicodedata
from typing import Optional

import pandas as pd
import streamlit as st


NO_STOCK_VALUES = {"sin stock", "disponibilidad critica"}
DEFAULT_VISIBLE_COLUMNS = [
    "product_id",
    "canonical_name",
    "name",
    "brand",
    "supplier",
    "size",
    "price_unit",
    "price_bulk",
    "stock",
    "scraped_at",
]
ALL_COLUMNS = [
    "product_id",
    "canonical_name",
    "name",
    "brand",
    "product_type",
    "variant",
    "size",
    "category_dept",
    "category_sub",
    "supplier",
    "price_unit",
    "price_bulk",
    "stock",
    "scraped_at",
]


def normalize_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""

    text = str(value).strip().lower()
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _clear_filter_state(prefix: str) -> None:
    for key in list(st.session_state.keys()):
        if key.startswith(prefix):
            del st.session_state[key]


def _slider_step(min_value: float, max_value: float) -> float:
    spread = max_value - min_value
    if spread <= 5:
        return 0.1
    if spread <= 25:
        return 0.25
    if spread <= 100:
        return 1.0
    if spread <= 1000:
        return 5.0
    return 25.0


def numeric_range_filter(
    df: pd.DataFrame,
    column: str,
    label: Optional[str] = None,
    key_prefix: str = "browse",
) -> tuple[pd.DataFrame, tuple[float, float] | None]:
    label = label or column
    numeric_values = pd.to_numeric(df[column], errors="coerce").dropna()
    if numeric_values.empty:
        st.caption(f"{label}: no values available")
        return df, None

    min_value = float(numeric_values.min())
    max_value = float(numeric_values.max())
    if min_value == max_value:
        st.caption(f"{label}: fixed at {min_value:,.2f}")
        return df, None

    selected_min, selected_max = st.slider(
        label,
        min_value=min_value,
        max_value=max_value,
        value=(min_value, max_value),
        step=_slider_step(min_value, max_value),
        key=f"{key_prefix}_{column}_range",
    )

    numeric_column = pd.to_numeric(df[column], errors="coerce")
    filtered = df[(numeric_column >= selected_min) & (numeric_column <= selected_max)]
    return filtered, (selected_min, selected_max)


def text_multi_filter(
    df: pd.DataFrame,
    column: str,
    label: Optional[str] = None,
    key_prefix: str = "browse",
) -> tuple[pd.DataFrame, list[str]]:
    label = label or column
    options = sorted(df[column].dropna().astype(str).unique().tolist())
    if not options:
        st.caption(f"{label}: no values available")
        return df, []

    selected = st.multiselect(
        label,
        options,
        key=f"{key_prefix}_{column}",
        placeholder=f"Select {label.lower()}",
    )
    if selected:
        return df[df[column].isin(selected)], selected
    return df, []


def formula_filter(
    df: pd.DataFrame,
    label: str = "Custom rule",
    key_prefix: str = "browse",
) -> tuple[pd.DataFrame, str | None]:
    formula = st.text_input(
        label,
        placeholder="Example: price_unit < 5 and price_bulk >= 20",
        key=f"{key_prefix}_formula",
    )

    if not formula:
        return df, None

    try:
        result = df.eval(formula, local_dict={}, global_dict={})
        return df[result], formula
    except Exception as exc:
        st.error(f"Invalid custom rule: {exc}")
        return df, None


def column_visibility_filter(
    available_columns: list[str],
    key_prefix: str = "browse",
) -> list[str]:
    visible = st.multiselect(
        "Visible columns",
        available_columns,
        default=DEFAULT_VISIBLE_COLUMNS,
        key=f"{key_prefix}_columns",
        placeholder="Choose the columns to display",
    )
    return visible or DEFAULT_VISIBLE_COLUMNS


class AdvancedFilterPanel:
    def __init__(self, df: pd.DataFrame):
        self.original_df = df.copy()
        self.df = df.copy()

    def render(self) -> tuple[pd.DataFrame, list[str], list[str]]:
        active_filters: list[str] = []

        controls = st.columns([2.25, 1.45, 1.0, 0.75])

        with controls[0]:
            search = st.text_input(
                "Search catalog",
                placeholder="Search product id, name, brand, or product type",
                key="browse_search",
            )
            if search:
                mask = (
                    self.df["product_id"].astype(str).str.contains(search, case=False, na=False)
                    | self.df["name"].astype(str).str.contains(search, case=False, na=False)
                    | self.df["brand"].astype(str).str.contains(search, case=False, na=False)
                    | self.df["product_type"].astype(str).str.contains(search, case=False, na=False)
                )
                self.df = self.df[mask]
                active_filters.append(f'Search: "{search}"')

        with controls[1]:
            self.df, selected_suppliers = text_multi_filter(
                self.df,
                "supplier",
                "Supplier",
                "browse",
            )
            if selected_suppliers:
                active_filters.append(f"Supplier: {len(selected_suppliers)}")

        with controls[2]:
            hide_unavailable = st.toggle(
                "Hide unavailable",
                value=True,
                key="browse_hide_unavailable",
            )
            if hide_unavailable:
                normalized_stock = self.df["stock"].map(normalize_text)
                self.df = self.df[~normalized_stock.isin(NO_STOCK_VALUES)]
                active_filters.append("Availability: in stock only")

        with controls[3]:
            st.caption(" ")
            if st.button("Reset", use_container_width=True, key="browse_reset"):
                _clear_filter_state("browse_")
                st.rerun()

        with st.expander("More filters", expanded=False):
            row_one = st.columns(3)
            with row_one[0]:
                self.df, selected_brands = text_multi_filter(
                    self.df,
                    "brand",
                    "Brand",
                    "browse",
                )
                if selected_brands:
                    active_filters.append(f"Brand: {len(selected_brands)}")

            with row_one[1]:
                self.df, selected_departments = text_multi_filter(
                    self.df,
                    "category_dept",
                    "Department",
                    "browse",
                )
                if selected_departments:
                    active_filters.append(f"Department: {len(selected_departments)}")

            with row_one[2]:
                stock_options = sorted(self.df["stock"].dropna().astype(str).unique().tolist())
                selected_stock = st.multiselect(
                    "Stock status",
                    stock_options,
                    key="browse_stock",
                    placeholder="Select stock states",
                )
                if selected_stock:
                    self.df = self.df[self.df["stock"].isin(selected_stock)]
                    active_filters.append(f"Stock status: {len(selected_stock)}")

            row_two = st.columns(2)
            with row_two[0]:
                self.df, unit_range = numeric_range_filter(
                    self.df,
                    "price_unit",
                    "Unit price range",
                    "browse",
                )
                if unit_range:
                    active_filters.append(
                        f"Unit price: ${unit_range[0]:,.2f}-${unit_range[1]:,.2f}"
                    )

            with row_two[1]:
                self.df, bulk_range = numeric_range_filter(
                    self.df,
                    "price_bulk",
                    "Bulk price range",
                    "browse",
                )
                if bulk_range:
                    active_filters.append(
                        f"Bulk price: ${bulk_range[0]:,.2f}-${bulk_range[1]:,.2f}"
                    )

            row_three = st.columns(3)
            with row_three[0]:
                if "category_sub" in self.df.columns:
                    self.df, selected_subcategories = text_multi_filter(
                        self.df,
                        "category_sub",
                        "Subcategory",
                        "browse",
                    )
                    if selected_subcategories:
                        active_filters.append(f"Subcategory: {len(selected_subcategories)}")

            with row_three[1]:
                if "product_type" in self.df.columns:
                    self.df, selected_types = text_multi_filter(
                        self.df,
                        "product_type",
                        "Product type",
                        "browse",
                    )
                    if selected_types:
                        active_filters.append(f"Product type: {len(selected_types)}")

            with row_three[2]:
                self.df, formula = formula_filter(self.df, "Custom rule", "browse")
                if formula:
                    active_filters.append("Custom rule applied")

            visible_columns = column_visibility_filter(ALL_COLUMNS, "browse")

        return self.df, visible_columns, active_filters
