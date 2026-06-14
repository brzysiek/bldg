from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash
from extensions import db
from models import Competition, Edition
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

        slug = slugify(name)
        existing_slugs = [e.slug for e in competition.editions]
        if slug in existing_slugs:
            slug = slug + "-" + str(len(existing_slugs) + 1)

        deadline = date.fromisoformat(deadline_raw) if deadline_raw else None
        year = int(year_raw) if year_raw else None

        e = Edition(
            competition_id=competition.id,
            name=name,
            slug=slug,
            year=year,
            status=status,
            deadline=deadline,
            description=description or None,
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
    from models import AppSettings
    settings = AppSettings.query.first()
    has_gemini = bool(settings and settings.gemini_api_key)
    return render_template("edition/detail.html", competition=competition, edition=edition, has_gemini=has_gemini)


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
