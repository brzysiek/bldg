from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from extensions import db
from models import Competition, Edition
from utils import slugify

bp = Blueprint("competitions", __name__)


@bp.route("/")
def index():
    competitions = Competition.query.order_by(Competition.name).all()
    return render_template("index.html", competitions=competitions)


@bp.route("/competition/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        name = request.form["name"].strip()
        program = request.form.get("program", "").strip()
        description = request.form.get("description", "").strip()
        slug = slugify(name)
        existing = Competition.query.filter_by(slug=slug).first()
        if existing:
            slug = slug + "-" + str(Competition.query.count() + 1)
        c = Competition(name=name, slug=slug, program=program or None, description=description or None)
        db.session.add(c)
        db.session.commit()
        flash(f'Konkurs "{name}" został dodany.', "success")
        return redirect(url_for("competitions.detail", slug=slug))
    return render_template("competition/form.html", competition=None)


@bp.route("/competition/<slug>")
def detail(slug):
    competition = Competition.query.filter_by(slug=slug).first_or_404()
    return render_template("competition/detail.html", competition=competition)


@bp.route("/competition/<slug>/edit", methods=["GET", "POST"])
def edit(slug):
    competition = Competition.query.filter_by(slug=slug).first_or_404()
    if request.method == "POST":
        competition.name = request.form["name"].strip()
        competition.program = request.form.get("program", "").strip() or None
        competition.description = request.form.get("description", "").strip() or None
        db.session.commit()
        flash("Konkurs zaktualizowany.", "success")
        return redirect(url_for("competitions.detail", slug=competition.slug))
    return render_template("competition/form.html", competition=competition)


@bp.route("/competition/<slug>/delete", methods=["POST"])
def delete(slug):
    competition = Competition.query.filter_by(slug=slug).first_or_404()
    name = competition.name
    db.session.delete(competition)
    db.session.commit()
    flash(f'Konkurs "{name}" zostal usuniety.', "success")
    return redirect(url_for("competitions.index"))
