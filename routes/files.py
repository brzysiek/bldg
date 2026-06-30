import json
import logging
import os
import threading
import time
import tempfile
from datetime import datetime
from types import SimpleNamespace
from flask import Blueprint, jsonify, render_template, request, redirect, url_for, flash, send_file, abort, Response, current_app
from werkzeug.utils import secure_filename
from extensions import db
from models import Competition, Edition, Document, AppSettings
from services.text_extractor import extract_text
from services.gemini import summarize_document

_logger = logging.getLogger(__name__)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

bp = Blueprint("files", __name__)



def _snap_orm(obj):
    """Convert an ORM instance to a plain SimpleNamespace.

    Reads all column attributes NOW (while session is open). The returned object
    is not bound to any session — safe to use after commit() or close().
    """
    from sqlalchemy import inspect as sa_inspect
    mapper = sa_inspect(type(obj))
    return SimpleNamespace(**{c.key: getattr(obj, c.key) for c in mapper.column_attrs})

ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx", "txt", "csv", "png", "jpg", "jpeg"}


def _allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _run_extract_bg(doc_id: int, app):
    """Run extract_document in a background thread with its own app context."""
    from services.comparator import extract_document
    with app.app_context():
        extract_document(doc_id)


def _run_extract_and_summarize_bg(doc_id: int, app):
    """Run extract_and_summarize in a background thread with its own app context."""
    from services.comparator import extract_and_summarize
    with app.app_context():
        extract_and_summarize(doc_id)


def _run_summarize_bg(doc_id: int, app):
    """Run summarization in a background thread with its own app context."""
    with app.app_context():
        doc = db.session.get(Document, doc_id)
        if not doc:
            return
        settings = AppSettings.query.first()
        if not settings or not settings.gemini_api_key:
            _db_write_error(doc_id, "Brak klucza Gemini API")
            return
        doc_snap      = _snap_orm(doc)
        settings_snap = _snap_orm(settings)
        _run_summarize(doc_snap, settings_snap)


@bp.route("/competition/<c_slug>/edition/<e_slug>/upload", methods=["GET", "POST"])
def upload(c_slug, e_slug):
    competition = Competition.query.filter_by(slug=c_slug).first_or_404()
    edition = Edition.query.filter_by(competition_id=competition.id, slug=e_slug).first_or_404()

    if request.method == "POST":
        f = request.files.get("file")
        if not f or f.filename == "":
            flash("Nie wybrano pliku.", "error")
            return redirect(request.url)
        if not _allowed(f.filename):
            flash("Niedozwolony typ pliku.", "error")
            return redirect(request.url)

        original_name = f.filename
        safe_name = secure_filename(original_name)
        ext = safe_name.rsplit(".", 1)[-1] if "." in safe_name else ""
        base = safe_name.rsplit(".", 1)[0] if "." in safe_name else safe_name
        ts = str(int(time.time()))
        stored_name = f"{base}_{ts}.{ext}" if ext else f"{base}_{ts}"

        storage_dir = os.path.join(BASE_DIR, "storage", competition.slug, edition.slug)
        os.makedirs(storage_dir, exist_ok=True)
        stored_path = os.path.join(storage_dir, stored_name)
        f.save(stored_path)

        size = os.path.getsize(stored_path)
        mime = f.content_type or ""

        doc = Document(
            edition_id=edition.id,
            original_name=original_name,
            stored_path=stored_path,
            file_size=size,
            mime_type=mime,
            version_label=request.form.get("version_label", "").strip() or None,
            notes=request.form.get("notes", "").strip() or None,
        )
        db.session.add(doc)
        db.session.commit()

        settings = AppSettings.query.first()
        if settings and settings.gemini_api_key:
            app = current_app._get_current_object()
            threading.Thread(target=_run_extract_bg, args=(doc.id, app), daemon=True).start()

        flash(f'Plik "{original_name}" zostal wgrany.', "success")
        return redirect(url_for("editions.detail", c_slug=c_slug, e_slug=e_slug))

    return render_template("file/upload.html", competition=competition, edition=edition)


@bp.route("/files/<int:file_id>/download")
def download(file_id):
    doc = Document.query.get_or_404(file_id)
    if not os.path.exists(doc.stored_path):
        abort(404)
    return send_file(doc.stored_path, as_attachment=True, download_name=doc.original_name)


@bp.route("/files/<int:file_id>/delete", methods=["POST"])
def delete(file_id):
    doc = Document.query.get_or_404(file_id)
    edition = doc.edition
    competition = edition.competition

    if os.path.exists(doc.stored_path):
        os.remove(doc.stored_path)

    db.session.delete(doc)
    db.session.commit()
    flash(f'Plik "{doc.original_name}" zostal usuniety.', "success")
    return redirect(url_for("editions.detail", c_slug=competition.slug, e_slug=edition.slug))


def _db_write_error(doc_id: int, error: str) -> None:
    """Write error status with a fresh DB connection (safe after broken pipe)."""
    try:
        db.session.rollback()
    except Exception:
        pass
    db.session.expire_all()
    try:
        d = db.session.get(Document, doc_id)
        if d:
            d.ai_summary_status = "error"
            d.ai_summary_error = error
            db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _run_summarize(doc, settings):
    """Extracts text and runs Gemini summarization. Returns (ok, error_msg)."""
    import shutil

    doc_id = doc.id
    local_path = doc.stored_path
    # Capture existing description now — will be preserved if Gemini returns empty one
    _old_description = doc.ai_description or ""
    _tmp_dir = None

    if doc.gdrive_file_id:
        from services.google_drive import get_drive_credentials, download_file
        creds = get_drive_credentials()
        if not creds:
            return False, "Brak autoryzacji Drive — zaloguj się ponownie, aby udzielić dostępu"
        try:
            _tmp_dir = tempfile.mkdtemp()
            local_path = download_file(doc.gdrive_file_id, doc.original_name, doc.mime_type or "application/pdf", _tmp_dir, creds.token)
        except Exception as e:
            return False, f"Błąd pobierania z Drive: {e}"

    try:
        text = extract_text(local_path, doc.mime_type or "")
    except Exception as e:
        return False, f"Błąd ekstrakcji tekstu: {e}"
    finally:
        if _tmp_dir:
            shutil.rmtree(_tmp_dir, ignore_errors=True)

    if not text or len(text.strip()) < 50:
        return False, "Nie udało się wyodrębnić tekstu — plik może być zeskanowany lub zabezpieczony"

    if len(text) > 400_000:
        text = text[:400_000] + "\n[TEKST OBCIĘTY DO 400 000 ZNAKÓW]"

    # Capture model name before Gemini call (settings is expired by caller's commit)
    gemini_model = settings.gemini_model

    try:
        result = summarize_document(text, settings)
    except Exception as e:
        _db_write_error(doc_id, str(e))
        return False, str(e)

    db.session.expire_all()

    try:
        fresh = db.session.get(Document, doc_id)
        fresh.ai_summary = result["summary"]
        new_desc = result.get("description", "") or ""
        fresh.ai_description = new_desc if new_desc else _old_description
        fresh.ai_summary_model = gemini_model
        fresh.ai_summarized_at = datetime.utcnow()
        fresh.ai_summary_status = "done"
        fresh.ai_summary_error = None
        db.session.commit()
        # Sync caller's in-memory reference so summarize_json can read attributes
        doc.ai_summary = fresh.ai_summary
        doc.ai_description = fresh.ai_description
        return True, None
    except Exception as e:
        _db_write_error(doc_id, str(e))
        return False, str(e)


@bp.route("/files/<int:file_id>/summarize", methods=["POST"])
def summarize(file_id):
    doc = Document.query.get_or_404(file_id)
    edition = doc.edition
    competition = edition.competition
    settings = AppSettings.query.first()

    if not settings or not settings.gemini_api_key:
        flash("Brak klucza Gemini API. Przejdź do ⚙️ Ustawień.", "warning")
        return redirect(url_for("editions.detail", c_slug=competition.slug, e_slug=edition.slug))

    # Snapshot before commit — commit() expires ORM attrs, causing lazy reloads
    # that hold a DB connection through the long Gemini call.
    doc_snap      = _snap_orm(doc)
    settings_snap = _snap_orm(settings)
    c_slug        = competition.slug
    e_slug        = edition.slug

    doc.ai_summary_status = "pending"
    db.session.commit()

    ok, err = _run_summarize(doc_snap, settings_snap)
    if ok:
        flash("Podsumowanie AI zostało wygenerowane.", "success")
    else:
        flash(f"Błąd: {err}", "error")

    return redirect(url_for("editions.detail", c_slug=c_slug, e_slug=e_slug))


@bp.route("/files/<int:file_id>/summarize-json", methods=["POST"])
def summarize_json(file_id):
    """Start background summarization. Returns immediately; poll extract-status for result."""
    doc = db.session.get(Document, file_id)
    if doc is None:
        return jsonify({"ok": False, "file_id": file_id, "error": f"Dokument {file_id} nie istnieje"}), 404

    settings = AppSettings.query.first()
    if not settings or not settings.gemini_api_key:
        return jsonify({"ok": False, "file_id": file_id, "error": "Brak klucza Gemini API w ustawieniach"})

    doc.ai_summary_status     = "pending"
    doc.ai_summary_error      = None
    doc.ai_summary_started_at = datetime.utcnow()
    db.session.commit()

    app_obj = current_app._get_current_object()
    threading.Thread(target=_run_summarize_bg, args=(file_id, app_obj), daemon=True).start()
    _logger.info("summarize_json: spawned thread file_id=%s name=%s", file_id, doc.original_name)
    return jsonify({"ok": True, "file_id": file_id, "started": True})


@bp.route("/files/<int:file_id>/cancel-summary", methods=["POST"])
def cancel_summary(file_id):
    doc = db.session.get(Document, file_id)
    if doc is None:
        return jsonify({"ok": False, "error": "Dokument nie istnieje"}), 404
    if doc.ai_summary_status == "pending":
        doc.ai_summary_status = None
        db.session.commit()
    return jsonify({"ok": True, "file_id": file_id})


@bp.route("/files/<int:file_id>/extract-json", methods=["POST"])
def extract_json(file_id):
    """Start background extraction for one document. Returns immediately; poll extract-status."""
    doc = db.session.get(Document, file_id)
    if doc is None:
        return jsonify({"ok": False, "file_id": file_id, "error": "Dokument nie istnieje"}), 404

    settings = AppSettings.query.first()
    if not settings or not settings.gemini_api_key:
        return jsonify({"ok": False, "file_id": file_id, "error": "Brak klucza Gemini API"})

    doc.extraction_status     = "pending"
    doc.extraction_error      = None
    doc.extraction_started_at = datetime.utcnow()
    db.session.commit()

    app = current_app._get_current_object()
    threading.Thread(target=_run_extract_bg, args=(file_id, app), daemon=True).start()
    _logger.info("extract_json: spawned thread file_id=%s name=%s", file_id, doc.original_name)
    return jsonify({"ok": True, "file_id": file_id, "started": True})


@bp.route("/files/<int:file_id>/extract-and-summarize-json", methods=["POST"])
def extract_and_summarize_json(file_id):
    """Start combined background extraction+summarization. Downloads file once. Returns immediately."""
    doc = db.session.get(Document, file_id)
    if doc is None:
        return jsonify({"ok": False, "file_id": file_id, "error": "Dokument nie istnieje"}), 404

    settings = AppSettings.query.first()
    if not settings or not settings.gemini_api_key:
        return jsonify({"ok": False, "file_id": file_id, "error": "Brak klucza Gemini API"})

    doc.extraction_status     = "pending"
    doc.extraction_error      = None
    doc.extraction_started_at = datetime.utcnow()
    doc.ai_summary_status     = "pending"
    doc.ai_summary_error      = None
    doc.ai_summary_started_at = datetime.utcnow()
    db.session.commit()

    app_obj = current_app._get_current_object()
    threading.Thread(target=_run_extract_and_summarize_bg, args=(file_id, app_obj), daemon=True).start()
    _logger.info("extract_and_summarize_json: spawned thread file_id=%s name=%s", file_id, doc.original_name)
    return jsonify({"ok": True, "file_id": file_id, "started": True})


@bp.route("/files/<int:file_id>/extract-status")
def extract_status(file_id):
    """Return current extraction (and summary) status for polling."""
    doc = db.session.get(Document, file_id)
    if doc is None:
        return jsonify({"ok": False, "error": "Dokument nie istnieje"}), 404
    return jsonify({
        "ok":             True,
        "file_id":        file_id,
        "status":         doc.extraction_status,
        "error":          doc.extraction_error,
        "summary_status": doc.ai_summary_status,
        "summary_error":  doc.ai_summary_error,
        "description":    doc.ai_description,
        "summary":        doc.ai_summary or "",
    })


@bp.route("/files/<int:file_id>/cancel-extraction", methods=["POST"])
def cancel_extraction(file_id):
    doc = db.session.get(Document, file_id)
    if doc is None:
        return jsonify({"ok": False, "error": "Dokument nie istnieje"}), 404
    if doc.extraction_status == "pending":
        doc.extraction_status = None
        db.session.commit()
    return jsonify({"ok": True, "file_id": file_id})


@bp.route("/files/<int:file_id>/summary")
def summary(file_id):
    doc = Document.query.get_or_404(file_id)
    edition = doc.edition
    competition = edition.competition

    summary_html = None
    if doc.ai_summary:
        import markdown as md_lib
        summary_html = md_lib.markdown(doc.ai_summary, extensions=["tables", "fenced_code"])

    return render_template(
        "file/summary.html",
        doc=doc,
        edition=edition,
        competition=competition,
        summary_html=summary_html,
    )


@bp.route("/files/<int:file_id>/summary-json")
def summary_json(file_id):
    doc = Document.query.get_or_404(file_id)
    return jsonify({"id": file_id, "summary": doc.ai_summary or ""})


@bp.route("/files/<int:file_id>/segments-json")
def segments_json(file_id):
    doc = Document.query.get_or_404(file_id)
    if not doc.extraction_cache_json:
        return jsonify({"id": file_id, "tytul": "", "sekcje": {}, "count": 0})
    try:
        data = json.loads(doc.extraction_cache_json)
    except Exception:
        return jsonify({"id": file_id, "tytul": "", "sekcje": {}, "count": 0})
    sekcje = data.get("sekcje", {})
    return jsonify({
        "id": file_id,
        "tytul": data.get("tytul", ""),
        "sekcje": sekcje,
        "count": len(sekcje),
    })


@bp.route("/files/<int:file_id>/summary/download")
def summary_download(file_id):
    doc = Document.query.get_or_404(file_id)
    if not doc.ai_summary:
        abort(404)
    filename = f"podsumowanie_{doc.original_name}.md"
    return Response(
        doc.ai_summary,
        mimetype="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
