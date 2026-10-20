"""Online search."""

import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import requests
import wikipedia
from ddgs import DDGS

_GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=fr&gl=FR&ceid=FR:fr"

# Preferred domains per mode, injected into the DuckDuckGo query through the 'site:' operator.
_PRICE_SITES = ["amazon.fr", "cdiscount.com", "fnac.com", "ldlc.com", "boulanger.com"]
_COMPARE_SITES = ["lesnumeriques.com", "clubic.com", "01net.com", "commentcamarche.net"]


def _fetch_news(query: str, max_results: int = 5) -> str:
    """Recent news for `query` through Google News' RSS feed."""
    url = _GOOGLE_NEWS_RSS.format(query=quote_plus(query))
    response = requests.get(url, timeout=10)
    response.raise_for_status()

    root = ET.fromstring(response.content)
    items = root.findall("./channel/item")[:max_results]
    if not items:
        return f"Aucune actualité récente trouvée pour '{query}'."

    formatted = []
    for item in items:
        title = item.findtext("title", default="Sans titre")
        link = item.findtext("link", default="")
        pub_date = item.findtext("pubDate", default="")
        source_name = item.findtext("source", default="")
        header = f"• {title}" + (f" ({source_name})" if source_name else "")
        formatted.append(f"{header}\n  {pub_date}\n  Lien: {link}")

    return f"Actualités pour '{query}' :\n" + "\n\n".join(formatted)


def _fetch_web(query: str, max_results: int = 3) -> str:
    """General web search through DuckDuckGo (the tool's original behaviour)."""
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=max_results))

    if not results:
        return f"Aucun résultat trouvé sur le Web pour '{query}'."

    formatted = []
    for r in results:
        title = r.get("title", "Sans titre")
        snippet = r.get("body", "")
        link = r.get("href", "")
        formatted.append(f"• {title}\n  {snippet}\n  Lien: {link}")

    return f"Résultats Web pour '{query}' :\n" + "\n\n".join(formatted)


def _fetch_research(query: str) -> str:
    """In-depth search: encyclopedic summary (if a page exists) + more web results."""
    parts = []
    try:
        summary = wikipedia.summary(query, sentences=3, auto_suggest=True)
        parts.append(f"Contexte (Wikipédia) :\n{summary}")
    except Exception:
        pass  # No matching Wikipedia page: carry on with the web alone.

    parts.append(_fetch_web(query, max_results=6))
    return "\n\n".join(parts)


def _build_targeted_query(query: str, mode: str) -> tuple[str, int]:
    """Adapt the query and the number of results for the 'price'/'compare'/'search' modes."""
    if mode == "price":
        sites = " OR ".join(f"site:{s}" for s in _PRICE_SITES)
        return f"{query} prix ({sites})", 5
    if mode == "compare":
        sites = " OR ".join(f"site:{s}" for s in _COMPARE_SITES)
        return f"{query} comparatif avis ({sites})", 5
    return query, 3  # "search" (default): query and number of results unchanged


def web_search(source: str, query: str, mode: str = "search") -> str:
    """Search Wikipedia or the Web. `mode` adapts the query and the preferred source for 'duckduckgo':
    news (RSS), in-depth search, price, comparison, or general search.
    """
    query_clean = query.strip()

    try:
        if source == "wikipedia":
            summary = wikipedia.summary(query_clean, sentences=2, auto_suggest=True)
            return f"Résultat Wikipédia pour '{query_clean}' :\n{summary}"

        if source != "duckduckgo":
            return "Source de recherche non reconnue (utilisez 'wikipedia' ou 'duckduckgo')."

        if mode == "news":
            return _fetch_news(query_clean)
        if mode == "research":
            return _fetch_research(query_clean)

        adapted_query, max_results = _build_targeted_query(query_clean, mode)
        return _fetch_web(adapted_query, max_results)

    except wikipedia.exceptions.DisambiguationError as e:
        options = ", ".join(e.options[:5])
        return f"La recherche '{query}' est ambiguë. Options possibles : {options}."
    except wikipedia.exceptions.PageError:
        return f"Aucune page Wikipédia trouvée pour '{query}'."
    except requests.RequestException as e:
        return f"Erreur réseau lors de la recherche d'actualités : {str(e)}"
    except ET.ParseError:
        return f"Erreur lors de l'analyse des actualités reçues pour '{query}'."
    except Exception as e:
        return f"Erreur lors de la recherche : {str(e)}"