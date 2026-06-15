import os
import re
import io
from datetime import datetime, timezone

import requests as _requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
REDIRECT_URI = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:5002/auth/google/callback")

EXPORT_MIME = {
    "application/vnd.google-apps.document":     ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.spreadsheet":  ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
}

DOWNLOADABLE_MIME = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/plain",
}


def extract_folder_id(url: str) -> str | None:
    patterns = [
        r"drive\.google\.com/drive/folders/([a-zA-Z0-9_-]+)",
        r"drive\.google\.com/drive/u/\d+/folders/([a-zA-Z0-9_-]+)",
        r"id=([a-zA-Z0-9_-]+)",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    if re.match(r"^[a-zA-Z0-9_-]{20,}$", url.strip()):
        return url.strip()
    return None


def get_oauth_url(client_id: str, client_secret: str) -> str:
    flow = Flow.from_client_config(
        {"web": {"client_id": client_id, "client_secret": client_secret,
                 "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                 "token_uri": "https://oauth2.googleapis.com/token"}},
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )
    url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    return url


def exchange_code(code: str, client_id: str, client_secret: str) -> dict:
    flow = Flow.from_client_config(
        {"web": {"client_id": client_id, "client_secret": client_secret,
                 "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                 "token_uri": "https://oauth2.googleapis.com/token"}},
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    email = _get_user_email(creds)
    return {
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "expiry": creds.expiry,
        "email": email,
    }


def _make_credentials(settings) -> Credentials:
    creds = Credentials(
        token=settings.google_access_token,
        refresh_token=settings.google_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        scopes=SCOPES,
    )
    if settings.google_token_expiry:
        expiry = settings.google_token_expiry
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        creds.expiry = expiry
    return creds


def _get_user_email(creds: Credentials) -> str:
    try:
        svc = build("oauth2", "v2", credentials=creds)
        info = svc.userinfo().get().execute()
        return info.get("email", "")
    except Exception:
        return ""


def _refresh_if_needed(creds: Credentials, settings):
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        settings.google_access_token = creds.token
        if creds.expiry:
            expiry = creds.expiry
            if expiry.tzinfo is not None:
                expiry = expiry.replace(tzinfo=None)
            settings.google_token_expiry = expiry
        from extensions import db
        db.session.commit()


# ── OAuth mode ────────────────────────────────────────────────────────────────

def list_folder_files(folder_id: str, settings) -> list[dict]:
    creds = _make_credentials(settings)
    _refresh_if_needed(creds, settings)
    svc = build("drive", "v3", credentials=creds)
    results = []
    page_token = None
    while True:
        resp = svc.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)",
            pageSize=100,
            pageToken=page_token,
        ).execute()
        for f in resp.get("files", []):
            mime = f.get("mimeType", "")
            if mime.startswith("application/vnd.google-apps.folder"):
                continue
            if mime not in DOWNLOADABLE_MIME and mime not in EXPORT_MIME:
                continue
            results.append({"id": f["id"], "name": f["name"], "mime_type": mime, "size": int(f.get("size", 0))})
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results


def download_file(file_id: str, file_name: str, mime_type: str, dest_dir: str, settings) -> str:
    creds = _make_credentials(settings)
    _refresh_if_needed(creds, settings)
    svc = build("drive", "v3", credentials=creds)
    os.makedirs(dest_dir, exist_ok=True)
    if mime_type in EXPORT_MIME:
        export_mime, ext = EXPORT_MIME[mime_type]
        base = os.path.splitext(file_name)[0]
        dest_path = os.path.join(dest_dir, f"{base}{ext}")
        request = svc.files().export_media(fileId=file_id, mimeType=export_mime)
    else:
        dest_path = os.path.join(dest_dir, file_name)
        request = svc.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    with open(dest_path, "wb") as f:
        f.write(buf.getvalue())
    return dest_path


# ── Public mode (no OAuth, uses Gemini/Drive API key) ────────────────────────

def list_folder_files_public(folder_id: str, api_key: str) -> list[dict]:
    """List publicly shared folder using Drive API key (no OAuth)."""
    url = "https://www.googleapis.com/drive/v3/files"
    params = {
        "q": f"'{folder_id}' in parents and trashed=false",
        "fields": "nextPageToken,files(id,name,mimeType,size)",
        "pageSize": 100,
        "key": api_key,
    }
    results = []
    while True:
        resp = _requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for f in data.get("files", []):
            mime = f.get("mimeType", "")
            if mime.startswith("application/vnd.google-apps.folder"):
                continue
            if mime not in DOWNLOADABLE_MIME and mime not in EXPORT_MIME:
                continue
            results.append({"id": f["id"], "name": f["name"], "mime_type": mime, "size": int(f.get("size", 0))})
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        params["pageToken"] = page_token
    return results


def download_file_public(file_id: str, file_name: str, mime_type: str, dest_dir: str, api_key: str) -> str:
    """Download a publicly shared file using Drive API key (no OAuth)."""
    os.makedirs(dest_dir, exist_ok=True)
    if mime_type in EXPORT_MIME:
        export_mime, ext = EXPORT_MIME[mime_type]
        base = os.path.splitext(file_name)[0]
        dest_path = os.path.join(dest_dir, f"{base}{ext}")
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
        params = {"mimeType": export_mime, "key": api_key}
    else:
        dest_path = os.path.join(dest_dir, file_name)
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
        params = {"alt": "media", "key": api_key}
    resp = _requests.get(url, params=params, timeout=60, stream=True)
    resp.raise_for_status()
    with open(dest_path, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=8192):
            fh.write(chunk)
    return dest_path
