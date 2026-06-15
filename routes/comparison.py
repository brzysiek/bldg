import io
import json
import os
import threading
from datetime import datetime

import markdown as md_lib
from flask import (
    Blueprint, current_app, jsonify, redirect, render_template,
    request, send_file, url_for, flash,
)
from werkzeug.utils import secure_filename

from extensions import db
from models import AppSettings, ComparisonJob, Competition, Edition, DocumentType, Document
from services.comparator import run_comparison

bp = Blueprint("comparison", __name__, url_prefix="/comparison")


@bp.route("/")
def index():
    jobs = ComparisonJob.query.order_by(ComparisonJob.created_at.desc()).all()
    return render_template("comparison/index.html", jobs=jobs)


@bp.route("/setup/", methods=["GET", "POST"])
def setup():
    settings = db.session.get(AppSettings, 1)
    if not settings or not settings.gemini_api_key:
        flash("Skonfiguruj klucz Gemini API w Ustawieniach przed uruchomieniem porownania.", "warning")
        return redirect(url_for("settings.index"))

    competitions = Competition.query.order_by(Competition.name).all()

    if request.method == "POST":
        competition_id = request.form.get("competition_id", type=int)
        edition_old_id = request.form.get("edition_old_id", type=int)
        edition_new_id = request.form.get("edition_new_id", type=int)

        if not competition_id or not edition_old_id or not edition_new_id:
            flash("Wybierz konkurs i dwie edycje.", "error")
            return redirect(url_for("comparison.setup"))
        if edition_old_id == edition_new_id:
            flash("Wybierz dwie rozne edycje.", "error")
            return redirect(url_for("comparison.setup"))

        edition_old = db.session.get(Edition, edition_old_id)
        edition_new = db.session.get(Edition, edition_new_id)
        competition = db.session.get(Competition, competition_id)

        # Build file mappings from form
        mappings = []
        i = 0
        while True:
            old_id_str = request.form.get(f"mapping_{i}_old")
            if old_id_str is None:
                break
            new_id_str = request.form.get(f"mapping_{i}_new", "")
            if old_id_str and new_id_str and new_id_str != "__skip__":
                old_doc = db.session.get(Document, int(old_id_str))
                new_doc = db.session.get(Document, int(new_id_str))
                if old_doc and new_doc:
                    mappings.append({
                        "old_doc_id": int(old_id_str),
                        "new_doc_id": int(new_id_str),
                        "old_name": old_doc.original_name,
                        "new_name": new_doc.original_name,
                    })
            i += 1

        if not mappings:
            flash("Nie wybrano zadnych par plikow do porownania.", "error")
            return redirect(url_for("comparison.setup"))

        job = ComparisonJob(
            competition_name=competition.name if competition else "",
            edition_old_id=edition_old_id,
            edition_new_id=edition_new_id,
            label_old=edition_old.name if edition_old else "Edycja starsza",
            label_new=edition_new.name if edition_new else "Edycja nowsza",
            file_mappings_json=json.dumps(mappings, ensure_ascii=False),
            status="pending",
            gemini_model_used=settings.gemini_model,
        )
        db.session.add(job)
        db.session.commit()

        flask_app = current_app._get_current_object()
        t = threading.Thread(target=run_comparison, args=(job.id, flask_app), daemon=True)
        t.start()

        flash(f"Porownanie uruchomione (ID: {job.id}). Analiza {len(mappings)} par plikow — moze potrwac kilka minut.", "success")
        return redirect(url_for("comparison.job_status", job_id=job.id))

    return render_template("comparison/setup.html", competitions=competitions, settings=settings)


@bp.route("/job/<int:job_id>")
def job_status(job_id):
    job = ComparisonJob.query.get_or_404(job_id)

    changes = []
    per_file_results = []

    if job.status == "done":
        if job.per_file_results_json:
            try:
                per_file_results = json.loads(job.per_file_results_json)
                for r in per_file_results:
                    changes.extend(r.get("changes", []))
            except Exception:
                pass
        elif job.changes_json:
            try:
                raw = json.loads(job.changes_json)
                if isinstance(raw, list):
                    changes = raw
            except Exception:
                pass

    # Render markdown summaries
    edition_summary_html = ""
    if job.edition_summary:
        edition_summary_html = md_lib.markdown(job.edition_summary, extensions=["extra"])

    summary_html = ""
    if job.executive_summary and not job.edition_summary:
        summary_html = md_lib.markdown(job.executive_summary, extensions=["extra"])

    for r in per_file_results:
        if r.get("summary"):
            r["summary_html"] = md_lib.markdown(r["summary"], extensions=["extra"])

    return render_template(
        "comparison/result.html",
        job=job,
        changes=changes,
        per_file_results=per_file_results,
        edition_summary_html=edition_summary_html,
        summary_html=summary_html,
    )


@bp.route("/job/<int:job_id>/status-api")
def job_status_api(job_id):
    job = ComparisonJob.query.get_or_404(job_id)
    return jsonify({
        "status": job.status,
        "status_detail": job.status_detail or "",
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
        "error_message": job.error_message,
    })


@bp.route("/job/<int:job_id>/download-excel")
def download_excel(job_id):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    job = ComparisonJob.query.get_or_404(job_id)
    if job.status != "done":
        flash("Porownanie jeszcze nie gotowe.", "warning")
        return redirect(url_for("comparison.job_status", job_id=job_id))

    # Collect all changes
    all_changes = []
    per_file_results = []
    if job.per_file_results_json:
        try:
            per_file_results = json.loads(job.per_file_results_json)
        except Exception:
            pass
    elif job.changes_json:
        try:
            raw = json.loads(job.changes_json)
            if isinstance(raw, list):
                all_changes = raw
        except Exception:
            pass

    wb = openpyxl.Workbook()

    # Sheet 1: Edition summary
    ws_summary = wb.active
    ws_summary.title = "Podsumowanie edycji"
    ws_summary["A1"] = f"Porownanie edycji: {job.competition_name}"
    ws_summary["A1"].font = Font(bold=True, size=14)
    ws_summary["A2"] = f"{job.label_old} vs {job.label_new}"
    ws_summary["A3"] = f"Wygenerowano: {job.created_at.strftime('%Y-%m-%d %H:%M')}"
    ws_summary["A4"] = ""
    ws_summary["A5"] = job.edition_summary or job.executive_summary or "(brak podsumowania)"
    ws_summary["A5"].alignment = Alignment(wrap_text=True)
    ws_summary.column_dimensions["A"].width = 120
    ws_summary.row_dimensions[5].height = 600

    waga_colors = {
        "KRYTYCZNA": "FFCCCC",
        "WYSOKA":    "FFE5CC",
        "SREDNIA":   "FFFFCC",
        "NISKA":     "E5FFE5",
    }
    headers = ["Dokument", "Sekcja", "Typ zmiany", "Waga",
               f"Zapis — {job.label_old}", f"Zapis — {job.label_new}", "Komentarz biznesowy"]

    if per_file_results:
        for pfr in per_file_results:
            ws = wb.create_sheet(pfr["old_name"][:28])
            _write_changes_sheet(ws, pfr.get("changes", []), headers[1:], waga_colors, job)
    else:
        ws = wb.create_sheet("Rejestr Zmian")
        _write_changes_sheet(ws, all_changes, headers[1:], waga_colors, job)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    comp = job.competition_name or "konkurs"
    filename = secure_filename(f"rejestr_zmian_{comp}_{job.label_old}_vs_{job.label_new}.xlsx")
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _write_changes_sheet(ws, changes, headers, waga_colors, job):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

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


@bp.route("/job/<int:job_id>/delete", methods=["POST"])
def delete_job(job_id):
    job = ComparisonJob.query.get_or_404(job_id)
    db.session.delete(job)
    db.session.commit()
    flash("Porownanie usuniete.", "success")
    return redirect(url_for("comparison.index"))
