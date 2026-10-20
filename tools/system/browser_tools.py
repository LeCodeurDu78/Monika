"""Browser control for Monika."""

from pathlib import Path
from core.settings import settings

APP_DIR = settings.APP_DIR
HEADLESS = settings.BROWSER_HEADLESS
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

SESSION_DIR = str(APP_DIR / ".monika_browser_session")
DEFAULT_ACTION_TIMEOUT_MS = 8000
MAX_CONTENT_CHARS = 4000
SNIPPET_CHARS = 300

_playwright = None
_context = None
_active_tab_index = 0


def _ensure_context():
    global _playwright, _context

    if _context is not None:
        try:
            _ = _context.pages
            return _context
        except Exception:
            _context = None

    if _playwright is None:
        _playwright = sync_playwright().start()

    _clear_stale_lock()

    _context = _playwright.firefox.launch_persistent_context(
        user_data_dir=SESSION_DIR,
        headless=HEADLESS,
    )
    if not _context.pages:
        _context.new_page()

    return _context


def _clear_stale_lock():
    """Remove the Firefox profile lock."""
    profile = Path(SESSION_DIR)
    if not profile.exists():
        return
    for lock_name in ("lock", ".parentlock"):
        lock_path = profile / lock_name
        try:
            if lock_path.exists() or lock_path.is_symlink():
                lock_path.unlink()
        except Exception:
            pass


def close_browser() -> str:
    """Explicitly close the Firefox managed by Monika."""
    global _playwright, _context
    try:
        if _context is not None:
            _context.close()
        if _playwright is not None:
            _playwright.stop()
        _context = None
        _playwright = None
        return "✅ Navigateur Monika fermé."
    except Exception as e:
        return f"❌ Erreur lors de la fermeture du navigateur : {e}"


def _live_pages() -> list:
    context = _ensure_context()
    return [p for p in context.pages if not p.is_closed()]


def _active_page():
    global _active_tab_index
    pages = _live_pages()
    if not pages:
        context = _ensure_context()
        pages = [context.new_page()]
    if _active_tab_index >= len(pages):
        _active_tab_index = 0
    return pages[_active_tab_index]


def list_tabs() -> str:
    """List the tabs currently open in Monika's Firefox."""
    try:
        pages = _live_pages()
        if not pages:
            return "Aucun onglet ouvert."
        lines = []
        for i, page in enumerate(pages):
            marker = "➡️ " if i == _active_tab_index else "   "
            try:
                title = page.title()
            except Exception:
                title = "(titre indisponible)"
            lines.append(f"{marker}[{i}] {title} — {page.url}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Erreur lors du listage des onglets : {e}"


def switch_tab(tab_id: int) -> str:
    """Bascule l'onglet actif."""
    global _active_tab_index
    try:
        pages = _live_pages()
        if not pages:
            return "Aucun onglet ouvert."
        if tab_id < 0 or tab_id >= len(pages):
            return f"❌ Index d'onglet invalide (valeurs possibles : 0 à {len(pages) - 1})."
        _active_tab_index = tab_id
        page = pages[tab_id]
        try:
            page.bring_to_front()
        except Exception:
            pass
        return f"✅ Onglet actif : [{tab_id}] {page.title()} — {page.url}"
    except Exception as e:
        return f"❌ Erreur lors du changement d'onglet : {e}"


def _snippet(page, max_chars: int = SNIPPET_CHARS) -> str:
    """Compact preview of the visible text."""
    try:
        raw_text = page.inner_text("body")
        text = " ".join(raw_text.split())
        truncated = text[:max_chars]
        suffix = "…" if len(text) > max_chars else ""
        return f"{truncated}{suffix}"
    except Exception:
        return "(aperçu indisponible)"


def navigate(url: str) -> str:
    """Navigate to a URL in the active tab."""
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    try:
        page = _active_page()
        page.goto(url, timeout=15000, wait_until="domcontentloaded")
        return (
            f"✅ Navigation effectuée vers {page.url} (titre : {page.title()}).\n"
            f"Aperçu : {_snippet(page)}"
        )
    except PlaywrightTimeoutError:
        return f"⚠️ Délai dépassé en chargeant '{url}' (la page continue peut-être de charger)."
    except Exception as e:
        return f"❌ Erreur de navigation : {e}"


def read_page_content(full: bool = True) -> str:
    """Extract the visible text of the active tab."""
    try:
        page = _active_page()
        if not full:
            return f"[{page.title()} — {page.url}]\n{_snippet(page)}"
        raw_text = page.inner_text("body")
        text = " ".join(raw_text.split())
        truncated = text[:MAX_CONTENT_CHARS]
        suffix = " […contenu tronqué]" if len(text) > MAX_CONTENT_CHARS else ""
        return f"[{page.title()} — {page.url}]\n{truncated}{suffix}"
    except Exception as e:
        return f"❌ Erreur lors de la lecture de la page : {e}"


def list_interactive_elements() -> str:
    """List the visible fields, buttons and links of the active tab with their accessible name."""
    try:
        page = _active_page()
        elements = page.eval_on_selector_all(
            "input, textarea, button, a[href], select, [role=button], [role=textbox], [role=combobox]",
            """(els) => els.map(el => {
                const visible = !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
                if (!visible) return null;
                const label = el.getAttribute('aria-label')
                    || el.getAttribute('placeholder')
                    || el.innerText
                    || el.value
                    || el.name
                    || '';
                return {
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role') || '',
                    type: el.getAttribute('type') || '',
                    label: label.trim().slice(0, 80)
                };
            }).filter(Boolean)"""
        )
        seen = set()
        lines = []
        for el in elements:
            if not el["label"]:
                continue
            key = (el["tag"], el["label"])
            if key in seen:
                continue
            seen.add(key)
            kind = el["role"] or el["type"] or el["tag"]
            lines.append(f"- [{kind}] \"{el['label']}\"")
            if len(lines) >= 40:
                break
        if not lines:
            return "Aucun élément interactif visible détecté."
        return f"[{page.title()} — {page.url}]\nÉléments détectés :\n" + "\n".join(lines)
    except Exception as e:
        return f"❌ Erreur lors du listage des éléments : {e}"


def _locate(page, description: str):
    """Locate an element by description."""
    description = description.strip()
    strategies = [
        lambda: page.get_by_role("button", name=description, exact=False),
        lambda: page.get_by_role("link", name=description, exact=False),
        lambda: page.get_by_role("textbox", name=description, exact=False),
        lambda: page.get_by_label(description, exact=False),
        lambda: page.get_by_placeholder(description, exact=False),
        lambda: page.get_by_text(description, exact=False),
    ]
    for strategy in strategies:
        try:
            locator = strategy()
            if locator.count() > 0:
                candidate = locator.first
                candidate.wait_for(state="visible", timeout=1500)
                return candidate
        except Exception:
            continue
    return None


def click_element(description: str) -> str:
    """Click the element of the active tab identified by its description."""
    try:
        page = _active_page()
        element = _locate(page, description)
        if element is None:
            return f"❌ Élément introuvable pour la description : '{description}'."
        element.click(timeout=DEFAULT_ACTION_TIMEOUT_MS)
        return (
            f"✅ Clic effectué sur l'élément correspondant à '{description}'.\n"
            f"Aperçu après clic : {_snippet(page)}"
        )
    except PlaywrightTimeoutError:
        return f"⚠️ Élément trouvé mais non cliquable dans le délai imparti ('{description}')."
    except Exception as e:
        return f"❌ Erreur lors du clic : {e}"


def fill_field(description: str, text: str) -> str:
    """Fill the field of the active tab identified by its description."""
    try:
        page = _active_page()
        element = _locate(page, description)
        if element is None:
            return f"❌ Champ introuvable pour la description : '{description}'."
        element.fill(text, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        return f"✅ Champ '{description}' rempli."  # no preview here: filling a field almost never changes the visible page
    except PlaywrightTimeoutError:
        return f"⚠️ Champ trouvé mais non modifiable dans le délai imparti ('{description}')."
    except Exception as e:
        return f"❌ Erreur lors du remplissage : {e}"


def get_field_value(description: str) -> str:
    """Read the current value of a field (input/textarea/select) - useful to check
    an entry (date, time, number of people...) before validating a step.
    """
    try:
        page = _active_page()
        element = _locate(page, description)
        if element is None:
            return f"❌ Champ introuvable pour la description : '{description}'."
        try:
            value = element.input_value(timeout=DEFAULT_ACTION_TIMEOUT_MS)
        except Exception:
            value = element.text_content(timeout=DEFAULT_ACTION_TIMEOUT_MS) or ""
        return f"Valeur actuelle de '{description}' : '{value.strip()}'"
    except PlaywrightTimeoutError:
        return f"⚠️ Champ trouvé mais valeur illisible dans le délai imparti ('{description}')."
    except Exception as e:
        return f"❌ Erreur lors de la lecture du champ : {e}"


def select_option(description: str, option: str) -> str:
    """Select an option in a dropdown (<select>, or ARIA combobox) identified by its description.
    Tries the visible label first, then the value.
    """
    try:
        page = _active_page()
        element = _locate(page, description)
        if element is None:
            return f"❌ Liste déroulante introuvable pour la description : '{description}'."

        tag = (element.evaluate("el => el.tagName.toLowerCase()") or "").lower()

        if tag == "select":
            try:
                element.select_option(label=option, timeout=DEFAULT_ACTION_TIMEOUT_MS)
            except Exception:
                try:
                    element.select_option(value=option, timeout=DEFAULT_ACTION_TIMEOUT_MS)
                except Exception:
                    element.select_option(option, timeout=DEFAULT_ACTION_TIMEOUT_MS)
            return f"✅ Option '{option}' sélectionnée pour '{description}'.\nAperçu : {_snippet(page)}"

        # ARIA combobox / custom menu: open it then click the displayed option
        element.click(timeout=DEFAULT_ACTION_TIMEOUT_MS)
        option_locator = None
        for strategy in (
            lambda: page.get_by_role("option", name=option, exact=False),
            lambda: page.get_by_text(option, exact=False),
        ):
            try:
                loc = strategy()
                if loc.count() > 0:
                    option_locator = loc.first
                    break
            except Exception:
                continue
        if option_locator is None:
            return f"❌ Option '{option}' introuvable après ouverture de la liste '{description}'."
        option_locator.click(timeout=DEFAULT_ACTION_TIMEOUT_MS)
        return f"✅ Option '{option}' sélectionnée pour '{description}'.\nAperçu : {_snippet(page)}"
    except PlaywrightTimeoutError:
        return f"⚠️ Liste déroulante trouvée mais sélection impossible dans le délai imparti ('{description}')."
    except Exception as e:
        return f"❌ Erreur lors de la sélection : {e}"


def press_key(key: str, description: str = None) -> str:
    """Send a keyboard key (e.g. 'Enter', 'Escape', 'Tab', 'ArrowDown') to a field (focused first if a
    description is given) or to the active page. Useful for date/time pickers and autocomplete fields that
    do not react to fill_field.
    """
    try:
        page = _active_page()
        if description:
            element = _locate(page, description)
            if element is None:
                return f"❌ Élément introuvable pour la description : '{description}'."
            element.focus(timeout=DEFAULT_ACTION_TIMEOUT_MS)
            element.press(key, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        else:
            page.keyboard.press(key)
        return f"✅ Touche '{key}' envoyée" + (f" à '{description}'." if description else " à la page.") + f"\nAperçu : {_snippet(page)}"
    except PlaywrightTimeoutError:
        return f"⚠️ Impossible d'envoyer la touche '{key}' dans le délai imparti."
    except Exception as e:
        return f"❌ Erreur lors de l'envoi de la touche : {e}"


def wait_for(description: str = None, text: str = None, timeout_ms: int = 10000) -> str:
    """Wait for an element (description) or a text to appear on the page - useful to give time to
    slots/availabilities loaded through AJAX to show up before listing interactive elements or clicking them.
    """
    try:
        page = _active_page()
        if not description and not text:
            return "❌ Fournir 'description' ou 'text' pour l'action 'wait_for'."
        if text:
            page.get_by_text(text, exact=False).first.wait_for(state="visible", timeout=timeout_ms)
            return f"✅ Le texte '{text}' est apparu.\nAperçu : {_snippet(page)}"
        deadline_element = _locate(page, description)
        if deadline_element is not None:
            deadline_element.wait_for(state="visible", timeout=timeout_ms)
            return f"✅ L'élément '{description}' est apparu.\nAperçu : {_snippet(page)}"
        return f"⚠️ Élément '{description}' non trouvé après attente."
    except PlaywrightTimeoutError:
        return f"⏱️ Rien de correspondant n'est apparu dans le délai imparti ({timeout_ms} ms)."
    except Exception as e:
        return f"❌ Erreur lors de l'attente : {e}"


def browser_control(
    action: str,
    url: str = None,
    tab_id: int = None,
    description: str = None,
    text: str = None,
    full: bool = False,
    option: str = None,
    key: str = None,
    timeout_ms: int = 10000,
) -> str:
    """Single entry point for the agent."""
    if action == "list_tabs":
        return list_tabs()
    if action == "switch_tab":
        if tab_id is None:
            return "❌ Paramètre 'tab_id' requis pour l'action 'switch_tab'."
        return switch_tab(tab_id)
    if action == "navigate":
        if not url:
            return "❌ Paramètre 'url' requis pour l'action 'navigate'."
        return navigate(url)
    if action == "read_page_content":
        return read_page_content(full=full)
    if action == "list_interactive_elements":
        return list_interactive_elements()
    if action == "click_element":
        if not description:
            return "❌ Paramètre 'description' requis pour l'action 'click_element'."
        return click_element(description)
    if action == "fill_field":
        if not description or text is None:
            return "❌ Paramètres 'description' et 'text' requis pour l'action 'fill_field'."
        return fill_field(description, text)
    if action == "get_field_value":
        if not description:
            return "❌ Paramètre 'description' requis pour l'action 'get_field_value'."
        return get_field_value(description)
    if action == "select_option":
        if not description or not option:
            return "❌ Paramètres 'description' et 'option' requis pour l'action 'select_option'."
        return select_option(description, option)
    if action == "press_key":
        if not key:
            return "❌ Paramètre 'key' requis pour l'action 'press_key'."
        return press_key(key, description=description)
    if action == "wait_for":
        return wait_for(description=description, text=text, timeout_ms=timeout_ms)
    if action == "close_browser":
        return close_browser()
    return f"❌ Action inconnue : '{action}'."