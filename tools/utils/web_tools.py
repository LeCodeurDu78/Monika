"""Reading the content of a web page (complements web_search, which only returns excerpts)."""

import json
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

_DEFAULT_MAX_CHARS = 15000
_MAX_DOWNLOAD_BYTES = 5_000_000
_MAX_REDIRECTS = 5
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0 Monika/1.0",
    "Accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.9,*/*;q=0.5",
    "Accept-Language": "fr,en;q=0.8",
}
_NOISE_TAGS = ["script", "style", "noscript", "svg", "iframe", "form", "nav", "footer", "aside", "header"]


def _check_url(url: str) -> str | None:
    """Return None if the URL is a complete http(s) URL, otherwise the reason it is invalid."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "seules les URL http(s) complètes sont acceptées (ex: https://exemple.fr/page)."
    return None


def _download(url: str) -> tuple[requests.Response, bytes]:
    """GET following redirects by hand (each step is re-checked); body size capped."""
    for _ in range(_MAX_REDIRECTS + 1):
        reason = _check_url(url)
        if reason:
            raise ValueError(reason)
        resp = requests.get(url, headers=_HEADERS, timeout=20, stream=True, allow_redirects=False)
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            resp.close()
            if not location:
                raise ValueError("redirection sans destination.")
            url = urljoin(url, location)
            continue
        chunks, size = [], 0
        for chunk in resp.iter_content(65536):
            chunks.append(chunk)
            size += len(chunk)
            if size >= _MAX_DOWNLOAD_BYTES:
                break
        resp.close()
        return resp, b"".join(chunks)[:_MAX_DOWNLOAD_BYTES]
    raise ValueError("trop de redirections.")


def _html_to_text(html: str, base_url: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "") or ""
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    # Links stay usable: "text (url)".
    for a in root.find_all("a", href=True):
        label = a.get_text(" ", strip=True)
        href = urljoin(base_url, a["href"])
        if label and href.startswith("http") and not href.startswith(base_url + "#"):
            a.replace_with(f"{label} ({href})")
    for h in root.find_all(re.compile(r"^h[1-6]$")):
        level = int(h.name[1])
        h.insert_before("\n\n" + "#" * level + " ")
        h.insert_after("\n")
    for br in root.find_all(["br", "p", "li", "tr", "div", "section"]):
        br.insert_after("\n")
    text = root.get_text()
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return title, text.strip()


def fetch_url(url: str, max_chars: int = _DEFAULT_MAX_CHARS, offset: int = 0) -> str:
    """Download a web page and return its readable text (with offset pagination)."""
    url = (url or "").strip()
    try:
        max_chars = max(500, min(int(max_chars or _DEFAULT_MAX_CHARS), 40000))
        offset = max(0, int(offset or 0))
        resp, body = _download(url)
    except ValueError as exc:
        return f"❌ URL refusée ou invalide : {exc}"
    except requests.RequestException as exc:
        return f"❌ Erreur réseau pour {url} : {type(exc).__name__}: {exc}"

    if resp.status_code >= 400:
        return f"❌ HTTP {resp.status_code} pour {url}."

    ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if ctype and not (ctype.startswith("text/") or "html" in ctype or "json" in ctype or "xml" in ctype):
        return f"❌ Type de contenu non pris en charge ({ctype}) pour {url}. Pour un fichier binaire, télécharge-le avec run_script (curl -L -o)."

    encoding = resp.encoding if resp.encoding and "charset" in resp.headers.get("Content-Type", "").lower() else None
    html = body.decode(encoding or "utf-8", errors="replace")

    title = ""
    if "html" in ctype or html.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        title, text = _html_to_text(html, str(resp.url))
    elif "json" in ctype:
        try:
            text = json.dumps(json.loads(html), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            text = html
    else:
        text = html

    if not text:
        return f"Page {url} sans contenu textuel (probablement générée par JavaScript : essaie browser_control)."

    total = len(text)
    if offset >= total:
        return f"❌ offset {offset} au-delà de la fin du contenu ({total} caractères)."
    chunk = text[offset : offset + max_chars]
    end = offset + len(chunk)
    header = f"{resp.url}" + (f" — {title}" if title else "") + f"\n[caractères {offset}-{end} sur {total}]\n\n"
    footer = f"\n\n[... suite disponible : relance fetch_url avec offset={end}]" if end < total else ""
    return header + chunk + footer
