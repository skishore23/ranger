"""Firecrawl API integration with graceful fallbacks."""

from __future__ import annotations

import os
from typing import Dict, List

import httpx


DEFAULT_ENDPOINT = "https://api.firecrawl.dev/v1/search"


def fetch_sources(
    query: str,
    *,
    api_key: str | None,
    max_results: int = 5,
    timeout: float = 15.0,
) -> List[Dict[str, object]]:
    """Search Firecrawl for the query, returning simplified source dicts."""

    if not api_key:
        raise RuntimeError("FIRECRAWL_KEY is required for deep research.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {"query": query, "num_results": max_results}

    response = httpx.post(DEFAULT_ENDPOINT, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    data = response.json()

    results = data.get("results") or data.get("documents") or []
    simplified: List[Dict[str, object]] = []
    for item in results[:max_results]:
        simplified.append(
            {
                "query": query,
                "title": item.get("title") or item.get("name") or f"Result for {query}",
                "url": item.get("url") or item.get("link") or "",
                "snippet": item.get("snippet") or item.get("summary") or "",
                "content": item.get("content") or item.get("raw_content") or "",
                "provider": "firecrawl",
            }
        )

    if not simplified:
        raise RuntimeError(f"Firecrawl returned no results for query: {query}")

    return simplified


def get_api_key() -> str | None:
    return os.getenv("FIRECRAWL_KEY")
