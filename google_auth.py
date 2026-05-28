import os

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as UserCredentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

OAUTH_CLIENT_FILE = os.getenv("GOOGLE_OAUTH_CLIENT_FILE", "oauth_credentials.json")
OAUTH_TOKEN_FILE = os.getenv("GOOGLE_OAUTH_TOKEN_FILE", "data/google_token.json")
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")


def get_google_credentials(allow_interactive=False):
    credentials = None

    if os.path.exists(OAUTH_TOKEN_FILE):
        credentials = UserCredentials.from_authorized_user_file(OAUTH_TOKEN_FILE, SCOPES)

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        os.makedirs(os.path.dirname(OAUTH_TOKEN_FILE), exist_ok=True)
        with open(OAUTH_TOKEN_FILE, "w") as token_file:
            token_file.write(credentials.to_json())

    if credentials and credentials.valid:
        return credentials, "OAuth user"

    if allow_interactive and os.path.exists(OAUTH_CLIENT_FILE):
        flow = InstalledAppFlow.from_client_secrets_file(OAUTH_CLIENT_FILE, SCOPES)
        credentials = flow.run_local_server(port=0)
        os.makedirs(os.path.dirname(OAUTH_TOKEN_FILE), exist_ok=True)
        with open(OAUTH_TOKEN_FILE, "w") as token_file:
            token_file.write(credentials.to_json())
        return credentials, "OAuth user"

    if os.path.exists(SERVICE_ACCOUNT_FILE):
        credentials = ServiceAccountCredentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        return credentials, "service account"

    return None, "missing credentials"
