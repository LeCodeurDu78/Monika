"""Reading and creating Google Calendar events."""

import os
import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from core.settings import settings

APP_DIR = settings.APP_DIR

CREDENTIALS_FILE = str(APP_DIR / "credentials_calendar.json")
TOKEN_FILE = str(APP_DIR / "token.json")
SCOPES = ['https://www.googleapis.com/auth/calendar']


def _get_calendar_service():
    """Handle OAuth2 authentication with the Google Calendar API."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Fichier '{CREDENTIALS_FILE}' introuvable. Téléchargez vos identifiants OAuth 2.0 depuis la console Google Cloud."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return build('calendar', 'v3', credentials=creds)


# Python weekday (Monday=0) -> iCalendar BYDAY code
_WEEKDAY_TO_BYDAY = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def calendar_control(
    action: str,
    summary: str = None,
    start_time: str = None,
    end_time: str = None,
    limit: int = 5,
    location: str = None,
    repeat_weekly: bool = False,
) -> str:
    """Handle reading and adding events in Google Calendar."""
    try:
        service = _get_calendar_service()

        if action == "list":
            now = datetime.datetime.utcnow().isoformat() + 'Z'
            events_result = (
                service.events()
                .list(
                    calendarId='primary',
                    timeMin=now,
                    maxResults=limit,
                    singleEvents=True,
                    orderBy='startTime',
                )
                .execute()
            )
            events = events_result.get('items', [])

            if not events:
                return "Aucun événement à venir trouvé."

            formatted = []
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                summary_text = event.get('summary', 'Sans titre')
                location_text = event.get('location')
                loc_suffix = f" ({location_text})" if location_text else ""
                recurring = " [récurrent]" if event.get('recurringEventId') else ""
                formatted.append(f"• {start} : {summary_text}{loc_suffix}{recurring}")

            return "Prochains événements Google Calendar :\n" + "\n".join(formatted)

        elif action == "add":
            if not summary or not start_time or not end_time:
                return "Erreur : 'summary', 'start_time' et 'end_time' (format ISO: YYYY-MM-DDTHH:MM:SS) sont requis."

            event = {
                'summary': summary,
                'start': {'dateTime': start_time, 'timeZone': 'Europe/Paris'},
                'end': {'dateTime': end_time, 'timeZone': 'Europe/Paris'},
            }

            if location:
                event['location'] = location

            if repeat_weekly:
                try:
                    start_dt = datetime.datetime.fromisoformat(start_time)
                except ValueError:
                    return f"Erreur : 'start_time' ('{start_time}') n'est pas une date/heure ISO valide."
                byday = _WEEKDAY_TO_BYDAY[start_dt.weekday()]
                event['recurrence'] = [f'RRULE:FREQ=WEEKLY;BYDAY={byday}']

            created_event = service.events().insert(calendarId='primary', body=event).execute()
            repeat_txt = " (récurrent chaque semaine)" if repeat_weekly else ""
            return f"Événement '{summary}'{repeat_txt} créé avec succès (Lien : {created_event.get('htmlLink')})."

        elif action == "delete":
            if not summary:
                return "Erreur : 'summary' est requis pour supprimer un événement (voir action='list')."

            now = datetime.datetime.utcnow().isoformat() + 'Z'
            events_result = (
                service.events()
                .list(
                    calendarId='primary',
                    timeMin=now,
                    q=summary,
                    maxResults=25,
                    singleEvents=True,
                    orderBy='startTime',
                )
                .execute()
            )
            matches = [
                e for e in events_result.get('items', [])
                if e.get('summary', '').strip().lower() == summary.strip().lower()
            ]

            if not matches:
                return f"Aucun événement à venir nommé « {summary} » trouvé."

            target = matches[0]
            is_recurring = bool(target.get('recurringEventId'))
            # For an occurrence of a recurring event, the master event (recurringEventId) must be deleted
            # to erase the whole series, not just that occurrence.
            event_id_to_delete = target.get('recurringEventId', target['id'])

            service.events().delete(calendarId='primary', eventId=event_id_to_delete).execute()

            if is_recurring:
                return f"🗑️ Événement récurrent « {summary} » supprimé (toutes les occurrences)."
            return f"🗑️ Événement « {summary} » supprimé."

        return "Action non reconnue."
    except Exception as e:
        return f"Erreur Google Calendar : {str(e)}"