from flask import Blueprint, redirect, request, url_for, flash
from extensions import db
from models import AppSettings
from services.google_drive import exchange_code

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/google/")
def google_login():
    settings = db.session.get(AppSettings, 1)
    if not settings or not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        flash("Wklej najpierw Client ID i Client Secret Google OAuth w Ustawieniach.", "warning")
        return redirect(url_for("settings.index"))

    from services.google_drive import get_oauth_url
    url = get_oauth_url(settings.google_oauth_client_id, settings.google_oauth_client_secret)
    return redirect(url)


@bp.route("/google/callback")
def google_callback():
    code = request.args.get("code")
    error = request.args.get("error")

    if error:
        flash(f"Google odmowil dostepu: {error}", "error")
        return redirect(url_for("settings.index"))

    if not code:
        flash("Brak kodu autoryzacji od Google.", "error")
        return redirect(url_for("settings.index"))

    settings = db.session.get(AppSettings, 1)
    if not settings:
        flash("Brak konfiguracji aplikacji.", "error")
        return redirect(url_for("settings.index"))

    try:
        token_data = exchange_code(code, settings.google_oauth_client_id, settings.google_oauth_client_secret)
        settings.google_access_token = token_data["access_token"]
        if token_data.get("refresh_token"):
            settings.google_refresh_token = token_data["refresh_token"]
        if token_data.get("expiry"):
            expiry = token_data["expiry"]
            from datetime import timezone
            if expiry.tzinfo is not None:
                expiry = expiry.replace(tzinfo=None)
            settings.google_token_expiry = expiry
        settings.google_user_email = token_data.get("email", "")
        db.session.commit()
        flash(f"Polaczono z Google Drive ({settings.google_user_email}).", "success")
    except Exception as e:
        flash(f"Blad autoryzacji Google: {e}", "error")

    return redirect(url_for("settings.index"))


@bp.route("/google/disconnect", methods=["POST"])
def google_disconnect():
    settings = db.session.get(AppSettings, 1)
    if settings:
        settings.google_access_token = None
        settings.google_refresh_token = None
        settings.google_token_expiry = None
        settings.google_user_email = None
        db.session.commit()
    flash("Konto Google Drive zostalo odlaczone.", "success")
    return redirect(url_for("settings.index"))
