"""
Santa Maria — selector debug script.
Step 2: verify login + find product item selectors in a category page.
Step 3: check product detail page for SKU/barcode field.
Run from cocoScraper/ root:
    python analysis/santamaria/selector_debug.py
"""

import asyncio
import os
import warnings

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()
warnings.filterwarnings("ignore")

BASE = "https://tienda.santamariasa.com.ar/comercio"


async def step2_login_and_category(client: httpx.AsyncClient) -> None:
    """Verify login and inspect a known leaf category page."""

    # 1. Fetch login page, extract hidden fields
    r = await client.get(f"{BASE}/login.php")
    soup = BeautifulSoup(r.text, "lxml")
    form = soup.select_one("form")
    if not form:
        print("ERROR: no <form> found on login page")
        return

    hidden = {i["name"]: i.get("value", "") for i in form.select("input[type=hidden]")}
    action = form.get("action", "login.php")
    if not action.startswith("http"):
        action = f"{BASE}/{action.lstrip('/')}"

    print(f"Login form action: {action}")
    print(f"Hidden fields: {hidden}")

    # 2. POST login
    payload = {
        **hidden,
        "email_address": os.getenv("SANTAMARIA_USER"),
        "password":      os.getenv("SANTAMARIA_PASS"),
    }
    r = await client.post(action, data=payload)
    print(f"\nAfter login URL: {r.url}")
    login_ok = "login.php" not in str(r.url)
    print(f"Login success: {login_ok}")
    if not login_ok:
        print("LOGIN FAILED — check SANTAMARIA_USER / SANTAMARIA_PASS in .env")
        return

    # 3. Fetch a known leaf category
    cat_url = f"{BASE}/index.php?cPath=1_101"
    r = await client.get(cat_url)
    soup = BeautifulSoup(r.text, "lxml")
    print(f"\nCategory page URL: {r.url}")

    # Try common osCommerce product listing selectors
    found = False
    for selector in [
        ".productListing-odd",
        ".productListing-even",
        "td.productListing-data",
        ".product-listing tr",
        "table.productListing tr",
        "tr.productListing-odd",
        "tr.productListing-even",
    ]:
        items = soup.select(selector)
        if items:
            print(f"\nSelector '{selector}' → {len(items)} items")
            print("--- First item HTML (first 1500 chars) ---")
            print(items[0].prettify()[:1500])
            found = True
            break

    if not found:
        print("\nNo product items found with standard selectors.")
        print("--- Body snippet (first 3000 chars) ---")
        print(soup.body.prettify()[:3000] if soup.body else r.text[:3000])

    # 4. Check pagination
    pages = soup.select("a[href*='page=']")
    print(f"\nPagination links ({len(pages)} found):")
    for a in pages[:8]:
        print(f"  {a.get('href', '')} — text: {a.get_text(strip=True)!r}")

    # 5. Print all unique class names present in the category body (helps find selectors)
    all_classes: set[str] = set()
    for tag in soup.body.find_all(True) if soup.body else []:
        for cls in tag.get("class", []):
            all_classes.add(cls)
    print(f"\nAll CSS classes on category page ({len(all_classes)} unique):")
    print(sorted(all_classes))


async def step3_product_detail(client: httpx.AsyncClient) -> None:
    """Inspect a product detail page to find SKU/barcode field."""

    # Try a product ID — 463 from NOTES.md, adjust if needed
    for pid in [463, 1, 100, 200]:
        url = f"{BASE}/product_info.php?products_id={pid}"
        r = await client.get(url)
        if "login.php" in str(r.url):
            print(f"\nproducts_id={pid}: redirected to login (session expired?)")
            continue
        soup = BeautifulSoup(r.text, "lxml")
        title = soup.title.string if soup.title else "(no title)"
        print(f"\n--- Product detail page: products_id={pid} ({title}) ---")
        print(soup.body.prettify()[:4000] if soup.body else r.text[:4000])
        break  # only need one example


async def main() -> None:
    async with httpx.AsyncClient(
        follow_redirects=True,
        verify=False,
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
        timeout=httpx.Timeout(30.0),
    ) as client:
        await step2_login_and_category(client)
        print("\n" + "=" * 60)
        print("STEP 3 — Product detail page")
        print("=" * 60)
        await step3_product_detail(client)


if __name__ == "__main__":
    asyncio.run(main())
