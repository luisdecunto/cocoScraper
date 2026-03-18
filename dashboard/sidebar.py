from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components
from html import escape

try:
    from .i18n import LANG_DISPLAY, get_lang, t
except ImportError:
    from i18n import LANG_DISPLAY, get_lang, t


def _sync_lang_from_url() -> None:
    """On a fresh page load (session state has no lang yet), read it from the URL."""
    if "lang" not in st.session_state:
        lang_from_url = st.query_params.get("lang")
        if isinstance(lang_from_url, list):
            lang_from_url = lang_from_url[0] if lang_from_url else None
        if isinstance(lang_from_url, str) and lang_from_url in LANG_DISPLAY:
            st.session_state.lang = lang_from_url


PRIMARY_SECTIONS = ["Dashboard", "Comparison", "History"]
SECONDARY_SECTIONS = ["Feedback", "Logs"]
NAV_ICONS = {
    "Dashboard": "&#8962;",
    "Comparison": "&#8781;",
    "History": "&#9716;",
    "Feedback": "&#9873;",
    "Logs": "&#9881;",
}
# Map English page key → i18n key for display label
_NAV_LABEL_KEY: dict[str, str] = {
    "Dashboard": "nav_dashboard",
    "Comparison": "nav_comparison",
    "History": "nav_history",
    "Feedback": "nav_feedback",
    "Logs": "nav_logs",
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
    return selected


def _build_nav_html(items: list[str], selected_page: str) -> str:
    lang = get_lang()
    links: list[str] = []
    for item in items:
        active_class = " active" if item == selected_page else ""
        icon = NAV_ICONS.get(item, "&#8226;")
        label = t(_NAV_LABEL_KEY.get(item, item))
        links.append(
            f'<a class="nav-item{active_class}" href="?section={_slug(item)}&lang={lang}" target="_self">'
            f'<span class="sidebar-nav-icon" aria-hidden="true">{icon}</span>'
            f"<span>{escape(label)}</span>"
            "</a>"
        )
    return "".join(links)


def _inject_mobile_swipe() -> None:
    """Inject a persistent hamburger button + swipe support for mobile.

    The button is appended directly to window.parent.document.body so it lives
    OUTSIDE Streamlit's CSS-transformed sidebar container. This is necessary
    because position:fixed inside a CSS transform loses viewport positioning.
    """
    components.html(
        """
        <script>
        (function() {
            const doc = window.parent.document;
            const win = window.parent;

            // Only inject once — survives Streamlit reruns because it lives in body
            if (doc.getElementById('coco-hamburger')) return;

            const ham = doc.createElement('button');
            ham.id = 'coco-hamburger';
            ham.setAttribute('aria-label', 'Menu');
            ham.textContent = '\u2630';  // ☰
            ham.style.cssText = [
                'position:fixed',
                'top:10px',
                'left:10px',
                'z-index:2147483647',
                'background:#ffffff',
                'border:1px solid #d9d9d9',
                'border-radius:4px',
                'padding:7px 11px',
                'font-size:16px',
                'line-height:1',
                'cursor:pointer',
                'color:#1f1f1f',
                'box-shadow:0 1px 4px rgba(0,0,0,0.12)',
                'display:none',
            ].join(';');
            doc.body.appendChild(ham);

            function showHam() {
                ham.style.display = win.innerWidth <= 768 ? 'block' : 'none';
            }
            showHam();
            win.addEventListener('resize', showHam);

            function toggle() {
                // Click Streamlit's own collapse control (it's in DOM even when off-screen)
                const ctrl = doc.querySelector('[data-testid="collapsedControl"]');
                if (ctrl) { ctrl.click(); return; }
                // Fallback: flip aria-expanded directly
                const sidebar = doc.querySelector('[data-testid="stSidebar"]');
                if (sidebar) {
                    const open = sidebar.getAttribute('aria-expanded') === 'true';
                    sidebar.setAttribute('aria-expanded', open ? 'false' : 'true');
                }
            }

            ham.addEventListener('click', toggle);

            // Swipe right from left edge → open; swipe left → close
            let tx = 0, ty = 0;
            doc.addEventListener('touchstart', function(e) {
                tx = e.touches[0].clientX;
                ty = e.touches[0].clientY;
            }, { passive: true });
            doc.addEventListener('touchend', function(e) {
                const dx = e.changedTouches[0].clientX - tx;
                const dy = e.changedTouches[0].clientY - ty;
                if (Math.abs(dy) > Math.abs(dx) * 1.5) return;
                const sidebar = doc.querySelector('[data-testid="stSidebar"]');
                const open = sidebar ? sidebar.getAttribute('aria-expanded') === 'true' : false;
                if (dx > 50 && tx < 60 && !open) toggle();
                if (dx < -50 && open) toggle();
            }, { passive: true });
        })();
        </script>
        """,
        height=0,
        scrolling=False,
    )


def render_sidebar(
    page_meta: dict[str, dict[str, str]],
    workspace_snapshot: dict[str, str] | None = None,
    snapshot_error: str | None = None,
) -> str:
    _sync_lang_from_url()
    options = list(page_meta.keys())
    selected_page = _resolve_selected_page(options)

    primary_items = [item for item in PRIMARY_SECTIONS if item in options]
    secondary_items = [item for item in SECONDARY_SECTIONS if item in options]
    fallback_items = [item for item in options if item not in primary_items and item not in secondary_items]
    if fallback_items:
        primary_items.extend(fallback_items)

    with st.sidebar:
        st.markdown(
            f"""
            <section class="sidebar-brand">
                <p class="sidebar-brand-title">cocoScraper</p>
                <p class="sidebar-brand-subtitle">{escape(t("brand_subtitle"))}</p>
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
                        <strong>{escape(t("meta_products"))}</strong>
                        <span>{escape(workspace_snapshot.get("products", "0"))}</span>
                    </div>
                    <div class="sidebar-meta-row">
                        <strong>{escape(t("meta_suppliers"))}</strong>
                        <span>{escape(workspace_snapshot.get("suppliers", "0"))}</span>
                    </div>
                    <div class="sidebar-meta-row">
                        <strong>{escape(t("meta_updated"))}</strong>
                        <span>{escape(workspace_snapshot.get("updated", t("not_available")))}</span>
                    </div>
                </section>
                """,
                unsafe_allow_html=True,
            )
        elif snapshot_error:
            st.caption(snapshot_error)
        else:
            st.caption(t("not_available"))

        if st.button(t("btn_refresh"), use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        # Language selector
        st.markdown('<div class="sidebar-divider"></div>', unsafe_allow_html=True)
        lang_options = list(LANG_DISPLAY.keys())
        lang_labels = list(LANG_DISPLAY.values())
        current_lang = get_lang()
        current_index = lang_options.index(current_lang) if current_lang in lang_options else 0
        selected_label = st.selectbox(
            t("lang_label"),
            lang_labels,
            index=current_index,
            key="lang_selectbox",
            label_visibility="collapsed",
        )
        selected_lang = lang_options[lang_labels.index(selected_label)]
        if selected_lang != current_lang:
            st.session_state.lang = selected_lang
            st.query_params["lang"] = selected_lang
            st.rerun()

        page_note = page_meta.get(selected_page, {}).get("sidebar")
        if page_note:
            st.markdown(
                f'<p class="sidebar-footer-note">{escape(page_note)}</p>',
                unsafe_allow_html=True,
            )

    # Inject mobile swipe JS (height=0 iframe, same-origin)
    _inject_mobile_swipe()

    return selected_page
