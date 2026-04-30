import logging

from open_webui.retrieval.web.main import SearchResult
from open_webui.retrieval.web.utils import request_json_with_timeout
from open_webui.env import SRC_LOG_LEVELS

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["RAG"])


def search_tavily(api_key: str, query: str, count: int) -> list[SearchResult]:
    """Search using Tavily's Search API and return the results as a list of SearchResult objects.

    Args:
        api_key (str): A Tavily Search API key
        query (str): The query to search for

    Returns:
        list[SearchResult]: A list of search results
    """
    url = "https://api.tavily.com/search"
    data = {"query": query, "api_key": api_key}

    json_response = request_json_with_timeout(
        "POST",
        url,
        provider_name="Tavily search",
        json=data,
    )

    raw_search_results = json_response.get("results", [])

    return [
        SearchResult(
            link=result["url"],
            title=result.get("title", ""),
            snippet=result.get("content"),
        )
        for result in raw_search_results[:count]
    ]
