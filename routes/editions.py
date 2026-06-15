import os
from datetime import date, datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from extensions import db
from models import Competition, Edition, Document, AppSettings
from utils import slugify

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


@bp.route("/competition/<c_slug>/edition/<e_slug>")
def detail(c_slug, e_slug):
    competition = Competition.query.filter_by(slug=c_slug).first_or_404()
    edition = Edition.query.filter_by(competition_id=competition.id, slug=e_slug).first_or_404()
    settings = AppSettings.query.first()
    has_gemini = bool(settings and settings.gemini_api_key)
    has_gdrive = bool(settings and settings.google_access_token)
    all_docs = sorted(edition.documents, key=lambda d: d.uploaded_at or datetime.min, reverse=True)
    return render_template(
        "edition/detail.html",
        competition=competition,
        edition=edition,
        has_gemini=has_gemini,
        has_gdrive=has_gdrive,
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

    if not settings or not settings.google_access_token:
        flash("Polacz konto Google Drive w Ustawieniach.", "warning")
        return redirect(url_for("editions.detail", c_slug=c_slug, e_slug=e_slug))

    if not edition.gdrive_folder_id:
        flash("Ustaw link do folderu Google Drive dla tej edycji.", "warning")
        return redirect(url_for("editions.detail", c_slug=c_slug, e_slug=e_slug))

    try:
        from services.google_drive import list_folder_files, download_file as gdrive_download
        files = list_folder_files(edition.gdrive_folder_id, settings)
    except Exception as e:
        flash(f"Blad pobierania listy plikow z Drive: {e}", "error")
        return redirect(url_for("editions.detail", c_slug=c_slug, e_slug=e_slug))

    dest_dir = os.path.join("storage", competition.slug, edition.slug, "gdrive")
    os.makedirs(dest_dir, exist_ok=True)

    added = 0
    updated = 0
    errors = []

    for file_meta in files:
        gid = file_meta["id"]
        existing = Document.query.filter_by(gdrive_file_id=gid).first()
        try:
            dest_path = gdrive_download(gid, file_meta["name"], file_meta["mime_type"], dest_dir, settings)
            size = os.path.getsize(dest_path)

            if existing:
                existing.stored_path = dest_path
                existing.file_size = size
                updated += 1
            else:
                doc = Document(
                    edition_id=edition.id,
                    original_name=file_meta["name"],
                    stored_path=dest_path,
                    file_size=size,
                    mime_type=file_meta["mime_type"],
                    gdrive_file_id=gid,
                )
                db.session.add(doc)
                added += 1
        except Exception as e:
            errors.append(f"{file_meta['name']}: {e}")

    edition.gdrive_synced_at = datetime.utcnow()
    db.session.commit()

    if errors:
        flash(f"Synchronizacja czesciowa: +{added} nowych, {updated} zaktualizowanych. Bledy: {'; '.join(errors[:3])}", "warning")
    else:
        flash(f"Synchronizacja zakonczona: +{added} nowych, {updated} zaktualizowanych.", "success")

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
