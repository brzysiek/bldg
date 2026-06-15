import os
import time
import tempfile
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, abort, Response
from werkzeug.utils import secure_filename
import markdown as md_lib
from extensions import db
from models import Competition, Edition, Document, AppSettings
from services.text_extractor import extract_text
from services.gemini import summarize_document

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

bp = Blueprint("files", __name__)

ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx", "txt", "csv", "png", "jpg", "jpeg"}


def _allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


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


def _run_summarize(doc, settings):
    """Extracts text and runs Gemini summarization. Returns (ok, error_msg)."""
    import shutil

    local_path = doc.stored_path
    _tmp_dir = None

    if doc.gdrive_file_id:
        if not settings.google_drive_api_key and not settings.google_access_token:
            return False, "Brak klucza Drive API — nie można pobrać pliku do analizy"
        try:
            _tmp_dir = tempfile.mkdtemp()
            if settings.google_access_token:
                from services.google_drive import download_file
                local_path = download_file(doc.gdrive_file_id, doc.original_name, doc.mime_type or "application/pdf", _tmp_dir, settings)
            else:
                from services.google_drive import download_file_public
                local_path = download_file_public(doc.gdrive_file_id, doc.original_name, doc.mime_type or "application/pdf", _tmp_dir, settings.google_drive_api_key)
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

    try:
        result = summarize_document(text, settings)
        doc.ai_summary = result["summary"]
        doc.ai_description = result.get("description", "") or ""
        doc.ai_summary_model = settings.gemini_model
        doc.ai_summarized_at = datetime.utcnow()
        doc.ai_summary_status = "done"
        doc.ai_summary_error = None
        db.session.commit()
        return True, None
    except Exception as e:
        doc.ai_summary_status = "error"
        doc.ai_summary_error = str(e)
        db.session.commit()
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

    doc.ai_summary_status = "pending"
    db.session.commit()

    ok, err = _run_summarize(doc, settings)
    if ok:
        flash("Podsumowanie AI zostało wygenerowane.", "success")
    else:
        flash(f"Błąd: {err}", "error")

    return redirect(url_for("editions.detail", c_slug=competition.slug, e_slug=edition.slug))


@bp.route("/files/<int:file_id>/summarize-json", methods=["POST"])
def summarize_json(file_id):
    from flask import jsonify
    doc = Document.query.get_or_404(file_id)
    settings = AppSettings.query.first()

    if not settings or not settings.gemini_api_key:
        return jsonify({"ok": False, "file_id": file_id, "error": "Brak klucza Gemini API"})

    doc.ai_summary_status = "pending"
    db.session.commit()

    ok, err = _run_summarize(doc, settings)
    if ok:
        return jsonify({
            "ok": True,
            "file_id": file_id,
            "description": doc.ai_description or "",
            "summary": doc.ai_summary or "",
        })
    else:
        return jsonify({"ok": False, "file_id": file_id, "error": err})


@bp.route("/files/<int:file_id>/summary")
def summary(file_id):
    doc = Document.query.get_or_404(file_id)
    edition = doc.edition
    competition = edition.competition

    summary_html = None
    if doc.ai_summary:
        summary_html = md_lib.markdown(doc.ai_summary, extensions=["tables", "fenced_code"])

    return render_template(
        "file/summary.html",
        doc=doc,
        edition=edition,
        competition=competition,
        summary_html=summary_html,
    )


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
