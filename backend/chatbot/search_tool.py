import logging
from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)

def perform_web_search(query: str, max_results: int = 5) -> str:
    """
    Performs a web search via DuckDuckGo and returns a formatted string.
    Uses DDGS directly to avoid RuntimeWarning about renaming.
    """
    try:
        logger.info(f"[search_tool] Searching for: {query}")
        # Using DDGS as a context manager is recommended
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            
            if not results:
                logger.warning(f"[search_tool] No results found for query: {query}")
                return "Aucun résultat trouvé sur le web."
            
            formatted_results = []
            for i, r in enumerate(results, 1):
                title = r.get("title", "Sans titre")
                snippet = r.get("body", "Pas de description.")
                url = r.get("href", "#")
                formatted_results.append(f"{i}. **{title}**\n   {snippet}\n   Source: {url}")
            
            return "\n\n".join(formatted_results)
            
    except Exception as e:
        logger.error(f"[search_tool] Search failed: {e}")
        return f"Désolé, la recherche web a échoué : {str(e)}"
