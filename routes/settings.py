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
    "comparison_prompt_edition",
]

# Maps stage_key → (prompt_field, model_field, temperature_field, max_tokens_field, system_field)
STAGE_KEYS = {
    "doc_summary": (
        "gemini_summary_prompt",
        "doc_summary_model", "doc_summary_temperature",
        "doc_summary_max_tokens", "doc_summary_system",
    ),
    "extraction": (
        "comparison_prompt_extraction",
        "cmp_extraction_model", "cmp_extraction_temperature",
        "cmp_extraction_max_tokens", "cmp_extraction_system",
    ),
    "comparison": (
        "comparison_prompt_comparison",
        "cmp_comparison_model", "cmp_comparison_temperature",
        "cmp_comparison_max_tokens", "cmp_comparison_system",
    ),
    "summary": (
        "comparison_prompt_summary",
        "cmp_summary_model", "cmp_summary_temperature",
        "cmp_summary_max_tokens", "cmp_summary_system",
    ),
    "edition": (
        "comparison_prompt_edition",
        "cmp_edition_model", "cmp_edition_temperature",
        "cmp_edition_max_tokens", "cmp_edition_system",
    ),
}

# Which tab to redirect to after saving/resetting each stage
STAGE_TAB = {
    "doc_summary": "podsumowanie",
    "extraction":  "porownania",
    "comparison":  "porownania",
    "summary":     "porownania",
    "edition":     "porownania",
}


def _stage_default_prompt(stage: str) -> str:
    if stage == "doc_summary":
        return DEFAULT_PROMPT
    from services.comparator import (
        DEFAULT_PROMPT_EXTRACTION, DEFAULT_PROMPT_COMPARISON,
        DEFAULT_PROMPT_SUMMARY, DEFAULT_PROMPT_EDITION_SUMMARY,
    )
    return {
        "extraction": DEFAULT_PROMPT_EXTRACTION,
        "comparison": DEFAULT_PROMPT_COMPARISON,
        "summary":    DEFAULT_PROMPT_SUMMARY,
        "edition":    DEFAULT_PROMPT_EDITION_SUMMARY,
    }[stage]


def _get_or_create_settings():
    s = AppSettings.query.first()
    if not s:
        s = AppSettings(id=1, gemini_model="gemini-2.5-flash", gemini_summary_prompt=DEFAULT_PROMPT)
        db.session.add(s)
        record_prompt_version("gemini_summary_prompt", DEFAULT_PROMPT, source="seed")
        db.session.commit()
    return s


def _build_stage_data(settings):
    """Return a dict of per-stage config values for the template."""
    result = {}
    for stage_key, (pf, mf, tf, xf, sf) in STAGE_KEYS.items():
        result[stage_key] = {
            "prompt":      getattr(settings, pf, None) or "",
            "model":       getattr(settings, mf, None) or "",
            "temperature": getattr(settings, tf, None),
            "max_tokens":  getattr(settings, xf, None),
            "system":      getattr(settings, sf, None) or "",
        }
    return result


@bp.route("/settings")
def index():
    import os
    settings = _get_or_create_settings()
    redirect_uri = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:5002/auth/google/callback")
    history = {key: get_prompt_history(key) for key in PROMPT_KEYS}
    stage_data = _build_stage_data(settings)
    return render_template(
        "settings/index.html",
        settings=settings,
        test_result=None,
        google_redirect_uri=redirect_uri,
        history=history,
        stage_data=stage_data,
    )


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


@bp.route("/settings/save-stage/<stage>", methods=["POST"])
def save_stage(stage):
    if stage not in STAGE_KEYS:
        flash("Nieznany etap.", "error")
        return redirect(url_for("settings.index", tab="porownania"))

    pf, mf, tf, xf, sf = STAGE_KEYS[stage]
    settings = _get_or_create_settings()

    # Prompt
    prompt = request.form.get("prompt", "")
    setattr(settings, pf, prompt)
    record_prompt_version(pf, prompt, source="manual")

    # Model override (empty → None → falls back to global model at call time)
    model_val = request.form.get("model", "").strip() or None
    setattr(settings, mf, model_val)

    # Temperature (nullable float)
    temp_str = request.form.get("temperature", "").strip()
    setattr(settings, tf, float(temp_str) if temp_str else None)

    # Max output tokens (nullable int)
    max_str = request.form.get("max_tokens", "").strip()
    setattr(settings, xf, int(max_str) if max_str else None)

    # System instruction (empty → None)
    system_val = request.form.get("system_instruction", "").strip() or None
    setattr(settings, sf, system_val)

    settings.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Konfiguracja etapu zapisana.", "success")
    return redirect(url_for("settings.index", tab=STAGE_TAB[stage]))


@bp.route("/settings/reset-stage/<stage>", methods=["POST"])
def reset_stage(stage):
    if stage not in STAGE_KEYS:
        flash("Nieznany etap.", "error")
        return redirect(url_for("settings.index", tab="porownania"))

    pf, mf, tf, xf, sf = STAGE_KEYS[stage]
    settings = _get_or_create_settings()

    default_prompt = _stage_default_prompt(stage)
    setattr(settings, pf, default_prompt)
    record_prompt_version(pf, default_prompt, source="reset_default")

    # Clear all overrides
    setattr(settings, mf, None)
    setattr(settings, tf, None)
    setattr(settings, xf, None)
    setattr(settings, sf, None)

    settings.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Konfiguracja etapu przywrócona do domyślnych.", "success")
    return redirect(url_for("settings.index", tab=STAGE_TAB[stage]))


# ── Legacy routes kept for backwards compatibility ────────────────────────────

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
    return redirect(url_for("settings.index", tab="podsumowanie"))


@bp.route("/settings/reset-prompt", methods=["POST"])
def reset_prompt():
    settings = _get_or_create_settings()
    settings.gemini_summary_prompt = DEFAULT_PROMPT
    record_prompt_version("gemini_summary_prompt", DEFAULT_PROMPT, source="reset_default")
    settings.updated_at = datetime.utcnow()
    db.session.commit()
    flash("Prompt przywrócony do domyślnego.", "success")
    return redirect(url_for("settings.index", tab="podsumowanie"))


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
    tab = "podsumowanie" if key == "gemini_summary_prompt" else "porownania"
    return redirect(url_for("settings.index", tab=tab))


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
            supported = getattr(m, "supported_actions", None) or getattr(m, "supported_generation_methods", [])
            if not any("generate" in str(s).lower() for s in supported):
                continue
            display = getattr(m, "display_name", model_id) or model_id
            result.append({"id": model_id, "display_name": display})
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
    stage_data = _build_stage_data(settings)
    return render_template(
        "settings/index.html",
        settings=settings,
        test_result=result,
        google_redirect_uri=redirect_uri,
        history=history,
        stage_data=stage_data,
    )
