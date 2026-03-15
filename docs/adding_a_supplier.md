# Adding a New Supplier

Short reference. For the full implementation prompt, use `prompts/04_new_supplier_template.md`.

---

## Decision checklist (before writing any code)

Answer these in `analysis/<supplier_id>/NOTES.md`:

1. **Does the site render prices in HTML or via JavaScript?**
   Check with DevTools → disable JS → reload. If prices disappear: Playwright.
   If prices still show: httpx + BeautifulSoup.

2. **Does it have a JSON API?**
   Check DevTools → Network tab → XHR/Fetch while browsing a category.
   If you see JSON responses with product data: use the API directly.
   This is the fastest and most stable approach when available.

3. **Is login required?**
   Load a category page without login. Are prices shown?
   If yes: no login needed.
   If no / prices are hidden / different: login required.

4. **What's the price format?**
   Argentine: `$1.234,56` (dot=thousands, comma=decimal)
   Standard: `$1,234.56`
   Other: describe it.

5. **What's the URL/pagination pattern?**
   Navigate through a category. Note the URL changes across pages.

---

## Steps

1. Recon → `analysis/<id>/NOTES.md`
2. Selector debug script → `analysis/<id>/selector_debug.py`
3. Implement → `scraper/suppliers/<id>.py` (extend `BaseSupplier`)
4. Register → add entry to `SUPPLIERS` in `scraper/config.py`
5. Add credentials → `.env.example` (not `.env`)
6. Test single category
7. Verify auth (if applicable)
8. Full scrape + check run_log
9. Run comparison export
10. Update `scraper/CLAUDE.md` + root `CLAUDE.md` status

---

## Files created per supplier

| File | Purpose |
|---|---|
| `analysis/<id>/NOTES.md` | Recon notes, approach decision, corrected selectors |
| `analysis/<id>/selector_debug.py` | Throwaway debug script |
| `scraper/suppliers/<id>.py` | Production implementation |

---

## Rules

- `analysis/` files are throwaway. Never import them from production code.
- Each supplier class must implement all four `BaseSupplier` abstract methods.
- A supplier is not done until `scrape_category` returns valid products and
  `run_log` shows `snapshots_written > 0` after a full run.
- If the approach requires Playwright, add `playwright` to `requirements_playwright.txt`,
  not `requirements.txt`. Document why in `NOTES.md`.
