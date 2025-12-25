from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class WebSearchConfig:
    api_key: str
    endpoint: str = "https://api.bing.microsoft.com/v7.0/search"
    market: str = "ru-RU"
    safe_search: str = "Moderate"
    timeout_s: float = 12.0
    max_results: int = 5


def bing_web_search(query: str, cfg: WebSearchConfig, count: int | None = None) -> dict[str, Any]:
    """
    Bing Web Search (JSON). Returns compact results for LLM tool usage.

    Output:
      {
        "ok": bool,
        "query": str,
        "results": [{"title": str, "url": str, "snippet": str}, ...],
        "error": str | None
      }
    """
    q = (query or "").strip()
    if not q:
        return {"ok": False, "query": query, "results": [], "error": "Empty query."}
    if not cfg.api_key.strip():
        return {"ok": False, "query": q, "results": [], "error": "Missing Bing API key."}

    n = int(count) if count is not None else cfg.max_results
    if n < 1:
        n = 1
    if n > 10:
        n = 10  # keep responses small and predictable

    headers = {
        "Ocp-Apim-Subscription-Key": cfg.api_key,
        "Accept": "application/json",
    }
    params = {
        "q": q,
        "mkt": cfg.market,
        "safeSearch": cfg.safe_search,
        "count": n,
        "textDecorations": False,
        "textFormat": "Raw",
    }

    try:
        resp = requests.get(cfg.endpoint, headers=headers, params=params, timeout=cfg.timeout_s)
        if resp.status_code != 200:
            return {
                "ok": False,
                "query": q,
                "results": [],
                "error": f"Bing API HTTP {resp.status_code}: {resp.text[:500]}",
            }

        data = resp.json()
        items = (data.get("webPages", {}) or {}).get("value", []) or []

        results: list[dict[str, str]] = []
        for it in items[:n]:
            title = str(it.get("name", "")).strip()
            url = str(it.get("url", "")).strip()
            snippet = str(it.get("snippet", "")).strip()
            if url:
                results.append({"title": title, "url": url, "snippet": snippet})

        return {"ok": True, "query": q, "results": results, "error": None}

    except requests.RequestException as e:
        return {"ok": False, "query": q, "results": [], "error": f"Request error: {e}"}
    except ValueError as e:
        # JSON decoding error
        return {"ok": False, "query": q, "results": [], "error": f"Bad JSON: {e}"}