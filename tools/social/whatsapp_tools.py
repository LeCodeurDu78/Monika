"""Envoi de messages WhatsApp fiables via Playwright."""

from pathlib import Path
from urllib.parse import quote
from core.settings import settings

APP_DIR = settings.APP_DIR
from playwright.sync_api import sync_playwright
from tools.social.contact_tools import get_phone_by_name

SESSION_DIR = str(APP_DIR / ".monika_whatsapp_session")


def _clear_stale_lock() -> None:
    """Supprime le verrou du profil Firefox dédié à WhatsApp, laissé par un crash précédent."""
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


def send_whatsapp_message(recipient: str, message: str) -> str:
    """Envoie un message WhatsApp de manière déterministe."""
    try:
        target = recipient.strip()
        phone_number = target if target.startswith("+") else get_phone_by_name(target)

        if not phone_number:
            return f"❌ Erreur : Le contact '{target}' est introuvable."

        clean_phone = phone_number.replace("+", "").replace(" ", "")
        url = f"https://web.whatsapp.com/send?phone={clean_phone}&text={quote(message)}"

        with sync_playwright() as p:
            _clear_stale_lock()
            context = p.firefox.launch_persistent_context(
                user_data_dir=SESSION_DIR, headless=False
            )
            page = context.new_page()
            page.goto(url)

            print("⏳ Attente du chargement de WhatsApp Web...")
            send_button_selector = 'button[aria-label="Envoyer"], button[aria-label="Send"]'
            page.wait_for_selector(send_button_selector, timeout=30000)
            page.click(send_button_selector)
            page.wait_for_timeout(2000)
            context.close()

        return f"✅ Message WhatsApp envoyé avec succès à {target} ({phone_number}) !"

    except Exception as e:
        return f"❌ Échec de l'envoi WhatsApp : {str(e)}"
