import logging
import os
import threading
import time as _time
from datetime import date, datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from extensions import db
from models import Competition, Edition, Document, AppSettings
from utils import slugify

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_logger = logging.getLogger(__name__)
_sync_in_progress: dict[int, bool] = {}

bp = Blueprint("editions", __name__)


@bp.route("/competition/<c_slug>/edition/new", methods=["GET", "POST"])
def new(c_slug):
    competition = Competition.query.filter_by(slug=c_slug).first_or_404()
    if request.method == "POST":
        name = request.form["name"].strip()
        year_raw = request.form.get("year", "").strip()
        status = request.form.get("status", "aktywna")
        deadline_raw = request.form.get("deadline", "").strip()
        description = request.form.get("description", "").strip()
        gdrive_folder_url = request.form.get("gdrive_folder_url", "").strip()

        slug = slugify(name)
        existing_slugs = [e.slug for e in competition.editions]
        if slug in existing_slugs:
            slug = slug + "-" + str(len(existing_slugs) + 1)

        deadline = date.fromisoformat(deadline_raw) if deadline_raw else None
        year = int(year_raw) if year_raw else None

        gdrive_folder_id = None
        if gdrive_folder_url:
            from services.google_drive import extract_folder_id
            gdrive_folder_id = extract_folder_id(gdrive_folder_url)

        e = Edition(
            competition_id=competition.id,
            name=name,
            slug=slug,
            year=year,
            status=status,
            deadline=deadline,
            description=description or None,
            gdrive_folder_url=gdrive_folder_url or None,
            gdrive_folder_id=gdrive_folder_id,
        )
        db.session.add(e)
        db.session.commit()
        flash(f'Edycja "{name}" zostala dodana.', "success")
        return redirect(url_for("editions.detail", c_slug=c_slug, e_slug=slug))
    return render_template("edition/form.html", competition=competition, edition=None)


def _gdrive_mode(settings) -> str:
    """Returns 'oauth', 'public', or 'none'."""
    if settings and settings.google_access_token:
        return "oauth"
    if settings and settings.google_drive_api_key:
        return "public"
    return "none"


@bp.route("/competition/<c_slug>/edition/<e_slug>")
def detail(c_slug, e_slug):
    competition = Competition.query.filter_by(slug=c_slug).first_or_404()
    edition = Edition.query.filter_by(competition_id=competition.id, slug=e_slug).first_or_404()
    settings = AppSettings.query.first()
    has_gemini = bool(settings and settings.gemini_api_key)
    all_docs = sorted(edition.documents, key=lambda d: d.uploaded_at or datetime.min, reverse=True)
    return render_template(
        "edition/detail.html",
        competition=competition,
        edition=edition,
        has_gemini=has_gemini,
        gdrive_mode=_gdrive_mode(settings),
        all_docs=all_docs,
        settings=settings,
    )


@bp.route("/competition/<c_slug>/edition/<e_slug>/edit", methods=["GET", "POST"])
def edit(c_slug, e_slug):
    competition = Competition.query.filter_by(slug=c_slug).first_or_404()
    edition = Edition.query.filter_by(competition_id=competition.id, slug=e_slug).first_or_404()
    if request.method == "POST":
        edition.name = request.form["name"].strip()
        year_raw = request.form.get("year", "").strip()
        edition.year = int(year_raw) if year_raw else None
        edition.status = request.form.get("status", "aktywna")
        deadline_raw = request.form.get("deadline", "").strip()
        edition.deadline = date.fromisoformat(deadline_raw) if deadline_raw else None
        edition.description = request.form.get("description", "").strip() or None
        gdrive_url = request.form.get("gdrive_folder_url", "").strip()
        edition.gdrive_folder_url = gdrive_url or None
        if gdrive_url:
            from services.google_drive import extract_folder_id
            edition.gdrive_folder_id = extract_folder_id(gdrive_url)
        else:
            edition.gdrive_folder_id = None
        db.session.commit()
        flash("Edycja zaktualizowana.", "success")
        return redirect(url_for("editions.detail", c_slug=c_slug, e_slug=edition.slug))
    return render_template("edition/form.html", competition=competition, edition=edition)


@bp.route("/competition/<c_slug>/edition/<e_slug>/delete", methods=["POST"])
def delete(c_slug, e_slug):
    competition = Competition.query.filter_by(slug=c_slug).first_or_404()
    edition = Edition.query.filter_by(competition_id=competition.id, slug=e_slug).first_or_404()
    db.session.delete(edition)
    db.session.commit()
    flash(f'Edycja "{edition.name}" zostala usunieta.', "success")
    return redirect(url_for("competitions.detail", slug=c_slug))


@bp.route("/competition/<c_slug>/edition/<e_slug>/sync-drive", methods=["POST"])
def sync_drive(c_slug, e_slug):
    competition = Competition.query.filter_by(slug=c_slug).first_or_404()
    edition = Edition.query.filter_by(competition_id=competition.id, slug=e_slug).first_or_404()
    settings = AppSettings.query.first()

    mode = _gdrive_mode(settings)
    if mode == "none":
        flash("Brak dostępu do Google Drive. Skonfiguruj OAuth lub klucz API w Ustawieniach.", "warning")
        return redirect(url_for("editions.detail", c_slug=c_slug, e_slug=e_slug))

    if not edition.gdrive_folder_id:
        flash("Ustaw link do folderu Google Drive dla tej edycji.", "warning")
        return redirect(url_for("editions.detail", c_slug=c_slug, e_slug=e_slug))

    app = current_app._get_current_object()
    edition_id = edition.id
    folder_id = edition.gdrive_folder_id
    comp_slug = competition.slug
    ed_slug = edition.slug
    drive_api_key = settings.google_drive_api_key if mode == "public" else None

    def _sync_bg():
        with app.app_context():
            try:
                _logger.info("sync_drive: start edition=%s folder=%s mode=%s", edition_id, folder_id, mode)
                try:
                    if mode == "oauth":
                        from services.google_drive import list_folder_files, download_file as _dl
                        files = list_folder_files(folder_id, AppSettings.query.first())
                    else:
                        from services.google_drive import list_folder_files_public, download_file_public as _dl
                        files = list_folder_files_public(folder_id, drive_api_key)
                except Exception as exc:
                    _logger.error("sync_drive: list error: %s", exc)
                    return

                _logger.info("sync_drive: found %d files", len(files))
                dest_dir = os.path.join(BASE_DIR, "storage", comp_slug, ed_slug, "gdrive")
                os.makedirs(dest_dir, exist_ok=True)

                for f in files:
                    gid = f["id"]
                    existing = Document.query.filter_by(gdrive_file_id=gid).first()
                    last_exc = None
                    dest_path = None
                    for attempt in range(3):
                        try:
                            if mode == "oauth":
                                dest_path = _dl(gid, f["name"], f["mime_type"], dest_dir, AppSettings.query.first())
                            else:
                                dest_path = _dl(gid, f["name"], f["mime_type"], dest_dir, drive_api_key)
                            last_exc = None
                            break
                        except Exception as exc:
                            last_exc = exc
                            _logger.warning("sync_drive: attempt %d failed for %s: %s", attempt + 1, f["name"], exc)
                            _time.sleep(2 * (attempt + 1))

                    if last_exc:
                        _logger.error("sync_drive: download failed %s: %s", f["name"], last_exc)
                        continue

                    size = os.path.getsize(dest_path)
                    if existing:
                        existing.stored_path = dest_path
                        existing.file_size = size
                    else:
                        db.session.add(Document(
                            edition_id=edition_id,
                            original_name=f["name"],
                            stored_path=dest_path,
                            file_size=size,
                            mime_type=f["mime_type"],
                            gdrive_file_id=gid,
                        ))
                    _logger.info("sync_drive: saved %s (%s bytes)", f["name"], size)

                ed = db.session.get(Edition, edition_id)
                if ed:
                    ed.gdrive_synced_at = datetime.utcnow()
                db.session.commit()
                _logger.info("sync_drive: done edition=%s", edition_id)
            finally:
                _sync_in_progress.pop(edition_id, None)

    _sync_in_progress[edition_id] = True
    threading.Thread(target=_sync_bg, daemon=True, name=f"sync-drive-{edition_id}").start()
    flash("Synchronizacja uruchomiona w tle. Strona odświeży się automatycznie.", "info")
    return redirect(url_for("editions.detail", c_slug=c_slug, e_slug=e_slug))


@bp.route("/competition/<c_slug>/edition/<e_slug>/set-drive-url", methods=["POST"])
def set_drive_url(c_slug, e_slug):
    competition = Competition.query.filter_by(slug=c_slug).first_or_404()
    edition = Edition.query.filter_by(competition_id=competition.id, slug=e_slug).first_or_404()
    url = request.form.get("gdrive_folder_url", "").strip()
    edition.gdrive_folder_url = url or None
    if url:
        from services.google_drive import extract_folder_id
        folder_id = extract_folder_id(url)
        edition.gdrive_folder_id = folder_id
        if not folder_id:
            flash("Nie udalo sie odczytac ID folderu z podanego linku.", "warning")
        else:
            flash("Link do folderu Drive zapisany.", "success")
    else:
        edition.gdrive_folder_id = None
        flash("Link do folderu Drive usunieto.", "success")
    db.session.commit()
    return redirect(url_for("editions.detail", c_slug=c_slug, e_slug=e_slug))


@bp.route("/api/editions/<int:edition_id>/sync-status")
def api_sync_status(edition_id):
    edition = db.session.get(Edition, edition_id)
    if not edition:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "syncing": _sync_in_progress.get(edition_id, False),
        "synced_at": edition.gdrive_synced_at.isoformat() if edition.gdrive_synced_at else None,
        "doc_count": len(edition.documents),
    })


@bp.route("/api/competitions/<int:comp_id>/editions")
def api_editions(comp_id):
    editions = Edition.query.filter_by(competition_id=comp_id).order_by(Edition.year.desc(), Edition.name).all()
    return jsonify([{"id": e.id, "name": e.name, "year": e.year} for e in editions])


@bp.route("/api/editions/<int:edition_id>/files")
def api_edition_files(edition_id):
    edition = db.session.get(Edition, edition_id)
    if not edition:
        return jsonify([])
    docs = [
        {"id": doc.id, "name": doc.original_name, "size": doc.file_size or 0, "from_gdrive": bool(doc.gdrive_file_id)}
        for doc in edition.documents
    ]
    docs.sort(key=lambda d: d["name"])
    return jsonify(docs)
