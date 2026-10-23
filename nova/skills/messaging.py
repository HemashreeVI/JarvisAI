"""WhatsApp and Gmail. Both are optional and need one-time setup (see README).

Contacts live in data/contacts.json (never committed), e.g.
    {"amma": "+9194xxxxxxxx", "appa": "+9194xxxxxxxx"}
"""
import base64
import json
from email.mime.text import MIMEText

from nova.config import DATA_DIR
from nova.tools import tool

CONTACTS = DATA_DIR / "contacts.json"
GMAIL_CREDS = DATA_DIR / "credentials.json"
GMAIL_TOKEN = DATA_DIR / "gmail_token.json"
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def _contacts() -> dict:
    try:
        return {k.lower(): v for k, v in json.loads(CONTACTS.read_text(encoding="utf-8")).items()}
    except (OSError, json.JSONDecodeError):
        return {}


@tool("Send a WhatsApp message to a saved contact.", dangerous=True,
      contact="Contact name, e.g. amma", message="The message text")
def send_whatsapp(contact: str, message: str) -> str:
    number = _contacts().get(contact.lower().strip())
    if not number:
        return f"I don't have a number for {contact}. Add it to data/contacts.json."
    import pywhatkit
    pywhatkit.sendwhatmsg_instantly(number, message, wait_time=15, tab_close=True, close_time=4)
    return f"Sent WhatsApp to {contact}."


def _gmail():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(GMAIL_TOKEN, GMAIL_SCOPES) if GMAIL_TOKEN.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not GMAIL_CREDS.exists():
                raise RuntimeError("Gmail isn't set up yet. Put credentials.json in the data folder.")
            creds = InstalledAppFlow.from_client_secrets_file(GMAIL_CREDS, GMAIL_SCOPES).run_local_server(port=0)
        GMAIL_TOKEN.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


@tool("Check unread emails and list who they're from and their subjects.", count="Max emails, default 5")
def check_email(count: int = 5) -> str:
    svc = _gmail()
    res = svc.users().messages().list(userId="me", labelIds=["INBOX", "UNREAD"], maxResults=count).execute()
    msgs = res.get("messages", [])
    if not msgs:
        return "No unread emails. Inbox zero!"
    lines = []
    for m in msgs:
        meta = svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                                          metadataHeaders=["From", "Subject"]).execute()
        h = {x["name"]: x["value"] for x in meta["payload"]["headers"]}
        sender = h.get("From", "someone").split("<")[0].strip().strip('"')
        lines.append(f"From {sender}: {h.get('Subject', '(no subject)')}")
    total = res.get("resultSizeEstimate", len(msgs))
    return f"{total} unread. " + " | ".join(lines)


@tool("Send an email.", dangerous=True, to="Email address", subject="Subject line", body="Email body")
def send_email(to: str, subject: str, body: str) -> str:
    msg = MIMEText(body)
    msg["to"], msg["subject"] = to, subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    _gmail().users().messages().send(userId="me", body={"raw": raw}).execute()
    return f"Email sent to {to}."
