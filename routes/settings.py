from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from extensions import db
from models import AppSettings, PromptVersion
from services.gemini import test_connection
from services.prompt_history import record_prompt_version, get_prompt_history
from seed import DEFAULT_PROMPT

bp = Blueprint("settings", __name__)

PROMPT_KEYS = [
    "gemini_summary_prompt",
    "comparison_prompt_extraction",
    "comparison_prompt_comparison",
    "comparison_prompt_summary",
]


def _get_or_create_settings():
    s = AppSettings.query.first()
    if not s:
        s = AppSettings(id=1, gemini_model="gemini-2.5-flash", gemini_summary_prompt=DEFAULT_PROMPT)
        db.session.add(s)
        record_prompt_version("gemini_summary_prompt", DEFAULT_PROMPT, source="seed")
        db.session.commit()
    return s


@bp.route("/settings")
def index():
    import os
    settings = _get_or_create_settings()
    redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:5002/auth/google/callback")
    history = {key: get_prompt_history(key) for key in PROMPT_KEYS}
    return render_template("settings/index.html", settings=settings, test_result=None,
                            google_redirect_uri=redirect_uri, history=history)


@bp.route("/settings/save", methods=["POST"])
def save():
    settings = _get_or_create_settings()
    api_key = request.form.get("gemini_api_key", "").strip()
    if api_key:
        settings.gemini_api_key = api_key
    settings.gemini_model = request.form.get("gemini_model", "gemini-2.5-flash")
    settings.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Ustawienia zostały zapisane.", "success")
    return redirect(url_for("settings.index", tab="integracje"))


@bp.route("/settings/save-prompt", methods=["POST"])
def save_prompt():
    settings = _get_or_create_settings()
    prompt = request.form.get("gemini_summary_prompt", "").strip()
    if prompt:
        settings.gemini_summary_prompt = prompt
        record_prompt_version("gemini_summary_prompt", prompt, source="manual")
    settings.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Prompt AI został zapisany.", "success")
    return redirect(url_for("settings.index", tab="porownania"))


@bp.route("/settings/reset-prompt", methods=["POST"])
def reset_prompt():
    settings = _get_or_create_settings()
    settings.gemini_summary_prompt = DEFAULT_PROMPT
    record_prompt_version("gemini_summary_prompt", DEFAULT_PROMPT, source="reset_default")
    settings.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Prompt przywrócony do domyślnego.", "success")
    return redirect(url_for("settings.index", tab="porownania"))


@bp.route("/settings/save-comparison-prompts", methods=["POST"])
def save_comparison_prompts():
    settings = _get_or_create_settings()
    fields = {
        "comparison_prompt_extraction": request.form.get("comparison_prompt_extraction", ""),
        "comparison_prompt_comparison": request.form.get("comparison_prompt_comparison", ""),
        "comparison_prompt_summary":    request.form.get("comparison_prompt_summary", ""),
    }
    for key, value in fields.items():
        setattr(settings, key, value)
        record_prompt_version(key, value, source="manual")
    settings.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Prompty porównania zapisane.", "success")
    return redirect(url_for("settings.index", tab="porownania"))


@bp.route("/settings/reset-comparison-prompts")
def reset_comparison_prompts():
    from services.comparator import DEFAULT_PROMPT_EXTRACTION, DEFAULT_PROMPT_COMPARISON, DEFAULT_PROMPT_SUMMARY
    settings = _get_or_create_settings()
    defaults = {
        "comparison_prompt_extraction": DEFAULT_PROMPT_EXTRACTION,
        "comparison_prompt_comparison": DEFAULT_PROMPT_COMPARISON,
        "comparison_prompt_summary":    DEFAULT_PROMPT_SUMMARY,
    }
    for key, value in defaults.items():
        setattr(settings, key, value)
        record_prompt_version(key, value, source="reset_default")
    settings.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Prompty przywrócone do domyślnych.", "success")
    return redirect(url_for("settings.index", tab="porownania"))


@bp.route("/settings/restore-prompt/<key>/<int:version_id>", methods=["POST"])
def restore_prompt(key, version_id):
    if key not in PROMPT_KEYS:
        flash("Nieznany prompt.", "error")
        return redirect(url_for("settings.index", tab="porownania"))
    version = PromptVersion.query.filter_by(id=version_id, prompt_key=key).first()
    if not version:
        flash("Nie znaleziono tej wersji promptu.", "error")
        return redirect(url_for("settings.index", tab="porownania"))
    settings = _get_or_create_settings()
    setattr(settings, key, version.content)
    record_prompt_version(key, version.content, source="restore")
    settings.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Przywrócono historyczną wersję promptu.", "success")
    return redirect(url_for("settings.index", tab="porownania"))


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
    drive_api_key = request.form.get("google_drive_api_key", "").strip()
    client_id = request.form.get("google_oauth_client_id", "").strip()
    client_secret = request.form.get("google_oauth_client_secret", "").strip()
    if drive_api_key:
        settings.google_drive_api_key = drive_api_key
    if client_id:
        settings.google_oauth_client_id = client_id
    if client_secret:
        settings.google_oauth_client_secret = client_secret
    settings.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Ustawienia Google Drive zapisane.", "success")
    return redirect(url_for("settings.index", tab="integracje"))


@bp.route("/settings/test", methods=["POST"])
def test():
    import os
    settings = _get_or_create_settings()
    if not settings.gemini_api_key:
        flash("Najpierw zapisz klucz Gemini API.", "warning")
        return redirect(url_for("settings.index"))
    result = test_connection(settings.gemini_api_key, settings.gemini_model)
    redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:5002/auth/google/callback")
    history = {key: get_prompt_history(key) for key in PROMPT_KEYS}
    return render_template("settings/index.html", settings=settings, test_result=result,
                            google_redirect_uri=redirect_uri, history=history)
