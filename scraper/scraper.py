"""
Scraper orchestrator.
Loops over suppliers, categories, and pages. Manages concurrency.
"""

import asyncio
import logging

import httpx

from scraper.config import SUPPLIERS, get_supplier_config, load_supplier_class
from scraper.db import (
    finish_run,
    start_run,
    update_run_categories_total,
    upsert_product,
    upsert_snapshot,
)

logger = logging.getLogger(__name__)


async def run_supplier(supplier_id: str, pool, max_products: int | None = None) -> None:
    """Run a full scrape for one supplier. Stops after max_products if set."""
    config = get_supplier_config(supplier_id)
    supplier = load_supplier_class(config)
    sem = asyncio.Semaphore(config.get("concurrency", 10))
    run_id = await start_run(pool, supplier_id)
    products_scraped = 0
    snapshots_written = 0
    categories_done = 0

    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
            verify=config.get("http_verify_ssl", True),
        ) as client:

            if config["requires_login"]:
                await supplier.login(client)
                logger.info(f"{supplier_id}: login successful")

            urls = config["category_urls"] or await supplier.discover_categories(client)
            logger.info(f"{supplier_id}: {len(urls)} categories found")
            await update_run_categories_total(pool, run_id, len(urls))

            for url in urls:
                if max_products and products_scraped >= max_products:
                    logger.info(f"{supplier_id}: reached max_products limit ({max_products}), stopping.")
                    break
                try:
                    products = await supplier.scrape_category(client, url, sem)
                    for p in products:
                        await upsert_product(pool, supplier_id, p)
                        wrote = await upsert_snapshot(
                            pool, p["sku"], supplier_id,
                            p["price_unit"], p["price_bulk"], p["stock"],
                        )
                        products_scraped += 1
                        if wrote:
                            snapshots_written += 1
                    categories_done += 1
                    logger.info(f"{supplier_id}: {url} — {len(products)} products")
                except Exception as e:
                    logger.warning(f"{supplier_id}: failed on {url} — {e}")

                await asyncio.sleep(2)

    except Exception as e:
        logger.error(f"{supplier_id}: run aborted — {e}")
        await finish_run(pool, run_id, "failed", categories_done,
                         products_scraped, snapshots_written, str(e))
        return

    await finish_run(pool, run_id, "success", categories_done,
                     products_scraped, snapshots_written)

    if snapshots_written == 0:
        logger.error(
            f"{supplier_id}: ALERT — run completed but 0 snapshots written. "
            "Login may have failed silently, or site structure changed."
        )
    else:
        logger.info(f"{supplier_id}: done — {products_scraped} products, "
                    f"{snapshots_written} snapshots written")


async def run_all(pool) -> None:
    """Run scrape for all suppliers defined in config."""
    for s in SUPPLIERS:
        await run_supplier(s["id"], pool)
