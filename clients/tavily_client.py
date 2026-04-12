from __future__ import annotations

import logging

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class TavilySearchResult(BaseModel):
    """A single result returned by the Tavily Search API."""

    title: str
    url: str
    content: str
    score: float  # Tavily relevance score, 0.0–1.0


class TavilyClient:
    """Thin wrapper around the Tavily Search API.

    Encapsulates query construction and response parsing so the rest of the
    codebase works with typed TavilySearchResult objects rather than raw dicts.
    """

    def __init__(self, api_key: str) -> None:
        """
        Args:
            api_key: Tavily API key from settings.
        """
        self._api_key = api_key
        # Lazy import so the package is optional during testing without the key.
        self._client: object | None = None

    def _get_client(self) -> object:
        """Return the underlying tavily-python client, initializing on first call."""
        if self._client is None:
            from tavily import TavilyClient as _Tavily  # type: ignore[import]

            self._client = _Tavily(api_key=self._api_key)
        return self._client

    def search(self, query: str, max_results: int = 5) -> list[TavilySearchResult]:
        """Run a general search query.

        Args:
            query: Free-text search query.
            max_results: Maximum number of results to return.

        Returns:
            List of TavilySearchResult ordered by relevance score descending.
        """
        if not self._api_key:
            logger.info(
                "Tavily API key is not configured. Returning no results for query: %s",
                query,
            )
            return []

        try:
            client = self._get_client()
            response = client.search(  # type: ignore[attr-defined]
                query=query,
                max_results=max_results,
            )
        except Exception as err:
            logger.warning("Tavily search failed for query %s: %s", query, err)
            return []

        raw_results = response.get("results", []) if isinstance(response, dict) else []
        results: list[TavilySearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            try:
                results.append(
                    TavilySearchResult(
                        title=str(item.get("title", "")),
                        url=str(item.get("url", "")),
                        content=str(item.get("content", "")),
                        score=float(item.get("score", 0.0)),
                    )
                )
            except Exception:
                continue

        return sorted(results, key=lambda r: r.score, reverse=True)

    def search_supplier_context(
        self, supplier_name: str, topic: str
    ) -> list[TavilySearchResult]:
        """Search for news or announcements related to a specific supplier and topic.

        Args:
            supplier_name: Human-readable supplier name (e.g. "Acme Paper Co.").
            topic: Topic to search within (e.g. "price increase", "stock shortage").

        Returns:
            List of TavilySearchResult.
        """
        query = f"{supplier_name} {topic}"
        return self.search(query=query, max_results=5)

    def search_product_availability(
        self, sku: str, description: str
    ) -> list[TavilySearchResult]:
        """Search for current availability or discontinuation notices for a product.

        Args:
            sku: The product SKU.
            description: Human-readable product description.

        Returns:
            List of TavilySearchResult.
        """
        query = f"{sku} {description} availability discontinued shortage"
        return self.search(query=query, max_results=5)

    def search_price_changes(
        self, supplier_name: str, product_category: str
    ) -> list[TavilySearchResult]:
        """Search for publicly announced price amendments from a supplier.

        Args:
            supplier_name: Human-readable supplier name.
            product_category: Category or commodity (e.g. "office paper", "steel").

        Returns:
            List of TavilySearchResult.
        """
        query = f"{supplier_name} {product_category} price increase announcement"
        return self.search(query=query, max_results=5)
