"""
CLI entrypoint for the scraper.

Usage:
    python scraper/main.py db init
    python scraper/main.py discover --supplier maxiconsumo
    python scraper/main.py scrape [--supplier <id>]
    python scraper/main.py export latest [--output <path>]
    python scraper/main.py export comparison [--output-csv <path>] [--output-xlsx <path>]
    python scraper/main.py export history --sku <sku> --supplier <id> [--output <path>]
    python scraper/main.py schedule
"""

import argparse
import asyncio
import logging
import os
import sys

import httpx

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
_file_handler = logging.FileHandler("logs/scraper.log")
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s")
)
logging.getLogger().addHandler(_file_handler)

logger = logging.getLogger(__name__)


async def async_main(args: argparse.Namespace) -> None:
    """Async entry point — dispatches to the correct handler."""
    from scraper.db import get_pool, init_schema
    from scraper.config import get_supplier_config, load_supplier_class
    from scraper.scraper import run_supplier, run_all
    from scraper.export import export_latest, export_comparison, export_history

    pool = await get_pool()

    try:
        if args.command == "db" and args.db_command == "init":
            await init_schema(pool)
            print("Database schema initialized.")

        elif args.command == "discover":
            config = get_supplier_config(args.supplier)
            supplier = load_supplier_class(config)
            async with httpx.AsyncClient(follow_redirects=True) as client:
                if config["requires_login"]:
                    await supplier.login(client)
                urls = await supplier.discover_categories(client)
            print(f"Found {len(urls)} categories:")
            for url in urls:
                print(f"  {url}")

        elif args.command == "scrape":
            max_products = getattr(args, "max_products", None)
            if args.supplier:
                await run_supplier(args.supplier, pool, max_products=max_products)
            else:
                await run_all(pool)

        elif args.command == "export":
            if args.export_command == "latest":
                path = getattr(args, "output", None) or "exports/latest_prices.csv"
                await export_latest(pool, path)
                print(f"Exported: {path}")

            elif args.export_command == "comparison":
                csv_path = getattr(args, "output_csv", None) or "exports/comparison.csv"
                xlsx_path = getattr(args, "output_xlsx", None) or "exports/comparison.xlsx"
                await export_comparison(pool, csv_path, xlsx_path)
                print(f"Exported: {csv_path}, {xlsx_path}")

            elif args.export_command == "history":
                path = (getattr(args, "output", None)
                        or f"exports/history_{args.supplier}_{args.sku}.csv")
                await export_history(pool, args.sku, args.supplier, path)
                print(f"Exported: {path}")

        elif args.command == "schedule":
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            scheduler = AsyncIOScheduler()
            scheduler.add_job(run_all, "cron", hour=6, args=[pool])
            scheduler.start()
            logger.info("Scheduler running. Daily scrape at 06:00. Ctrl+C to stop.")
            try:
                await asyncio.Event().wait()
            except (KeyboardInterrupt, SystemExit):
                scheduler.shutdown()

    finally:
        await pool.close()


def main() -> None:
    """Build the argument parser and dispatch to async_main."""
    parser = argparse.ArgumentParser(prog="scraper")
    subparsers = parser.add_subparsers(dest="command")

    # db subcommand
    db_parser = subparsers.add_parser("db")
    db_sub = db_parser.add_subparsers(dest="db_command")
    db_sub.add_parser("init")

    # discover subcommand
    discover_parser = subparsers.add_parser("discover")
    discover_parser.add_argument("--supplier", required=True)

    # scrape subcommand
    scrape_parser = subparsers.add_parser("scrape")
    scrape_parser.add_argument("--supplier", required=False)
    scrape_parser.add_argument("--max-products", dest="max_products", type=int, required=False)

    # export subcommand
    export_parser = subparsers.add_parser("export")
    export_sub = export_parser.add_subparsers(dest="export_command")

    latest_parser = export_sub.add_parser("latest")
    latest_parser.add_argument("--output", required=False)

    comparison_parser = export_sub.add_parser("comparison")
    comparison_parser.add_argument("--output-csv", dest="output_csv", required=False)
    comparison_parser.add_argument("--output-xlsx", dest="output_xlsx", required=False)

    history_parser = export_sub.add_parser("history")
    history_parser.add_argument("--sku", required=True)
    history_parser.add_argument("--supplier", required=True)
    history_parser.add_argument("--output", required=False)

    # schedule subcommand
    subparsers.add_parser("schedule")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
