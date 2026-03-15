"""
Base supplier class.
Every supplier implementation must extend this and implement all abstract methods.
The internals of each method can use completely different approaches
(httpx, Playwright, JSON API, etc.) — the contract only defines the interface.
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Optional
import httpx


class BaseSupplier(ABC):
    """Abstract base class for all supplier scrapers."""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    async def login(self, client: httpx.AsyncClient) -> None:
        """
        Authenticate with the supplier website.
        No-op if the supplier requires no login.
        Must raise RuntimeError if login fails.
        """

    @abstractmethod
    async def discover_categories(self, client: httpx.AsyncClient) -> list[str]:
        """
        Return a list of all leaf category URLs to scrape.
        Called once per run if config['category_urls'] is empty.
        """

    @abstractmethod
    async def scrape_category(
        self,
        client: httpx.AsyncClient,
        url: str,
        sem: asyncio.Semaphore,
    ) -> list[dict]:
        """
        Scrape all products from a category URL, including pagination.

        Returns a list of dicts, each with keys:
            sku          (str)
            name         (str)
            url          (str)
            category     (str)
            price_unit   (float | None)
            price_bulk   (float | None)
            stock        (str)
        """

    @abstractmethod
    def parse_price(self, raw: str) -> Optional[float]:
        """Parse a raw price string to float. Return None on failure."""

    def _parse_argentine_price(self, raw: str) -> Optional[float]:
        """
        Parse Argentine number format: $1.234,56 -> 1234.56
        Dot is thousands separator, comma is decimal.
        Use this as default implementation for Argentine suppliers.
        """
        try:
            cleaned = (
                raw.replace("$", "")
                   .replace("\xa0", "")
                   .replace(".", "")
                   .replace(",", ".")
                   .strip()
            )
            return float(cleaned) if cleaned else None
        except (ValueError, AttributeError):
            return None
