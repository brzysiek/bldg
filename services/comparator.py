import json
import time
from datetime import datetime
from services.text_extractor import extract_text

DEFAULT_PROMPT_EXTRACTION = """Jestes ekspertem ds. analizy dokumentow prawnych i regulaminow konkursow grantowych.

Przeanalizuj ponizszy dokument regulaminowy i wyodrebnij jego strukture jako obiekt JSON.
Zwroc WYLACZNIE obiekt JSON (bez zadnego tekstu przed ani po, bez blokow ```json```).

Format odpowiedzi:
{
  "tytul": "Tytul dokumentu lub naglowek",
  "sekcje": {
    "§1": "pelna tresc paragrafu pierwszego...",
    "§1 ust. 1": "tresc ustepu jesli paragrafy maja ustepy...",
    "§2": "pelna tresc paragrafu drugiego...",
    "Rozdzial I": "tresc rozdzialu jesli brak paragrafow..."
  }
}

Zasady:
- Zachowaj oryginalna numeracje (§1, §2, Rozdzial I, Art. 1 itd.)
- Jesli dokument uzywa innej struktury (punkty, litery), uzyj jej jako kluczy
- Kazda wartosc to pelna tresc danej sekcji jako plain text
- Nie pomijaj zadnych sekcji
- Jesli dokument nie ma wyraznej struktury, podziel go na logiczne bloki po ~300 slow i nazwij je "Blok 1", "Blok 2" itd.

--- DOKUMENT ---
{document_text}
--- KONIEC DOKUMENTU ---"""


DEFAULT_PROMPT_COMPARISON = """Jestes wybitnym ekspertem ds. pozyskiwania funduszy unijnych i pisania wnioskow o dotacje (konkursy FENG, SMART, PARP, NCBR).

Twoim zadaniem jest analityczne porownanie dwoch fragmentow regulaminu z dwoch roznych edycji tego samego konkursu.

ZASADY ODPOWIEDZI:
1. Jesli nie ma ZADNYCH zmian merytorycznych miedzy fragmentami — zwroc dokladnie to slowo (nic wiecej): BRAK_ZMIAN
2. Jesli sa zmiany — zwroc WYLACZNIE obiekt JSON (bez tekstu przed/po, bez blokow ```json```):
{
  "sekcja": "numer paragrafu/sekcji ktorej dotyczy zmiana",
  "zapis_stary": "Dokladny cytat zmienionego fragmentu ze starszej edycji",
  "zapis_nowy": "Dokladny cytat zmienionego fragmentu z nowszej edycji",
  "typ_zmiany": "jedna z wartosci: ZAOSTRZENIE | ZLAGO DZENIE | NOWA_WYMAGANIE | USUNIETE_WYMAGANIE | ZMIANA_TERMINU | ZMIANA_KWOTY | ZMIANA_REDAKCYJNA | INNE",
  "waga": "jedna z wartosci: KRYTYCZNA | WYSOKA | SREDNIA | NISKA",
  "komentarz_biznesowy": "Ekspercka ocena: jakie ma to znaczenie dla firmy skladajacej wniosek? Jakie ryzyka lub szanse stwarza? Co wnioskodawca powinien zrobic inaczej?"
}

Porownaj ponizsze fragmenty:

Edycja starsza ({label_old}) — sekcja {sekcja}:
{tresc_stara}

Edycja nowsza ({label_new}) — sekcja {sekcja}:
{tresc_nowa}"""


DEFAULT_PROMPT_SUMMARY = """Jestes glownym analitykiem projektow unijnych i doradca strategicznym dla firm wnioskujacych o dofinansowanie.

Ponizej znajduje sie wygenerowany rejestr zmian miedzy edycja {label_old} a edycja {label_new} konkursu: {competition_name}.

Lista zidentyfikowanych zmian:
{changes_list}

Na podstawie tego zestawienia napisz syntetyczne Executive Summary (Podsumowanie Kluczowych Zmian) w jezyku polskim, skladajace sie z dwoch czesci:

**CZESC 1 — DLA ZARZADU (max 150 slow)**
Napisz w jezyku biznesowym. Skup sie wylacznie na konsekwencjach finansowych, terminowych i ryzyku dla organizacji. Nie uzywaj zargonu technicznego.

**CZESC 2 — DLA ZESPOLU WNIOSKOW (szczegolowa)**
Podziel na trzy sekcje:
ZAGROZENIA I ZAOSTRZENIA — co stalo sie trudniejsze, krotsze terminy, surowsze kryteria, nowe obowiazki
SZANSE I ULATWIENIA — co stalo sie korzystniejsze, wyzsze limity, zlagodzone wymagania
ZMIANY OPERACYJNE — zmiany w harmonogramie, procedurach, dokumentacji ktorych nie mozna przeoczye

Formatuj odpowiedz w Markdown. Uzywaj profesjonalnego, doradczego tonu."""


DEFAULT_PROMPT_EDITION_SUMMARY = """Jestes glownym analitykiem projektow unijnych.

Przeprowadzono porownanie edycji {label_old} z edycja {label_new} konkursu "{competition_name}".
Przeanalizowano {n_files} par dokumentow. Ponizej podsumowania zmian per dokument:

{per_file_summaries}

Na podstawie powyzszych danych napisz syntetyczne PODSUMOWANIE CALEJ EDYCJI w jezyku polskim:

1. **Ogolna ocena skali zmian** — czy to rewolucja, ewolucja czy jedynie kosmetyka?
2. **Najwazniejsze zmiany** (max 5 punktow) — z perspektywy wnioskodawcy
3. **Pliki z najwazniejszymi zmianami** — ktore dokumenty wymagaja najuwazniejszej lektury?
4. **Rekomendacja dla zespolu** — co nalezy zrobic przed zlozeniem wniosku?

Formatuj w Markdown. Badz precyzyjny i praktyczny."""


PRICING = {
    "gemini-2.5-flash":      {"input": 0.30,  "output": 2.50},
    "gemini-2.5-pro":        {"input": 1.25,  "output": 10.00},
    "gemini-2.5-flash-lite": {"input": 0.10,  "output": 0.40},
}


def _resolve_local_path(doc, settings):
    """Return (local_path, tmp_dir_or_None). Caller must shutil.rmtree(tmp_dir) if set."""
    if not doc.gdrive_file_id:
        return doc.stored_path, None
    import tempfile, shutil
    tmp_dir = tempfile.mkdtemp()
    try:
        if settings and settings.google_access_token:
            from services.google_drive import download_file
            path = download_file(
                doc.gdrive_file_id, doc.original_name,
                doc.mime_type or "application/pdf", tmp_dir, settings,
            )
        elif settings and settings.google_drive_api_key:
            from services.google_drive import download_file_public
            path = download_file_public(
                doc.gdrive_file_id, doc.original_name,
                doc.mime_type or "application/pdf", tmp_dir, settings.google_drive_api_key,
            )
        else:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise ValueError("Brak konfiguracji Drive — nie można pobrać pliku")
        return path, tmp_dir
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def compare_one_pair(doc_old, doc_new, job, settings):
    """Compare one document pair in the current (main-request) thread.
    Returns result dict: old/new names, changes list, summary text, token counts."""
    import shutil
    from google import genai

    client    = genai.Client(api_key=settings.gemini_api_key)
    model     = settings.gemini_model or "gemini-2.5-flash"
    t_in = t_out = 0

    def call_gemini(prompt):
        nonlocal t_in, t_out
        resp = client.models.generate_content(model=model, contents=prompt)
        u = getattr(resp, "usage_metadata", None)
        if u:
            t_in  += getattr(u, "prompt_token_count",     0) or 0
            t_out += getattr(u, "candidates_token_count", 0) or 0
        return resp

    old_path, old_tmp = _resolve_local_path(doc_old, settings)
    new_path, new_tmp = _resolve_local_path(doc_new, settings)
    try:
        text_old = extract_text(old_path, doc_old.mime_type or "")
        text_new = extract_text(new_path, doc_new.mime_type or "")
    finally:
        if old_tmp: shutil.rmtree(old_tmp, ignore_errors=True)
        if new_tmp: shutil.rmtree(new_tmp, ignore_errors=True)

    changes = _compare_pair(
        text_old, text_new,
        job.label_old or "Edycja starsza",
        job.label_new or "Edycja nowsza",
        call_gemini, lambda _: None, settings,
    )
    summary = _file_summary(
        changes,
        job.label_old or "Edycja starsza",
        job.label_new or "Edycja nowsza",
        job.competition_name or "konkurs",
        call_gemini, settings,
    )
    return {
        "old_doc_id": doc_old.id,
        "new_doc_id": doc_new.id,
        "old_name":   doc_old.original_name,
        "new_name":   doc_new.original_name,
        "changes":    changes,
        "summary":    summary,
        "tokens_in":  t_in,
        "tokens_out": t_out,
    }


def generate_edition_summary_text(per_file_results, job, settings):
    """Generate edition-wide executive summary from completed per-file results.
    Returns (summary_text, tokens_in, tokens_out)."""
    from google import genai

    client = genai.Client(api_key=settings.gemini_api_key)
    model  = settings.gemini_model or "gemini-2.5-flash"
    t_in = t_out = 0

    def call_gemini(prompt):
        nonlocal t_in, t_out
        resp = client.models.generate_content(model=model, contents=prompt)
        u = getattr(resp, "usage_metadata", None)
        if u:
            t_in  += getattr(u, "prompt_token_count",     0) or 0
            t_out += getattr(u, "candidates_token_count", 0) or 0
        return resp

    per_file_summaries = "\n\n".join(
        f"### {r['old_name']} vs {r['new_name']}\n{r.get('summary','')[:1000]}"
        for r in per_file_results
    )
    prompt = (
        DEFAULT_PROMPT_EDITION_SUMMARY
        .replace("{label_old}",          job.label_old or "Edycja starsza")
        .replace("{label_new}",          job.label_new or "Edycja nowsza")
        .replace("{competition_name}",   job.competition_name or "konkurs")
        .replace("{n_files}",            str(len(per_file_results)))
        .replace("{per_file_summaries}", per_file_summaries[:30_000])
    )
    return call_gemini(prompt).text, t_in, t_out


def _extract_structure(text, label, call_gemini, settings):
    prompt = (settings.comparison_prompt_extraction or DEFAULT_PROMPT_EXTRACTION).replace(
        "{document_text}", text[:300_000]
    )
    resp = call_gemini(prompt)
    raw = resp.text.strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        words = text.split()
        blocks = {f"Blok {i // 500 + 1}": " ".join(words[i:i + 500]) for i in range(0, len(words), 500)}
        return {"tytul": label, "sekcje": blocks}


def _compare_pair(text_old, text_new, label_old, label_new, call_gemini, on_progress, settings):
    struct_old = _extract_structure(text_old, label_old, call_gemini, settings)
    struct_new = _extract_structure(text_new, label_new, call_gemini, settings)

    sekcje_old = struct_old.get("sekcje", {})
    sekcje_new = struct_new.get("sekcje", {})
    all_keys = sorted(set(list(sekcje_old.keys()) + list(sekcje_new.keys())))

    changes = []
    found = 0

    for idx, sekcja in enumerate(all_keys, 1):
        tresc_stara = sekcje_old.get(sekcja, "[SEKCJA NIEOBECNA W TEJ EDYCJI]")
        tresc_nowa  = sekcje_new.get(sekcja, "[SEKCJA NIEOBECNA W TEJ EDYCJI]")

        on_progress(f"Sekcja {idx}/{len(all_keys)}: {sekcja}" + (f" — {found} zmian" if found else ""))

        if tresc_stara == tresc_nowa:
            continue

        prompt = (
            (settings.comparison_prompt_comparison or DEFAULT_PROMPT_COMPARISON)
            .replace("{label_old}", label_old)
            .replace("{label_new}", label_new)
            .replace("{sekcja}", sekcja)
            .replace("{tresc_stara}", tresc_stara[:4000])
            .replace("{tresc_nowa}", tresc_nowa[:4000])
        )
        try:
            resp = call_gemini(prompt)
            raw = resp.text.strip()
            if raw != "BRAK_ZMIAN":
                raw = raw.replace("```json", "").replace("```", "").strip()
                obj = json.loads(raw)
                obj["sekcja"] = sekcja
                changes.append(obj)
                found += 1
        except Exception as e:
            changes.append({
                "sekcja": sekcja,
                "zapis_stary": tresc_stara[:500],
                "zapis_nowy": tresc_nowa[:500],
                "typ_zmiany": "INNE",
                "waga": "NISKA",
                "komentarz_biznesowy": f"[Blad analizy: {str(e)[:200]}]",
            })
            found += 1

        time.sleep(0.5)

    return changes


def _file_summary(changes, label_old, label_new, competition_name, call_gemini, settings):
    if not changes:
        return "Brak istotnych roznic w tym dokumencie."
    changes_text = "\n".join(
        f"- [{c.get('waga','?')}] {c.get('sekcja','?')} ({c.get('typ_zmiany','?')}): {c.get('komentarz_biznesowy','')[:300]}"
        for c in changes
    )
    prompt = (
        (settings.comparison_prompt_summary or DEFAULT_PROMPT_SUMMARY)
        .replace("{label_old}", label_old)
        .replace("{label_new}", label_new)
        .replace("{competition_name}", competition_name)
        .replace("{changes_list}", changes_text[:20_000])
    )
    return call_gemini(prompt).text


def run_comparison(job_id: int, app):
    with app.app_context():
        from models import db, ComparisonJob, AppSettings, Document
        from google import genai

        job = db.session.get(ComparisonJob, job_id)
        settings = db.session.get(AppSettings, 1)

        if not job or not settings or not settings.gemini_api_key:
            if job:
                job.status = "error"
                job.error_message = "Brak konfiguracji Gemini API"
                db.session.commit()
            return

        client = genai.Client(api_key=settings.gemini_api_key)
        model_name = settings.gemini_model or "gemini-2.5-flash"
        price = PRICING.get(model_name, {"input": 0.30, "output": 2.50})

        total_input = 0
        total_output = 0

        def call_gemini(prompt):
            nonlocal total_input, total_output
            resp = client.models.generate_content(model=model_name, contents=prompt)
            usage = getattr(resp, "usage_metadata", None)
            if usage:
                total_input  += getattr(usage, "prompt_token_count", 0) or 0
                total_output += getattr(usage, "candidates_token_count", 0) or 0
            return resp

        def save(detail=None):
            if detail is not None:
                job.status_detail = detail
            db.session.commit()

        def finish(ok=True):
            cost = (total_input / 1_000_000 * price["input"]) + (total_output / 1_000_000 * price["output"])
            job.tokens_input = total_input
            job.tokens_output = total_output
            job.estimated_cost_usd = round(cost, 6)
            job.finished_at = datetime.utcnow()
            job.gemini_model_used = model_name

        try:
            job.started_at = datetime.utcnow()
            mappings = json.loads(job.file_mappings_json)

            job.progress_total = len(mappings)
            job.progress_current = 0
            job.status = "comparing"
            save("Rozpoczynam porownanie plikow edycji...")

            per_file_results = []
            all_changes = []

            for idx, mapping in enumerate(mappings):
                old_doc = db.session.get(Document, mapping["old_doc_id"])
                new_doc = db.session.get(Document, mapping["new_doc_id"])

                if not old_doc or not new_doc:
                    job.progress_current += 1
                    save()
                    continue

                old_name = old_doc.original_name
                new_name = new_doc.original_name
                n = len(mappings)

                save(f"Plik {idx + 1}/{n}: {old_name} — Ekstrakcja tekstu...")
                text_old = extract_text(old_doc.stored_path, old_doc.mime_type or "")
                text_new = extract_text(new_doc.stored_path, new_doc.mime_type or "")

                save(f"Plik {idx + 1}/{n}: {old_name} — Analiza struktury...")

                def on_progress(msg, _idx=idx, _name=old_name, _n=n):
                    save(f"Plik {_idx + 1}/{_n}: {_name} — {msg}")

                changes = _compare_pair(
                    text_old, text_new,
                    job.label_old or "Edycja starsza",
                    job.label_new or "Edycja nowsza",
                    call_gemini, on_progress, settings,
                )

                save(f"Plik {idx + 1}/{n}: {old_name} — Generuje podsumowanie pliku...")
                summary = _file_summary(
                    changes,
                    job.label_old or "Edycja starsza",
                    job.label_new or "Edycja nowsza",
                    job.competition_name or "konkurs",
                    call_gemini, settings,
                )

                per_file_results.append({
                    "idx": idx,
                    "old_doc_id": mapping["old_doc_id"],
                    "new_doc_id": mapping["new_doc_id"],
                    "old_name": old_name,
                    "new_name": new_name,
                    "changes": changes,
                    "summary": summary,
                })
                all_changes.extend(changes)

                job.progress_current = idx + 1
                job.per_file_results_json = json.dumps(per_file_results, ensure_ascii=False)
                job.changes_json = json.dumps(all_changes, ensure_ascii=False)
                db.session.commit()

            # Edition summary
            job.status = "summarizing"
            save("Generuje podsumowanie calej edycji...")

            per_file_summaries = "\n\n".join(
                f"### {r['old_name']} vs {r['new_name']}\n{r['summary'][:1000]}"
                for r in per_file_results
            )
            edition_prompt = (
                DEFAULT_PROMPT_EDITION_SUMMARY
                .replace("{label_old}", job.label_old or "Edycja starsza")
                .replace("{label_new}", job.label_new or "Edycja nowsza")
                .replace("{competition_name}", job.competition_name or "konkurs")
                .replace("{n_files}", str(len(per_file_results)))
                .replace("{per_file_summaries}", per_file_summaries[:30_000])
            )
            job.edition_summary = call_gemini(edition_prompt).text

            finish()
            job.status = "done"
            job.status_detail = f"Analiza zakonczona. Znaleziono {len(all_changes)} zmian w {len(per_file_results)} plikach."
            db.session.commit()

        except Exception as e:
            # Rollback any partial/dirty transaction before writing error state
            try:
                db.session.rollback()
            except Exception:
                pass
            try:
                finish(ok=False)
                job.status = "error"
                job.status_detail = None
                job.error_message = str(e)[:1000]
                db.session.commit()
            except Exception:
                pass  # DB unavailable — job will be caught by timeout monitor
