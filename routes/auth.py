import logging
import os

from flask import (
    Blueprint, redirect, render_template, request,
    session, url_for, flash, jsonify,
)

log = logging.getLogger(__name__)

bp = Blueprint("auth", __name__, url_prefix="/auth")

ALLOWED_DOMAINS = {"bldg.pl", "lukaszbrzyski.com"}


def _redirect_uri():
    uri = os.environ.get("GOOGLE_REDIRECT_URI")
    if uri:
        return uri
    # Fallback: build from request; honour reverse-proxy scheme
    scheme = "https" if (
        request.headers.get("X-Forwarded-Proto") == "https"
        or os.environ.get("FORCE_HTTPS")
    ) else request.scheme
    return url_for("auth.callback", _external=True, _scheme=scheme)


def _make_flow(state=None):
    from google_auth_oauthlib.flow import Flow
    client_config = {
        "web": {
            "client_id":     os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
            "redirect_uris": [_redirect_uri()],
        }
    }
    kwargs = {"state": state} if state else {}
    return Flow.from_client_config(
        client_config,
        scopes=[
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/drive.readonly",
        ],
        redirect_uri=_redirect_uri(),
        **kwargs,
    )


@bp.route("/login")
def login():
    if session.get("user_email"):
        return redirect(url_for("competitions.index"))
    return render_template("auth/login.html", unconfigured=not os.environ.get("GOOGLE_CLIENT_ID"))


@bp.route("/google")
def google():
    """Starts the Google OAuth flow — called when user clicks the login button."""
    if session.get("user_email"):
        return redirect(url_for("competitions.index"))
    if not os.environ.get("GOOGLE_CLIENT_ID"):
        return redirect(url_for("auth.login"))

    flow = _make_flow()
    # Force consent screen when Drive is not yet authorized so Google issues
    # a refresh_token covering the drive.readonly scope.  Once connected,
    # revert to account-selection-only to avoid showing consent every login.
    from models import AppSettings
    _settings = AppSettings.query.first()
    _drive_ok = bool(_settings and _settings.drive_refresh_token)
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="select_account" if _drive_ok else "consent",
    )
    session["oauth_state"] = state
    # google_auth_oauthlib >= 1.2 auto-generates a PKCE code_verifier and
    # embeds code_challenge in the auth URL. We must persist the verifier so
    # the callback (which creates a fresh Flow) can include it in the token
    # exchange — otherwise Google returns "Missing code verifier".
    cv = getattr(flow, "code_verifier", None)
    if cv:
        session["oauth_cv"] = cv
    return redirect(auth_url)


@bp.route("/callback")
def callback():
    state = session.get("oauth_state")
    if not state:
        flash("Nieprawidłowa sesja OAuth — spróbuj ponownie.", "error")
        return redirect(url_for("auth.login"))

    try:
        flow = _make_flow(state=state)
        # Restore PKCE code_verifier stored during login() so fetch_token
        # includes it in the token request (required by google_auth_oauthlib >= 1.2).
        cv = session.pop("oauth_cv", None)
        if cv:
            flow.code_verifier = cv
        # On cPanel behind HTTPS proxy the redirect URL may arrive as http://
        auth_response = request.url
        if request.headers.get("X-Forwarded-Proto") == "https" or os.environ.get("FORCE_HTTPS"):
            auth_response = auth_response.replace("http://", "https://", 1)
        flow.fetch_token(authorization_response=auth_response)
        credentials = flow.credentials
    except Exception as exc:
        log.error("OAuth callback błąd: %s", exc, exc_info=True)
        flash(f"Błąd logowania Google: {exc}", "error")
        return redirect(url_for("auth.login"))

    import requests as _http
    try:
        r = _http.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {credentials.token}"},
            timeout=10,
        )
        r.raise_for_status()
        info = r.json()
    except Exception as exc:
        log.error("Userinfo fetch błąd: %s", exc)
        flash("Nie udało się pobrać danych konta Google.", "error")
        return redirect(url_for("auth.login"))

    email  = info.get("email", "")
    domain = email.split("@")[-1].lower() if "@" in email else ""

    if domain not in ALLOWED_DOMAINS:
        log.warning("Zablokowana próba logowania: %s (domena %s)", email, domain)
        flash(
            f"Dostęp tylko dla kont @bldg.pl i @lukaszbrzyski.com. "
            f"Zalogowano jako: {email}",
            "error",
        )
        return redirect(url_for("auth.login"))

    session.permanent = True
    session["user_email"]   = email
    session["user_name"]    = info.get("name", email)
    session["user_picture"] = info.get("picture", "")
    session.pop("oauth_state", None)

    # Save Drive OAuth tokens so the app can sync Drive on behalf of this user.
    # refresh_token is only issued on first consent — keep existing one if Google omits it.
    try:
        from models import AppSettings
        from extensions import db as _db
        settings = AppSettings.query.first()
        if not settings:
            settings = AppSettings(id=1)
            _db.session.add(settings)
        settings.drive_access_token = credentials.token
        if credentials.refresh_token:
            settings.drive_refresh_token = credentials.refresh_token
        settings.drive_token_expiry = credentials.expiry
        _db.session.commit()
    except Exception as _exc:
        log.warning("Nie udało się zapisać tokenów Drive: %s", _exc)

    log.info("Zalogowano: %s", email)

    next_url = session.pop("next_url", None)
    return redirect(next_url or url_for("competitions.index"))


@bp.route("/logout")
def logout():
    email = session.get("user_email", "—")
    session.clear()
    log.info("Wylogowano: %s", email)
    return redirect(url_for("auth.login"))
