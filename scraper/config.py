"""
Supplier registry.
Add new suppliers to the SUPPLIERS list.
Each entry maps to a class in scraper/suppliers/<id>.py.
"""

import os
from dotenv import load_dotenv

load_dotenv()

SUPPLIERS: list[dict] = [
    {
        "id": "maxiconsumo",
        "short_code": "mx",
        "class": "MaxiconsumoSupplier",
        "module": "scraper.suppliers.maxiconsumo",
        "base_url": "https://maxiconsumo.com/sucursal_moreno",
        "requires_login": True,
        "login_page_url": "https://maxiconsumo.com/sucursal_moreno/customer/account/login/",
        "login_post_url": "https://maxiconsumo.com/sucursal_moreno/customer/account/loginPost/",
        "credentials_env": {
            "username": "MAXICONSUMO_USER",
            "password": "MAXICONSUMO_PASS",
        },
        "selectors": {
            "product_item": ".product-item",
            "name":         ".product-item-link",
            "sku":          ".product-sku",
            "prices":       ".price-including-tax .price",
            "stock":        ".stock-status",
            "next_page":    'a[title="Siguiente"]',
        },
        # Empty = auto-discover from homepage nav
        "category_urls": [],
        # Concurrent requests for this supplier
        "concurrency": 8,
    },
    {
        "id": "santamaria",
        "short_code": "sm",
        "class": "SantaMariaSupplier",
        "module": "scraper.suppliers.santamaria",
        "base_url": "https://tienda.santamariasa.com.ar/comercio",
        "requires_login": True,
        "login_page_url": "https://tienda.santamariasa.com.ar/comercio/login.php",
        "login_post_url": "https://tienda.santamariasa.com.ar/comercio/login.php?action=process",
        "credentials_env": {
            "username": "SANTAMARIA_USER",
            "password": "SANTAMARIA_PASS",
        },
        "selectors": {
            # Verified 2026-03-10 via selector_debug.py
            # productListTable is a <div>, rows interleave product <tr> and nested form <tr>
            # product rows are filtered in _parse_product by presence of product_link
            "product_item":   ".productListTable tr",
            "name":           "td:nth-child(2) a",
            "price":          "td:nth-child(4)",
            "product_link":   "a[href*='product_info.php']",
            # next_page handled by _find_next_page (looks for a[href*='page='] with text '>')
            "next_page":      None,
        },
        "category_urls": [],
        # Top-level cPath IDs to skip during discovery.
        # OFERTAS (90): weekly/daily deals — products are duplicates of regular categories.
        "skip_top_categories": ["90"],
        "concurrency": 6,
        "http_verify_ssl": False,
    },
    {
        "id": "luvik",
        "short_code": "lv",
        "class": "LuvikSupplier",
        "module": "scraper.suppliers.luvik",
        "base_url": "https://tiendaluvik.com.ar",
        "requires_login": False,  # Public Shopify store — verified 2026-03-10
        "credentials_env": {
            "username": "LUVIK_USER",
            "password": "LUVIK_PASS",
        },
        "selectors": {},        # Not used — JSON API
        "category_urls": [],
        "concurrency": 2,       # HTML scraping — lower limit than JSON API to avoid 429s
        "http_verify_ssl": False,  # tiendaluvik.com.ar SSL cert doesn't cover subdomain
    },
    {
        "id": "vital",
        "short_code": "vt",
        "class": "VitalSupplier",
        "module": "scraper.suppliers.vital",
        "base_url": "https://tiendaonline.vital.com.ar",
        "requires_login": True,
        "credentials_env": {
            "username": "VITAL_USER",
            "password": "VITAL_PASS",
        },
        "selectors": {},        # Not used — IS API
        "category_urls": [],    # Single sentinel "_api_" returned by discover_categories
        "concurrency": 8,
    },
    {
        "id": "nini",
        "short_code": "nn",
        "class": "NiniSupplier",
        "module": "scraper.suppliers.nini",
        "base_url": "http://ecommerce.nini.com.ar:8081",
        "requires_login": True,
        "credentials_env": {
            "username": "NINI_USER",
            "password": "NINI_PASS",
        },
        "selectors": {},        # Not used — JSON API
        "category_urls": [],    # Discovered dynamically from department/sector API
        "concurrency": 6,
    },
]


def get_supplier_config(supplier_id: str) -> dict:
    """Return config dict for a supplier id. Raises ValueError if not found."""
    for s in SUPPLIERS:
        if s["id"] == supplier_id:
            return s
    raise ValueError(f"Unknown supplier: '{supplier_id}'. Available: {[s['id'] for s in SUPPLIERS]}")


def get_short_code(supplier_id: str) -> str:
    """Return the short code (e.g. 'mx', 'lv') for a supplier id."""
    config = get_supplier_config(supplier_id)
    return config["short_code"]


def load_supplier_class(config: dict):
    """Dynamically import and instantiate the supplier class from config."""
    import importlib
    module = importlib.import_module(config["module"])
    cls = getattr(module, config["class"])
    return cls(config)
