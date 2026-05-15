import logging
from mcp.server.fastmcp import FastMCP
from duckduckgo_search import DDGS

# Configuration du logging pour le serveur MCP
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp_search_server")

# Initialisation du serveur MCP
mcp = FastMCP("BankChat Search")


@mcp.tool()
def web_search(query: str, max_results: int = 5) -> str:
    """
    Effectue une recherche sur le web via DuckDuckGo et renvoie les résultats formatés.
    """
    logger.info(f"Searching for: {query}")
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            
            if not results:
                return "Aucun résultat trouvé sur le web."
            
            formatted_results = []
            for i, r in enumerate(results, 1):
                title = r.get("title", "Sans titre")
                snippet = r.get("body", "Pas de description.")
                url = r.get("href", "#")
                formatted_results.append(f"{i}. **{title}**\n   {snippet}\n   Source: {url}")
            
            return "\n\n".join(formatted_results)
            
    except Exception as e:
        logger.error(f"Search failed: {e}")
        return f"Désolé, la recherche web a échoué : {str(e)}"

if __name__ == "__main__":
    # Le serveur s'exécute par défaut via stdio
    mcp.run()
