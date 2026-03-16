from __future__ import annotations

from html import escape

import streamlit as st


PRIMARY_SECTIONS = ["Dashboard", "Comparison", "History"]
SECONDARY_SECTIONS = ["Logs"]
NAV_ICONS = {
    "Dashboard": "&#8962;",
    "Comparison": "&#8781;",
    "History": "&#9716;",
    "Logs": "&#9881;",
}


def _slug(label: str) -> str:
    return label.strip().lower().replace(" ", "-")


def _resolve_selected_page(options: list[str]) -> str:
    slug_to_page = {_slug(option): option for option in options}
    query_value = st.query_params.get("section")

    if isinstance(query_value, list):
        query_value = query_value[0] if query_value else None

    if isinstance(query_value, str) and query_value in slug_to_page:
        selected = slug_to_page[query_value]
    else:
        selected = st.session_state.get("dashboard_page", options[0])
        if selected not in options:
            selected = options[0]

    st.session_state.dashboard_page = selected
    expected_slug = _slug(selected)
    if query_value != expected_slug:
        st.query_params["section"] = expected_slug

    return selected


def _build_nav_html(items: list[str], selected_page: str) -> str:
    links: list[str] = []
    for item in items:
        active_class = " active" if item == selected_page else ""
        icon = NAV_ICONS.get(item, "&#8226;")
        links.append(
            f'<a class="nav-item{active_class}" href="?section={_slug(item)}" target="_self">'
            f'<span class="sidebar-nav-icon" aria-hidden="true">{icon}</span>'
            f"<span>{escape(item)}</span>"
            "</a>"
        )
    return "".join(links)


def render_sidebar(
    page_meta: dict[str, dict[str, str]],
    workspace_snapshot: dict[str, str] | None = None,
    snapshot_error: str | None = None,
) -> str:
    options = list(page_meta.keys())
    selected_page = _resolve_selected_page(options)

    primary_items = [item for item in PRIMARY_SECTIONS if item in options]
    secondary_items = [item for item in SECONDARY_SECTIONS if item in options]
    fallback_items = [item for item in options if item not in primary_items and item not in secondary_items]
    if fallback_items:
        primary_items.extend(fallback_items)

    with st.sidebar:
        st.markdown(
            """
            <section class="sidebar-brand">
                <p class="sidebar-brand-title">cocoScraper</p>
                <p class="sidebar-brand-subtitle">Supplier pricing workspace</p>
            </section>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            f'<nav class="sidebar-nav">{_build_nav_html(primary_items, selected_page)}</nav>',
            unsafe_allow_html=True,
        )

        st.markdown('<div class="sidebar-divider"></div>', unsafe_allow_html=True)

        if secondary_items:
            st.markdown(
                f'<nav class="sidebar-nav">{_build_nav_html(secondary_items, selected_page)}</nav>',
                unsafe_allow_html=True,
            )

        if workspace_snapshot:
            st.markdown(
                f"""
                <section class="sidebar-meta">
                    <div class="sidebar-meta-row">
                        <strong>Products</strong>
                        <span>{escape(workspace_snapshot.get("products", "0"))}</span>
                    </div>
                    <div class="sidebar-meta-row">
                        <strong>Suppliers</strong>
                        <span>{escape(workspace_snapshot.get("suppliers", "0"))}</span>
                    </div>
                    <div class="sidebar-meta-row">
                        <strong>Updated</strong>
                        <span>{escape(workspace_snapshot.get("updated", "Not available"))}</span>
                    </div>
                </section>
                """,
                unsafe_allow_html=True,
            )
        elif snapshot_error:
            st.caption(snapshot_error)
        else:
            st.caption("Workspace snapshot is not available yet.")

        if st.button("Refresh data", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        page_note = page_meta.get(selected_page, {}).get("sidebar")
        if page_note:
            st.markdown(
                f'<p class="sidebar-footer-note">{escape(page_note)}</p>',
                unsafe_allow_html=True,
            )

    return selected_page
