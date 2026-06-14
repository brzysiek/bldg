from flask import Blueprint, render_template, request, redirect, url_for, flash
from extensions import db
from models import Competition, Edition, DocumentType
from utils import slugify

bp = Blueprint("document_types", __name__)


@bp.route("/competition/<c_slug>/edition/<e_slug>/doctype/new", methods=["GET", "POST"])
def new(c_slug, e_slug):
    competition = Competition.query.filter_by(slug=c_slug).first_or_404()
    edition = Edition.query.filter_by(competition_id=competition.id, slug=e_slug).first_or_404()
    if request.method == "POST":
        name = request.form["name"].strip()
        description = request.form.get("description", "").strip()
        slug = slugify(name)
        existing_slugs = [dt.slug for dt in edition.document_types]
        if slug in existing_slugs:
            slug = slug + "-" + str(len(existing_slugs) + 1)
        order_index = len(edition.document_types)
        dt = DocumentType(
            edition_id=edition.id,
            name=name,
            slug=slug,
            order_index=order_index,
            description=description or None,
        )
        db.session.add(dt)
        db.session.commit()
        flash(f'Typ dokumentu "{name}" zostal dodany.', "success")
        return redirect(url_for("editions.detail", c_slug=c_slug, e_slug=e_slug))
    return render_template("doc_type/form.html", competition=competition, edition=edition, doc_type=None)


@bp.route("/competition/<c_slug>/edition/<e_slug>/doctype/<int:dt_id>/edit", methods=["GET", "POST"])
def edit(c_slug, e_slug, dt_id):
    competition = Competition.query.filter_by(slug=c_slug).first_or_404()
    edition = Edition.query.filter_by(competition_id=competition.id, slug=e_slug).first_or_404()
    doc_type = DocumentType.query.get_or_404(dt_id)
    if request.method == "POST":
        doc_type.name = request.form["name"].strip()
        doc_type.description = request.form.get("description", "").strip() or None
        db.session.commit()
        flash("Typ dokumentu zaktualizowany.", "success")
        return redirect(url_for("editions.detail", c_slug=c_slug, e_slug=e_slug))
    return render_template("doc_type/form.html", competition=competition, edition=edition, doc_type=doc_type)


@bp.route("/competition/<c_slug>/edition/<e_slug>/doctype/<int:dt_id>/delete", methods=["POST"])
def delete(c_slug, e_slug, dt_id):
    competition = Competition.query.filter_by(slug=c_slug).first_or_404()
    edition = Edition.query.filter_by(competition_id=competition.id, slug=e_slug).first_or_404()
    doc_type = DocumentType.query.get_or_404(dt_id)
    db.session.delete(doc_type)
    db.session.commit()
    flash(f'Typ dokumentu "{doc_type.name}" zostal usuniety.', "success")
    return redirect(url_for("editions.detail", c_slug=c_slug, e_slug=e_slug))
