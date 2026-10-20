"""Reading and sending e-mails through IMAP and SMTP."""

import imaplib
import smtplib
from email import message_from_bytes
from email.header import decode_header
from email.message import EmailMessage

from core.settings import settings

EMAIL_USER = settings.EMAIL_USER
EMAIL_PASS = settings.EMAIL_PASS
IMAP_SERVER = settings.IMAP_SERVER
SMTP_SERVER = settings.SMTP_SERVER


def email_control(
    action: str, recipient: str = None, subject: str = None, body: str = None, limit: int = 5
) -> str:
    """Handle reading and sending e-mails through IMAP and SMTP."""
    user = EMAIL_USER
    password = EMAIL_PASS

    if not user or not password:
        return "Erreur : EMAIL_USER ou EMAIL_PASS non configurés dans le fichier .env."

    try:
        if action == "list":
            mail = imaplib.IMAP4_SSL(IMAP_SERVER)
            mail.login(user, password)
            mail.select("inbox")

            status, messages = mail.search(None, "ALL")
            email_ids = messages[0].split()

            if not email_ids:
                return "Aucun e-mail trouvé dans la boîte de réception."

            latest_ids = email_ids[-int(limit) :]
            results = []

            for e_id in reversed(latest_ids):
                _, msg_data = mail.fetch(e_id, "(RFC822.HEADER)")
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = message_from_bytes(response_part[1])

                        subj, encoding = decode_header(msg["Subject"])[0]
                        if isinstance(subj, bytes):
                            subj = subj.decode(encoding or "utf-8", errors="ignore")

                        sender = msg.get("From")
                        results.append(f"- De: {sender} | Sujet: {subj}")

            mail.logout()
            return "Derniers e-mails reçus :\n" + "\n".join(results)

        elif action == "send":
            if not recipient or not subject or not body:
                return "Erreur : 'recipient', 'subject' et 'body' sont requis pour envoyer un e-mail."

            msg = EmailMessage()
            msg.set_content(body)
            msg["Subject"] = subject
            msg["From"] = user
            msg["To"] = recipient

            with smtplib.SMTP_SSL(SMTP_SERVER, 465) as server:
                server.login(user, password)
                server.send_message(msg)

            return f"E-mail envoyé avec succès à {recipient}."

        return "Action non reconnue."

    except Exception as e:
        return f"Erreur lors de la gestion des e-mails : {str(e)}"
