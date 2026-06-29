"""Google Drive integration: read SEO queue xlsx and upload paste-pack files."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pandas as pd

DRIVE_FILE_ID = "1LIa3h_oksQUVtjHCQE5nRPz7kZko361Z"
QUEUE_TAB = "URLs to Do"
SHARED_DRIVE_FOLDER_ID = "0AHp_CfZhbhV0Uk9PVA"
SCOPES = ["https://www.googleapis.com/auth/drive"]

# Local dev fallback: place a service account key here (never commit it).
_LOCAL_KEY_PATH = Path(__file__).resolve().parent.parent / "config" / "sheets-key.json"


def _credentials():
    from google.oauth2 import service_account

    key_json = os.environ.get("SHEETS_SERVICE_ACCOUNT_KEY")
    if key_json:
        info = json.loads(key_json)
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    if _LOCAL_KEY_PATH.exists():
        return service_account.Credentials.from_service_account_file(
            str(_LOCAL_KEY_PATH), scopes=SCOPES
        )
    raise RuntimeError(
        "No Google Drive credentials found. Set SHEETS_SERVICE_ACCOUNT_KEY env var "
        "or place a service account key at config/sheets-key.json (never commit it)."
    )


def available() -> bool:
    """True if credentials are present — used to decide whether to use Drive or local xlsx."""
    return bool(os.environ.get("SHEETS_SERVICE_ACCOUNT_KEY")) or _LOCAL_KEY_PATH.exists()


def read_queue() -> pd.DataFrame:
    """Download the xlsx from Google Drive and return a raw headerless DataFrame."""
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload

    creds = _credentials()
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    request = service.files().get_media(fileId=DRIVE_FILE_ID)
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    buffer.seek(0)
    return pd.read_excel(buffer, sheet_name=QUEUE_TAB, header=None)


def upload_docx(local_path: str, filename: str) -> str:
    """Upload a .docx file to the Shared Drive folder.

    Returns the web view URL of the uploaded file.
    supportsAllDrives=True is required for Shared Drive access.
    """
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = _credentials()
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    file_metadata = {
        "name": filename,
        "parents": [SHARED_DRIVE_FOLDER_ID],
    }
    media = MediaFileUpload(
        local_path,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    uploaded = service.files().create(
        body=file_metadata,
        media_body=media,
        supportsAllDrives=True,
        fields="id,webViewLink",
    ).execute()

    return uploaded.get("webViewLink", "")

