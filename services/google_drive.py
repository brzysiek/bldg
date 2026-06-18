import os
import re
import io
import ssl
import socket
import unicodedata
from contextlib import contextmanager

import requests as _requests
from requests.adapters import HTTPAdapter

_PL_TRANSLIT = {
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o", "ś": "s", "ź": "z", "ż": "z",
    "Ą": "A", "Ć": "C", "Ę": "E", "Ł": "L", "Ń": "N", "Ó": "O", "Ś": "S", "Ź": "Z", "Ż": "Z",
}


def _safe_filename(name: str) -> str:
    """Returns an ASCII-only filename for on-disk temp storage.

    Some cPanel hosts run with an ASCII filesystem encoding, so writing a
    path containing Polish diacritics (ł, ś, ż...) raises UnicodeEncodeError
    even though the bytes were downloaded successfully. The original_name
    stored in the DB is unaffected — this only renames the temp file on disk.
    """
    try:
        name.encode("ascii")
        return name
    except UnicodeEncodeError:
        pass
    translit = "".join(_PL_TRANSLIT.get(ch, ch) for ch in name)
    normalized = unicodedata.normalize("NFKD", translit)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    return ascii_name or "file"


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


def _make_session() -> _requests.Session:
    """Session with a permissive SSL context — needed on CloudLinux/cPanel hosts
    where Python 3.13's default TLS context causes SSLEOFError during handshake."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    try:
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    except ssl.SSLError:
        pass
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        ctx.load_default_certs()

    class _TLSAdapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            kwargs["ssl_context"] = ctx
            super().init_poolmanager(*args, **kwargs)

        def proxy_manager_for(self, proxy, **kwargs):
            kwargs["ssl_context"] = ctx
            return super().proxy_manager_for(proxy, **kwargs)

    session = _requests.Session()
    session.headers["Connection"] = "close"
    session.mount("https://", _TLSAdapter())
    return session


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


def list_folder_files(folder_id: str, api_key: str) -> list[dict]:
    """List a publicly shared Drive folder using an API key."""
    url = "https://www.googleapis.com/drive/v3/files"
    params = {
        "q": f"'{folder_id}' in parents and trashed=false",
        "fields": "nextPageToken,files(id,name,mimeType,size)",
        "pageSize": 100,
        "key": api_key,
    }
    results = []
    session = _make_session()
    while True:
        resp = session.get(url, params=params, timeout=20)
        if not resp.ok:
            _raise_drive_error(resp)
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


import time as _time
import logging as _logging
_log = _logging.getLogger(__name__)

# Reasons that warrant a retry with acknowledgeAbuse=true before giving up
_ABUSE_REASONS = {"cannotDownloadFile", "abuse", "forbidden"}


def _parse_drive_error(resp):
    """Return (api_msg, api_reason) from a Drive API error response body."""
    try:
        body = resp.json()
        err = body.get("error", {})
        msg = err.get("message", "")
        reason = (err.get("errors") or [{}])[0].get("reason", "")
        return msg, reason
    except Exception:
        return "", ""


def _raise_drive_error(resp, file_name: str = "") -> None:
    """Parse Drive API error JSON and raise RuntimeError with a Polish message."""
    status = resp.status_code
    api_msg, api_reason = _parse_drive_error(resp)

    name_info = f' "{file_name}"' if file_name else ""
    drive_detail = f" (Drive: {api_msg})" if api_msg else ""
    reason_detail = f" [reason={api_reason}]" if api_reason else ""

    if status == 403:
        if api_reason in ("insufficientFilePermissions", "domainPolicy"):
            hint = (
                "Plik nie jest udostepniony publicznie lub dostep jest ograniczony domenowo. "
                "Otworz plik w Google Drive -> Udostepnij -> ustaw 'Kazdy, kto ma link' -> Przegladajacy."
            )
        else:
            hint = (
                "Brak uprawnien do pobrania pliku. Sprawdz, czy plik jest udostepniony publicznie "
                "(Google Drive -> Udostepnij -> 'Kazdy, kto ma link' -> Przegladajacy)."
            )
        raise RuntimeError(
            f"Google Drive 403 Forbidden{name_info}{drive_detail}{reason_detail}. {hint}"
        )
    elif status == 404:
        raise RuntimeError(
            f"Google Drive 404 Not Found{name_info}{drive_detail}. "
            "Sprawdz, czy plik nadal istnieje i czy ID jest poprawne."
        )
    elif status == 429:
        raise RuntimeError(
            f"Google Drive 429 Too Many Requests{name_info}. "
            "Przekroczono limit zapytan API — poczekaj kilka minut i sprobuj ponownie."
        )
    elif status >= 500:
        raise RuntimeError(
            f"Google Drive blad serwera (HTTP {status}){name_info}{drive_detail}."
        )
    else:
        raise RuntimeError(
            f"Google Drive HTTP {status}{name_info}{drive_detail}{reason_detail}."
        )


def download_file(file_id: str, file_name: str, mime_type: str, dest_dir: str, api_key: str) -> str:
    """Download a publicly shared Drive file using an API key.

    Retry strategy:
    - attempt 0: normal download
    - attempt 1: add acknowledgeAbuse=true (handles large-file virus-scan 403)
    - attempt 2: same, after 2s backoff (handles transient 5xx / rate-limit)
    """
    os.makedirs(dest_dir, exist_ok=True)
    safe_name = _safe_filename(file_name)
    if mime_type in EXPORT_MIME:
        export_mime, ext = EXPORT_MIME[mime_type]
        base = os.path.splitext(safe_name)[0]
        dest_path = os.path.join(dest_dir, f"{base}{ext}")
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
        base_params: dict = {"mimeType": export_mime, "key": api_key}
    else:
        dest_path = os.path.join(dest_dir, safe_name)
        url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
        base_params = {"alt": "media", "key": api_key}

    session = _make_session()
    last_resp = None

    for attempt in range(3):
        params = dict(base_params)
        if attempt >= 1:
            params["acknowledgeAbuse"] = "true"
        if attempt >= 2:
            _time.sleep(2)

        _log.debug(
            "Drive download attempt=%d  file=%s  id=%s  acknowledgeAbuse=%s",
            attempt, file_name, file_id, params.get("acknowledgeAbuse", "false"),
        )
        resp = session.get(url, params=params, timeout=120, stream=True)
        last_resp = resp

        if resp.ok:
            with open(dest_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    fh.write(chunk)
            _log.debug("Drive download OK  file=%s  attempt=%d", file_name, attempt)
            return dest_path

        api_msg, api_reason = _parse_drive_error(resp)
        _log.warning(
            "Drive download FAIL  attempt=%d  file=%s  id=%s  status=%d  reason=%s  msg=%s",
            attempt, file_name, file_id, resp.status_code, api_reason or "-", api_msg or "-",
        )
        resp.close()

        # 403 on attempt 0 with an abuse/download-check reason → retry with acknowledgeAbuse
        if resp.status_code == 403 and attempt == 0 and api_reason in _ABUSE_REASONS:
            continue
        # 403 with no specific reason on attempt 0 → also worth one retry
        if resp.status_code == 403 and attempt == 0 and not api_reason:
            continue
        # 5xx transient server error → one more retry
        if resp.status_code >= 500 and attempt < 2:
            continue
        # anything else (404, real permissions, exhausted retries) → raise now
        break

    _raise_drive_error(last_resp, file_name)
