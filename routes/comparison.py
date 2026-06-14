import io
import json
import os
import threading
from datetime import datetime

import markdown as md_lib
from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
    flash,
)
from werkzeug.utils import secure_filename

from extensions import db
from models import AppSettings, ComparisonJob
from services.comparator import run_comparison

bp = Blueprint("comparison", __name__, url_prefix="/comparison")

UPLOAD_TEMP_DIR = "storage/_temp_comparison"
ALLOWED = {"pdf", "docx", "txt", "doc"}


def _allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED


@bp.route("/")
def index():
    jobs = ComparisonJob.query.order_by(ComparisonJob.created_at.desc()).all()
    return render_template("comparison/index.html", jobs=jobs)


@bp.route("/new", methods=["GET", "POST"])
def new_comparison():
    settings = db.session.get(AppSettings, 1)
    if not settings or not settings.gemini_api_key:
        flash("Skonfiguruj klucz Gemini API w Ustawieniach przed uruchomieniem porownania.", "warning")
        return redirect(url_for("settings.index"))

    if request.method == "POST":
        competition_name = request.form.get("competition_name", "").strip()
        label_old = request.form.get("label_old", "Edycja starsza").strip()
        label_new = request.form.get("label_new", "Edycja nowsza").strip()

        file_old = request.files.get("file_old")
        file_new = request.files.get("file_new")

        if not file_old or not file_new or file_old.filename == "" or file_new.filename == "":
            flash("Wgraj oba pliki.", "error")
            return redirect(url_for("comparison.new_comparison"))

        if not _allowed_file(file_old.filename) or not _allowed_file(file_new.filename):
            flash("Dozwolone formaty: PDF, DOCX, TXT, DOC", "error")
            return redirect(url_for("comparison.new_comparison"))

        os.makedirs(UPLOAD_TEMP_DIR, exist_ok=True)
        ts = int(datetime.utcnow().timestamp())

        fn_old = secure_filename(f"{ts}_old_{file_old.filename}")
        fn_new = secure_filename(f"{ts}_new_{file_new.filename}")
        path_old = os.path.join(UPLOAD_TEMP_DIR, fn_old)
        path_new = os.path.join(UPLOAD_TEMP_DIR, fn_new)

        file_old.save(path_old)
        file_new.save(path_new)

        job = ComparisonJob(
            competition_name=competition_name,
            doc_old_name=file_old.filename,
            doc_new_name=file_new.filename,
            label_old=label_old,
            label_new=label_new,
            status="pending",
            changes_json=json.dumps({"path_old": path_old, "path_new": path_new}),
            gemini_model_used=settings.gemini_model,
            prompt_extraction_used=settings.comparison_prompt_extraction,
            prompt_comparison_used=settings.comparison_prompt_comparison,
            prompt_summary_used=settings.comparison_prompt_summary,
        )
        db.session.add(job)
        db.session.commit()

        flask_app = current_app._get_current_object()

        t = threading.Thread(target=run_comparison, args=(job.id, flask_app), daemon=True)
        t.start()

        flash(f"Porownanie uruchomione (ID: {job.id}). Analiza moze potrwac kilka minut.", "success")
        return redirect(url_for("comparison.job_status", job_id=job.id))

    return render_template("comparison/new.html", settings=settings)


@bp.route("/job/<int:job_id>")
def job_status(job_id):
    job = ComparisonJob.query.get_or_404(job_id)

    changes = []
    if job.status == "done" and job.changes_json:
        try:
            raw = json.loads(job.changes_json)
            if isinstance(raw, list):
                changes = raw
        except Exception:
            pass

    summary_html = ""
    if job.executive_summary:
        summary_html = md_lib.markdown(job.executive_summary, extensions=["extra"])

    return render_template(
        "comparison/result.html",
        job=job,
        changes=changes,
        summary_html=summary_html,
    )


@bp.route("/job/<int:job_id>/status-api")
def job_status_api(job_id):
    job = ComparisonJob.query.get_or_404(job_id)
    return jsonify(
        {
            "status": job.status,
            "status_detail": job.status_detail or "",
            "progress_current": job.progress_current,
            "progress_total": job.progress_total,
            "error_message": job.error_message,
        }
    )


@bp.route("/job/<int:job_id>/download-excel")
def download_excel(job_id):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    job = ComparisonJob.query.get_or_404(job_id)
    if job.status != "done":
        flash("Porownanie jeszcze nie gotowe.", "warning")
        return redirect(url_for("comparison.job_status", job_id=job_id))

    changes = []
    try:
        raw = json.loads(job.changes_json or "[]")
        if isinstance(raw, list):
            changes = raw
    except Exception:
        pass

    wb = openpyxl.Workbook()

    # Arkusz 1: Executive Summary
    ws_summary = wb.active
    ws_summary.title = "Executive Summary"
    ws_summary["A1"] = f"Porownanie: {job.competition_name}"
    ws_summary["A1"].font = Font(bold=True, size=14)
    ws_summary["A2"] = f"{job.label_old} vs {job.label_new}"
    ws_summary["A2"].font = Font(size=11)
    ws_summary["A3"] = f"Wygenerowano: {job.created_at.strftime('%Y-%m-%d %H:%M')}"
    ws_summary["A4"] = ""
    ws_summary["A5"] = job.executive_summary or "(brak podsumowania)"
    ws_summary["A5"].alignment = Alignment(wrap_text=True)
    ws_summary.column_dimensions["A"].width = 100
    ws_summary.row_dimensions[5].height = 400

    # Arkusz 2: Rejestr Zmian
    ws = wb.create_sheet("Rejestr Zmian")

    waga_colors = {
        "KRYTYCZNA": "FFCCCC",
        "WYSOKA": "FFE5CC",
        "SREDNIA": "FFFFCC",
        "NISKA": "E5FFE5",
    }

    headers = [
        "Sekcja",
        "Typ zmiany",
        "Waga",
        f"Zapis — {job.label_old}",
        f"Zapis — {job.label_new}",
        "Komentarz biznesowy",
    ]
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1C4B40")
        cell.alignment = Alignment(wrap_text=True)

    for row_idx, change in enumerate(changes, 2):
        waga = change.get("waga", "NISKA")
        fill_color = waga_colors.get(waga, "FFFFFF")
        row_data = [
            change.get("sekcja", ""),
            change.get("typ_zmiany", ""),
            waga,
            change.get("zapis_stary", ""),
            change.get("zapis_nowy", ""),
            change.get("komentarz_biznesowy", ""),
        ]
        for col_idx, value in enumerate(row_data, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.fill = PatternFill("solid", fgColor=fill_color)
            cell.alignment = Alignment(wrap_text=True)

    col_widths = [15, 20, 12, 50, 50, 60]
    for i, width in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width

    for row in ws.iter_rows(min_row=2):
        ws.row_dimensions[row[0].row].height = 80

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    comp = job.competition_name or "konkurs"
    filename = secure_filename(f"rejestr_zmian_{comp}_{job.label_old}_vs_{job.label_new}.xlsx")

    return send_file(
        buf,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.route("/job/<int:job_id>/delete", methods=["POST"])
def delete_job(job_id):
    job = ComparisonJob.query.get_or_404(job_id)
    db.session.delete(job)
    db.session.commit()
    flash("Porownanie usuniete.", "success")
    return redirect(url_for("comparison.index"))
