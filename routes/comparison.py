import io
import json
from datetime import datetime

from flask import (
    Blueprint, jsonify, redirect, render_template,
    request, send_file, url_for, flash,
)
from werkzeug.utils import secure_filename

from extensions import db
from models import AppSettings, ComparisonJob, Competition, Edition, Document
from services.comparator import (
    compare_one_pair,
    generate_edition_summary_text,
    run_comparison,
)

bp = Blueprint("comparison", __name__, url_prefix="/comparison")

PRICING = {
    "gemini-2.5-flash":      {"input": 0.30,  "output": 2.50},
    "gemini-2.5-pro":        {"input": 1.25,  "output": 10.00},
    "gemini-2.5-flash-lite": {"input": 0.10,  "output": 0.40},
}


@bp.route("/")
def index():
    jobs = ComparisonJob.query.order_by(ComparisonJob.created_at.desc()).all()
    return render_template("comparison/index.html", jobs=jobs)


@bp.route("/setup/", methods=["GET", "POST"])
def setup():
    settings = db.session.get(AppSettings, 1)
    if not settings or not settings.gemini_api_key:
        flash("Skonfiguruj klucz Gemini API w Ustawieniach.", "warning")
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
            flash("Wybierz dwie różne edycje.", "error")
            return redirect(url_for("comparison.setup"))

        edition_old  = db.session.get(Edition, edition_old_id)
        edition_new  = db.session.get(Edition, edition_new_id)
        competition  = db.session.get(Competition, competition_id)

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
                        "old_name":   old_doc.original_name,
                        "new_name":   new_doc.original_name,
                    })
            i += 1

        if not mappings:
            flash("Nie wybrano żadnych par plików do porównania.", "error")
            return redirect(url_for("comparison.setup"))

        active_statuses = ["pending", "comparing", "extracting", "chunking", "summarizing"]
        is_busy = ComparisonJob.query.filter(ComparisonJob.status.in_(active_statuses)).first() is not None
        initial_status = "queued" if is_busy else "pending"

        job = ComparisonJob(
            competition_name   = competition.name if competition else "",
            edition_old_id     = edition_old_id,
            edition_new_id     = edition_new_id,
            label_old          = edition_old.name if edition_old else "Edycja starsza",
            label_new          = edition_new.name if edition_new else "Edycja nowsza",
            file_mappings_json = json.dumps(mappings, ensure_ascii=False),
            status             = initial_status,
            progress_total     = len(mappings),
            progress_current   = 0,
            gemini_model_used  = settings.gemini_model,
        )
        db.session.add(job)
        db.session.commit()

        return redirect(url_for("comparison.job_status", job_id=job.id))

    return render_template("comparison/setup.html", competitions=competitions, settings=settings)


# ── AJAX endpoints for browser-driven comparison ───────────────────────────

@bp.route("/job/<int:job_id>/run-pair", methods=["POST"])
def run_pair(job_id):
    """Process one file pair synchronously in the main request thread."""
    job = ComparisonJob.query.get_or_404(job_id)

    # Catch cancellations that arrived while a previous pair was running
    db.session.refresh(job)
    if job.status == "cancelled":
        return jsonify({"ok": False, "cancelled": True, "error": "Porównanie zostało anulowane"})

    settings = db.session.get(AppSettings, 1)

    if not settings or not settings.gemini_api_key:
        return jsonify({"ok": False, "error": "Brak klucza Gemini API w ustawieniach"}), 400

    data = request.get_json() or {}
    pair_idx = data.get("pair_idx", 0)

    mappings = json.loads(job.file_mappings_json or "[]")
    if pair_idx >= len(mappings):
        return jsonify({"ok": False, "error": f"Nieprawidłowy indeks pary: {pair_idx}"}), 400

    mapping = mappings[pair_idx]
    doc_old = db.session.get(Document, mapping["old_doc_id"])
    doc_new = db.session.get(Document, mapping["new_doc_id"])

    if not doc_old or not doc_new:
        return jsonify({"ok": False, "pair_idx": pair_idx, "error": "Dokument nie istnieje"}), 404

    job.status = "comparing"
    job.status_detail = f"Para {pair_idx + 1}/{len(mappings)}: {doc_old.original_name}"
    job.progress_current = pair_idx
    if not job.started_at:
        job.started_at = datetime.utcnow()
    db.session.commit()

    try:
        result = compare_one_pair(doc_old, doc_new, job, settings)

        per_file_results = json.loads(job.per_file_results_json or "[]")
        per_file_results.append(result)
        job.per_file_results_json = json.dumps(per_file_results, ensure_ascii=False)

        all_changes = [c for r in per_file_results for c in r.get("changes", [])]
        job.changes_json     = json.dumps(all_changes, ensure_ascii=False)
        job.progress_current = pair_idx + 1
        job.tokens_input     = (job.tokens_input  or 0) + result.get("tokens_in",  0)
        job.tokens_output    = (job.tokens_output or 0) + result.get("tokens_out", 0)
        _update_cost(job)
        db.session.commit()

        import markdown as md_lib
        summary_html = md_lib.markdown(result.get("summary", ""), extensions=["extra"]) if result.get("summary") else ""

        return jsonify({
            "ok":          True,
            "pair_idx":    pair_idx,
            "old_name":    result["old_name"],
            "new_name":    result["new_name"],
            "changes":     result["changes"],
            "summary":     result.get("summary", ""),
            "summary_html": summary_html,
        })
    except Exception as exc:
        job.status       = "error"
        job.error_message = str(exc)[:1000]
        db.session.commit()
        return jsonify({"ok": False, "pair_idx": pair_idx, "error": str(exc)}), 500


@bp.route("/job/<int:job_id>/generate-summary", methods=["POST"])
def generate_edition_summary(job_id):
    """Generate the edition-wide executive summary from completed per-file results."""
    job = ComparisonJob.query.get_or_404(job_id)
    settings = db.session.get(AppSettings, 1)

    if not settings or not settings.gemini_api_key:
        return jsonify({"ok": False, "error": "Brak klucza Gemini API w ustawieniach"}), 400

    per_file_results = json.loads(job.per_file_results_json or "[]")
    if not per_file_results:
        return jsonify({"ok": False, "error": "Brak wyników do podsumowania"}), 400

    job.status       = "summarizing"
    job.status_detail = "Generuję podsumowanie całej edycji..."
    db.session.commit()

    try:
        summary_text, t_in, t_out = generate_edition_summary_text(per_file_results, job, settings)

        job.edition_summary  = summary_text
        job.status           = "done"
        job.status_detail    = f"Analiza zakończona — {sum(len(r.get('changes',[])) for r in per_file_results)} zmian w {len(per_file_results)} parach"
        job.finished_at      = datetime.utcnow()
        job.tokens_input     = (job.tokens_input  or 0) + t_in
        job.tokens_output    = (job.tokens_output or 0) + t_out
        _update_cost(job)
        db.session.commit()

        import markdown as md_lib
        summary_html = md_lib.markdown(summary_text, extensions=["extra"])

        _promote_next_queued()

        return jsonify({"ok": True, "summary_html": summary_html})
    except Exception as exc:
        job.status        = "error"
        job.error_message = str(exc)[:1000]
        db.session.commit()
        _promote_next_queued()
        return jsonify({"ok": False, "error": str(exc)}), 500


def _update_cost(job):
    model = job.gemini_model_used or "gemini-2.5-flash"
    price = PRICING.get(model, {"input": 0.30, "output": 2.50})
    t_in  = job.tokens_input  or 0
    t_out = job.tokens_output or 0
    job.estimated_cost_usd = round(
        (t_in / 1_000_000 * price["input"]) + (t_out / 1_000_000 * price["output"]), 6
    )


def _promote_next_queued():
    """Promote the oldest queued job to pending so it can start."""
    next_job = ComparisonJob.query.filter_by(status="queued").order_by(ComparisonJob.created_at).first()
    if next_job:
        next_job.status = "pending"
        db.session.commit()


# ── Result / status pages ──────────────────────────────────────────────────

@bp.route("/job/<int:job_id>")
def job_status(job_id):
    import markdown as md_lib
    job = ComparisonJob.query.get_or_404(job_id)

    per_file_results = []
    all_changes = []

    if job.per_file_results_json:
        per_file_results = json.loads(job.per_file_results_json)
        for r in per_file_results:
            all_changes.extend(r.get("changes", []))
            if r.get("summary"):
                r["summary_html"] = md_lib.markdown(r["summary"], extensions=["extra"])

    edition_summary_html = ""
    if job.edition_summary:
        edition_summary_html = md_lib.markdown(job.edition_summary, extensions=["extra"])

    mappings = json.loads(job.file_mappings_json or "[]") if job.status in ("pending", "queued") else []

    return render_template(
        "comparison/result.html",
        job=job,
        changes=all_changes,
        per_file_results=per_file_results,
        edition_summary_html=edition_summary_html,
        mappings=mappings,
    )


@bp.route("/job/<int:job_id>/status-api")
def job_status_api(job_id):
    job = ComparisonJob.query.get_or_404(job_id)
    return jsonify({
        "status":           job.status,
        "status_detail":    job.status_detail or "",
        "progress_current": job.progress_current,
        "progress_total":   job.progress_total,
        "error_message":    job.error_message,
    })


# ── Excel export ───────────────────────────────────────────────────────────

@bp.route("/job/<int:job_id>/download-excel")
def download_excel(job_id):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    job = ComparisonJob.query.get_or_404(job_id)
    if job.status != "done":
        flash("Porównanie jeszcze nie gotowe.", "warning")
        return redirect(url_for("comparison.job_status", job_id=job_id))

    per_file_results = json.loads(job.per_file_results_json or "[]")

    wb = openpyxl.Workbook()

    ws_sum = wb.active
    ws_sum.title = "Podsumowanie edycji"
    ws_sum["A1"] = f"Porównanie: {job.competition_name}"
    ws_sum["A1"].font = Font(bold=True, size=14)
    ws_sum["A2"] = f"{job.label_old} vs {job.label_new}"
    ws_sum["A3"] = f"Wygenerowano: {job.created_at.strftime('%Y-%m-%d %H:%M')}" if job.created_at else ""
    ws_sum["A5"] = job.edition_summary or "(brak podsumowania)"
    ws_sum["A5"].alignment = Alignment(wrap_text=True)
    ws_sum.column_dimensions["A"].width = 120
    ws_sum.row_dimensions[5].height = 600

    waga_colors = {
        "KRYTYCZNA": "FFCCCC",
        "WYSOKA":    "FFE5CC",
        "SREDNIA":   "FFFFCC",
        "NISKA":     "E5FFE5",
    }

    for pfr in per_file_results:
        sheet_name = pfr.get("old_name", "Plik")[:28]
        ws = wb.create_sheet(sheet_name)
        _write_changes_sheet(ws, pfr.get("changes", []), waga_colors, job)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = secure_filename(
        f"rejestr_zmian_{job.competition_name or 'konkurs'}_{job.label_old}_vs_{job.label_new}.xlsx"
    )
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _write_changes_sheet(ws, changes, waga_colors, job):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    headers = ["Sekcja", "Typ zmiany", "Waga",
               f"Zapis — {job.label_old}", f"Zapis — {job.label_new}", "Komentarz biznesowy"]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1C4B40")
        cell.alignment = Alignment(wrap_text=True)

    for row, change in enumerate(changes, 2):
        waga = change.get("waga", "NISKA")
        fill = PatternFill("solid", fgColor=waga_colors.get(waga, "FFFFFF"))
        row_data = [
            change.get("sekcja", ""), change.get("typ_zmiany", ""), waga,
            change.get("zapis_stary", ""), change.get("zapis_nowy", ""),
            change.get("komentarz_biznesowy", ""),
        ]
        for col, value in enumerate(row_data, 1):
            cell = ws.cell(row=row, column=col, value=value)
            cell.fill = fill
            cell.alignment = Alignment(wrap_text=True)

    for i, width in enumerate([15, 20, 12, 50, 50, 60], 1):
        ws.column_dimensions[get_column_letter(i)].width = width
    for row in ws.iter_rows(min_row=2):
        ws.row_dimensions[row[0].row].height = 80


@bp.route("/job/<int:job_id>/cancel", methods=["POST"])
def cancel_job(job_id):
    job = ComparisonJob.query.get_or_404(job_id)
    cancellable = {"queued", "pending", "comparing", "extracting", "chunking", "summarizing"}
    if job.status not in cancellable:
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "error": f"Nie można anulować statusu '{job.status}'"}), 400
        flash(f"Nie można anulować porównania o statusie '{job.status}'.", "warning")
        return redirect(url_for("comparison.index"))

    job.status      = "cancelled"
    job.finished_at = datetime.utcnow()
    db.session.commit()

    _promote_next_queued()

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True})
    flash("Porównanie anulowane.", "info")
    return redirect(url_for("comparison.index"))


@bp.route("/job/<int:job_id>/delete", methods=["POST"])
def delete_job(job_id):
    job = ComparisonJob.query.get_or_404(job_id)
    db.session.delete(job)
    db.session.commit()
    flash("Porównanie usunięte.", "success")
    return redirect(url_for("comparison.index"))
