"""HTTP client for Enterprise Knowledge Hub (EKH) Wikipedia search."""

import logging
from typing import Dict, List, Optional

import aiohttp

from open_webui.env import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["GROUNDING"])


async def search_wikipedia(
    query: str,
    base_url: str,
    limit: int = 15,
    timeout_seconds: float = 5.0,
    api_key: Optional[str] = None,
    source: Optional[str] = None,
) -> List[Dict]:
    """Return EKH search result rows, or [] on any failure so chat is never blocked."""
    if not base_url:
        log.warning("EKH_BASE_URL is not set; skipping EKH search")
        return []

    url = f"{base_url.rstrip('/')}/database/wikipedia/search"
    params = {"query": query, "limit": str(limit)}
    if source:
        params["source"] = source
    headers = {"X-API-Key": api_key} if api_key else {}

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_seconds)
        ) as session:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status != 200:
                    log.warning(f"EKH search returned HTTP {resp.status}")
                    return []
                payload = await resp.json()
    except Exception as e:
        # Log the error type only; the query is user content.
        log.warning(f"EKH search failed: {type(e).__name__}")
        return []

    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        log.warning("EKH search returned an unexpected payload")
        return []
    return results
