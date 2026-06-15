from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from extensions import db
from models import AppSettings
from services.gemini import test_connection
from seed import DEFAULT_PROMPT

bp = Blueprint("settings", __name__)


def _get_or_create_settings():
    s = AppSettings.query.first()
    if not s:
        s = AppSettings(id=1, gemini_model="gemini-2.5-flash", gemini_summary_prompt=DEFAULT_PROMPT)
        db.session.add(s)
        db.session.commit()
    return s


@bp.route("/settings")
def index():
    import os
    settings = _get_or_create_settings()
    redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:5002/auth/google/callback")
    return render_template("settings/index.html", settings=settings, test_result=None, google_redirect_uri=redirect_uri)


@bp.route("/settings/save", methods=["POST"])
def save():
    settings = _get_or_create_settings()
    api_key = request.form.get("gemini_api_key", "").strip()
    if api_key:
        settings.gemini_api_key = api_key
    settings.gemini_model = request.form.get("gemini_model", "gemini-1.5-flash")
    settings.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Ustawienia zostały zapisane.", "success")
    return redirect(url_for("settings.index"))


@bp.route("/settings/save-prompt", methods=["POST"])
def save_prompt():
    settings = _get_or_create_settings()
    prompt = request.form.get("gemini_summary_prompt", "").strip()
    if prompt:
        settings.gemini_summary_prompt = prompt
    settings.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Prompt AI został zapisany.", "success")
    return redirect(url_for("settings.index"))


@bp.route("/settings/reset-prompt", methods=["POST"])
def reset_prompt():
    settings = _get_or_create_settings()
    settings.gemini_summary_prompt = DEFAULT_PROMPT
    settings.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Prompt przywrócony do domyślnego.", "success")
    return redirect(url_for("settings.index"))


@bp.route("/settings/save-comparison-prompts", methods=["POST"])
def save_comparison_prompts():
    settings = _get_or_create_settings()
    settings.comparison_prompt_extraction = request.form.get("comparison_prompt_extraction", "")
    settings.comparison_prompt_comparison = request.form.get("comparison_prompt_comparison", "")
    settings.comparison_prompt_summary    = request.form.get("comparison_prompt_summary", "")
    settings.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Prompty porownania zapisane.", "success")
    return redirect(url_for("settings.index"))


@bp.route("/settings/reset-comparison-prompts")
def reset_comparison_prompts():
    from services.comparator import DEFAULT_PROMPT_EXTRACTION, DEFAULT_PROMPT_COMPARISON, DEFAULT_PROMPT_SUMMARY
    settings = _get_or_create_settings()
    settings.comparison_prompt_extraction = DEFAULT_PROMPT_EXTRACTION
    settings.comparison_prompt_comparison = DEFAULT_PROMPT_COMPARISON
    settings.comparison_prompt_summary    = DEFAULT_PROMPT_SUMMARY
    settings.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Prompty przywrocone do domyslnych.", "success")
    return redirect(url_for("settings.index"))


@bp.route("/settings/gemini-models")
def gemini_models():
    from flask import jsonify
    settings = _get_or_create_settings()
    if not settings.gemini_api_key:
        return jsonify({"error": "Brak klucza API", "models": []})
    try:
        from google import genai
        client = genai.Client(api_key=settings.gemini_api_key)
        models = client.models.list()
        result = []
        for m in models:
            name = m.name  # format: "models/gemini-2.5-flash"
            model_id = name.split("/")[-1] if "/" in name else name
            # Filtruj tylko modele generatywne (nie embedding itp.)
            supported = getattr(m, "supported_actions", None) or getattr(m, "supported_generation_methods", [])
            if not any("generate" in str(s).lower() for s in supported):
                continue
            display = getattr(m, "display_name", model_id) or model_id
            result.append({"id": model_id, "display_name": display})
        # Sortuj: nowsze (wyższy numer wersji) najpierw
        result.sort(key=lambda x: x["id"], reverse=True)
        return jsonify({"models": result})
    except Exception as e:
        return jsonify({"error": str(e), "models": []})


@bp.route("/settings/save-google-oauth", methods=["POST"])
def save_google_oauth():
    settings = _get_or_create_settings()
    client_id = request.form.get("google_oauth_client_id", "").strip()
    client_secret = request.form.get("google_oauth_client_secret", "").strip()
    if client_id:
        settings.google_oauth_client_id = client_id
    if client_secret:
        settings.google_oauth_client_secret = client_secret
    settings.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Dane OAuth Google zapisane. Teraz polacz konto.", "success")
    return redirect(url_for("settings.index"))


@bp.route("/settings/test", methods=["POST"])
def test():
    import os
    settings = _get_or_create_settings()
    if not settings.gemini_api_key:
        flash("Najpierw zapisz klucz Gemini API.", "warning")
        return redirect(url_for("settings.index"))
    result = test_connection(settings.gemini_api_key, settings.gemini_model)
    redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:5002/auth/google/callback")
    return render_template("settings/index.html", settings=settings, test_result=result, google_redirect_uri=redirect_uri)
