import hashlib
import json
import logging
import os
import time
from datetime import datetime
from services.text_extractor import extract_text

log = logging.getLogger(__name__)

# Opóźnienie między wywołaniami Gemini API (w sekundach), konfigurowalne przez .env
_GEMINI_SLEEP = float(os.environ.get("GEMINI_SLEEP_SECONDS", "1.0"))

# Liczba sekcji przetwarzanych w jednym żądaniu HTTP, konfigurowalne przez .env
BATCH_SIZE = int(os.environ.get("GEMINI_BATCH_SECTIONS", "20"))

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
  "lokalizacja": "precyzyjna lokalizacja zmiany w dokumencie, np. 'Rozdzial II, §5, ust. 3 pkt 2' lub 'Art. 12 pkt 4 lit. b' — uzywaj dokladnej numeracji z dokumentu; jezeli zmiana dotyczy calej sekcji wpisz tylko numer sekcji",
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


DEFAULT_PROMPT_MATCHING = """Jestes ekspertem od analizy dokumentow regulaminowych konkursow grantowych.

Ponizej znajduja sie listy sekcji z dwoch edycji tego samego dokumentu.
Dopasuj semantycznie odpowiadajace sobie sekcje — takie, ktore opisuja ten sam temat, nawet jesli maja inny numer lub zmieniona nazwe miedzy edycjami.

Edycja starsza ({label_old}):
{sekcje_stare}

Edycja nowsza ({label_new}):
{sekcje_nowe}

Zwroc WYLACZNIE obiekt JSON (bez tekstu przed/po, bez blokow ```json```):
{{
  "matched": [
    {{"old": "§1", "new": "§1"}},
    {{"old": "§2", "new": "§3"}}
  ],
  "only_in_old": ["§X"],
  "only_in_new": ["§2"]
}}

Zasady:
- "matched": para sekcji opisujacych ten sam temat w obu edycjach (niezaleznie od numeru)
- "only_in_old": sekcja istnieje TYLKO w starszej edycji (calkowicie usunieta w nowej)
- "only_in_new": sekcja istnieje TYLKO w nowszej edycji (zupelnie nowa, bez odpowiednika w starszej)
- Kazda sekcja ze starszej edycji musi wystapic dokladnie raz — albo w matched.old, albo w only_in_old
- Kazda sekcja z nowszej edycji musi wystapic dokladnie raz — albo w matched.new, albo w only_in_new
- Jesli tresc sekcji jest identyczna lub bardzo podobna mimo innego numeru — dopasuj je
- Jesli kolejnosc sekcji sie zmienila, ale tresc jest podobna — dopasuj je"""


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


def _stage_config(settings, stage: str):
    """Return (model_name, GenerateContentConfig|None) for a pipeline stage.

    Each stage can override model, temperature, max_output_tokens, and
    system_instruction independently. Any field left blank falls back to the
    global gemini_model with no extra config.
    """
    from google.genai import types

    prefix = f"cmp_{stage}_"
    model = (getattr(settings, f"{prefix}model", None) or "").strip() \
            or (settings.gemini_model or "").strip() \
            or "gemini-2.5-flash"

    temp       = getattr(settings, f"{prefix}temperature", None)
    max_tokens = getattr(settings, f"{prefix}max_tokens", None)
    system     = (getattr(settings, f"{prefix}system", None) or "").strip()

    config_kwargs = {}
    if temp is not None:
        config_kwargs["temperature"] = float(temp)
    if max_tokens:
        config_kwargs["max_output_tokens"] = int(max_tokens)
    if system:
        config_kwargs["system_instruction"] = system

    config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None
    return model, config


def _make_stage_caller(client, settings, stage: str, tokens: dict):
    """Return a call_gemini(prompt) closure for a specific pipeline stage.

    The closure tracks cumulative input/output tokens in the provided dict
    and emits verbose DEBUG logs covering every request/response detail.
    """
    model, config = _stage_config(settings, stage)

    temp_val   = getattr(settings, f"cmp_{stage}_temperature", None)
    max_val    = getattr(settings, f"cmp_{stage}_max_tokens", None)
    system_val = (getattr(settings, f"cmp_{stage}_system", None) or "").strip()

    log.debug(
        "Stage config  stage=%s  model=%s  temperature=%s  max_output_tokens=%s  "
        "system_instruction=%s znaków",
        stage, model,
        temp_val if temp_val is not None else "API default",
        max_val  if max_val  is not None else "API default",
        len(system_val) if system_val else 0,
    )

    def caller(prompt):
        log.debug(
            "Gemini REQUEST  stage=%s  model=%s  prompt=%d znaków  "
            "temperature=%s  max_output_tokens=%s  system=%d znaków",
            stage, model, len(prompt),
            temp_val if temp_val is not None else "default",
            max_val  if max_val  is not None else "default",
            len(system_val) if system_val else 0,
        )
        kwargs = dict(model=model, contents=prompt)
        if config:
            kwargs["config"] = config

        import time as _t
        t0 = _t.monotonic()
        resp = client.models.generate_content(**kwargs)
        elapsed = _t.monotonic() - t0

        t_in = t_out = 0
        u = getattr(resp, "usage_metadata", None)
        if u:
            t_in  = getattr(u, "prompt_token_count",     0) or 0
            t_out = getattr(u, "candidates_token_count", 0) or 0
            tokens["in"]  += t_in
            tokens["out"] += t_out

        resp_text = getattr(resp, "text", "") or ""
        log.debug(
            "Gemini RESPONSE  stage=%s  elapsed=%.2fs  "
            "tok_in=%d  tok_out=%d  (total: in=%d out=%d)  "
            "response_len=%d znaków  preview=%.120r",
            stage, elapsed, t_in, t_out,
            tokens["in"], tokens["out"],
            len(resp_text), resp_text[:120],
        )

        if _GEMINI_SLEEP > 0:
            log.debug("Gemini SLEEP  stage=%s  sleep=%.1fs", stage, _GEMINI_SLEEP)
            time.sleep(_GEMINI_SLEEP)

        return resp

    return caller


PRICING = {
    "gemini-2.5-flash":      {"input": 0.30,  "output": 2.50},
    "gemini-2.5-pro":        {"input": 1.25,  "output": 10.00},
    "gemini-2.5-flash-lite": {"input": 0.10,  "output": 0.40},
}


def _resolve_local_path(doc, settings):
    """Return (local_path, tmp_dir_or_None). Caller must shutil.rmtree(tmp_dir) if set."""
    if not doc.gdrive_file_id:
        log.debug("_resolve_local_path  plik lokalny: %s", doc.original_name)
        return doc.stored_path, None
    import tempfile, shutil
    tmp_dir = tempfile.mkdtemp()
    try:
        if settings and settings.google_drive_api_key:
            from services.google_drive import download_file
            log.info(
                "_resolve_local_path  pobieranie z Drive: '%s'  id=%s",
                doc.original_name, doc.gdrive_file_id,
            )
            path = download_file(
                doc.gdrive_file_id, doc.original_name,
                doc.mime_type or "application/pdf", tmp_dir, settings.google_drive_api_key,
            )
            log.info("_resolve_local_path  pobrano OK: '%s'", doc.original_name)
        else:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise ValueError(
                f"Brak klucza Drive API — nie mozna pobrac pliku '{doc.original_name}'"
            )
        return path, tmp_dir
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def compare_one_pair(doc_old, doc_new, job, settings, on_status=None):
    """Compare one document pair in the current (main-request) thread.
    Returns result dict: old/new names, changes list, summary text, token counts."""
    from google import genai

    def _status(msg):
        if on_status:
            try:
                on_status(msg)
            except Exception:
                pass

    log.debug(
        "compare_one_pair START  job=%d  stary=%s  nowy=%s  "
        "default_model=%s",
        job.id, doc_old.original_name, doc_new.original_name,
        settings.gemini_model or "gemini-2.5-flash",
    )

    client = genai.Client(api_key=settings.gemini_api_key)
    tokens = {"in": 0, "out": 0}
    call_extraction = _make_stage_caller(client, settings, "extraction", tokens)
    call_comparison = _make_stage_caller(client, settings, "comparison", tokens)

    _status(f"Analiza struktury: {doc_old.original_name}")
    struct_old, text_old = _structure_for_doc(doc_old, settings, call_extraction)
    log.debug("compare_one_pair  struktura stara: %d sekcji", len(struct_old.get("sekcje", {})))

    _status(f"Analiza struktury: {doc_new.original_name}")
    struct_new, text_new = _structure_for_doc(doc_new, settings, call_extraction)
    log.debug("compare_one_pair  struktura nowa: %d sekcji", len(struct_new.get("sekcje", {})))

    n_sekcji = len(set(struct_old.get("sekcje", {}).keys()) | set(struct_new.get("sekcje", {}).keys()))
    _status(f"Porównywanie sekcji (0/{n_sekcji})")
    log.debug("compare_one_pair  porównuję %d sekcji", n_sekcji)
    call_summary = _make_stage_caller(client, settings, "summary", tokens)
    changes = _compare_pair(
        text_old, text_new,
        job.label_old or "Edycja starsza",
        job.label_new or "Edycja nowsza",
        call_comparison, _status, settings,
        struct_old=struct_old, struct_new=struct_new,
        call_extraction=call_extraction,
    )
    log.debug("compare_one_pair  porównanie zakończone: %d zmian", len(changes))

    _status("Podsumowanie pliku")
    log.debug("compare_one_pair  generuję podsumowanie pliku")
    summary = _file_summary(
        changes,
        job.label_old or "Edycja starsza",
        job.label_new or "Edycja nowsza",
        job.competition_name or "konkurs",
        call_summary, settings,
    )
    log.debug(
        "compare_one_pair KONIEC  job=%d  stary=%s  zmian=%d  tok_in=%d  tok_out=%d",
        job.id, doc_old.original_name, len(changes), tokens["in"], tokens["out"],
    )
    return {
        "old_doc_id": doc_old.id,
        "new_doc_id": doc_new.id,
        "old_name":   doc_old.original_name,
        "new_name":   doc_new.original_name,
        "changes":    changes,
        "summary":    summary,
        "tokens_in":  tokens["in"],
        "tokens_out": tokens["out"],
    }


def generate_edition_summary_text(per_file_results, job, settings):
    """Generate edition-wide executive summary from completed per-file results.
    Returns (summary_text, tokens_in, tokens_out)."""
    from google import genai

    client = genai.Client(api_key=settings.gemini_api_key)
    tokens = {"in": 0, "out": 0}
    call_edition = _make_stage_caller(client, settings, "edition", tokens)

    per_file_summaries = "\n\n".join(
        f"### {r['old_name']} vs {r['new_name']}\n{r.get('summary','')[:1000]}"
        for r in per_file_results
    )
    prompt = (
        (settings.comparison_prompt_edition or DEFAULT_PROMPT_EDITION_SUMMARY)
        .replace("{label_old}",          job.label_old or "Edycja starsza")
        .replace("{label_new}",          job.label_new or "Edycja nowsza")
        .replace("{competition_name}",   job.competition_name or "konkurs")
        .replace("{n_files}",            str(len(per_file_results)))
        .replace("{per_file_summaries}", per_file_summaries[:30_000])
    )
    return call_edition(prompt).text, tokens["in"], tokens["out"]


def _extract_structure(text, label, call_gemini, settings):
    log.debug("_extract_structure  label=%s  text=%d znaków", label, len(text))
    prompt = (settings.comparison_prompt_extraction or DEFAULT_PROMPT_EXTRACTION).replace(
        "{document_text}", text[:300_000]
    )
    resp = call_gemini(prompt)
    raw = resp.text.strip().replace("```json", "").replace("```", "").strip()
    try:
        result = json.loads(raw)
        log.debug("_extract_structure  OK  label=%s  sekcji=%d", label, len(result.get("sekcje", {})))
        return result
    except json.JSONDecodeError:
        log.warning("_extract_structure  błąd parsowania JSON dla %s — używam fallback bloków słownych", label)
        words = text.split()
        blocks = {f"Blok {i // 500 + 1}": " ".join(words[i:i + 500]) for i in range(0, len(words), 500)}
        return {"tytul": label, "sekcje": blocks}


def _cache_key(text, prompt):
    return hashlib.md5((text[:300_000] + prompt).encode("utf-8", errors="ignore")).hexdigest()


def _prompt_hash(prompt: str) -> str:
    return hashlib.md5(prompt.encode("utf-8", errors="ignore")).hexdigest()


def _get_structure_cached(doc, text, call_gemini, settings):
    """Return extracted document structure, using DB cache when prompt+content unchanged."""
    from extensions import db

    prompt = settings.comparison_prompt_extraction or DEFAULT_PROMPT_EXTRACTION
    key = _cache_key(text, prompt)

    if doc.extraction_cache_key == key and doc.extraction_cache_json:
        try:
            structure = json.loads(doc.extraction_cache_json)
            n_sekcji = len(structure.get("sekcje", {}))
            log.info("Ekstrakcja (cache HIT): %s — %d sekcji, pominięto wywołanie Gemini", doc.original_name, n_sekcji)
            return structure
        except Exception:
            pass  # uszkodzony cache — przelicz

    log.info("Ekstrakcja (cache MISS): %s — wywołuję Gemini", doc.original_name)
    structure = _extract_structure(text, doc.original_name, call_gemini, settings)

    try:
        doc.extraction_cache_key   = key
        doc.extraction_cache_json  = json.dumps(structure, ensure_ascii=False)
        doc.extraction_prompt_hash = _prompt_hash(prompt)
        doc.extraction_status      = "done"
        db.session.commit()
        log.info("Ekstrakcja zapisana do cache: %s", doc.original_name)
    except Exception as e:
        log.warning("Zapis cache ekstrakcji nieudany (%s): %s", doc.original_name, e)
        # MySQL może zamknąć połączenie podczas długiego wywołania Gemini (wait_timeout).
        # Rollback przywraca sesję do czystego stanu — bez tego wszystkie kolejne
        # db.session.commit() w route handlerze failują z PendingRollbackError.
        try:
            db.session.rollback()
        except Exception:
            pass

    return structure


def _structure_for_doc(doc, settings, call_gemini):
    """Return (structure, text) for a document.

    Uses pre-computed extraction cache when available, avoiding Drive download entirely.
    Falls back to download → extract → cache (existing behaviour) on cache miss.
    """
    import shutil

    prompt = settings.comparison_prompt_extraction or DEFAULT_PROMPT_EXTRACTION
    ph = _prompt_hash(prompt)

    if (doc.extraction_status == "done"
            and doc.extraction_prompt_hash == ph
            and doc.extraction_cache_json):
        try:
            structure = json.loads(doc.extraction_cache_json)
            n = len(structure.get("sekcje", {}))
            log.info(
                "Ekstrakcja (pre-computed HIT): %s — %d sekcji, pobieranie z Drive pominięte",
                doc.original_name, n,
            )
            return structure, ""
        except Exception:
            log.warning("Uszkodzony pre-computed cache dla %s — przeliczam", doc.original_name)

    local_path, tmp_dir = _resolve_local_path(doc, settings)
    try:
        text = extract_text(local_path, doc.mime_type or "")
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    structure = _get_structure_cached(doc, text, call_gemini, settings)
    return structure, text


def extract_document(doc_id: int) -> tuple:
    """Eagerly extract and cache document structure for a single file.

    Designed to run in a background thread. Returns (ok, error_msg).
    Requires an active Flask app context.
    """
    import shutil
    from extensions import db
    from models import Document, AppSettings
    from google import genai

    doc = db.session.get(Document, doc_id)
    if not doc:
        return False, "Dokument nie istnieje"

    settings = db.session.get(AppSettings, 1)
    if not settings or not settings.gemini_api_key:
        _write_extract_error(doc_id, "Brak klucza Gemini API")
        return False, "Brak klucza Gemini API"

    prompt = settings.comparison_prompt_extraction or DEFAULT_PROMPT_EXTRACTION
    ph = _prompt_hash(prompt)

    if doc.extraction_status == "done" and doc.extraction_prompt_hash == ph:
        return True, None

    if doc.gdrive_file_id and not settings.google_drive_api_key:
        _write_extract_error(doc_id, "Brak klucza Drive API")
        return False, "Brak klucza Drive API"

    try:
        doc.extraction_status = "pending"
        doc.extraction_error  = None
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass

    client = genai.Client(api_key=settings.gemini_api_key)
    tokens = {"in": 0, "out": 0}
    call_extraction = _make_stage_caller(client, settings, "extraction", tokens)

    try:
        local_path, tmp_dir = _resolve_local_path(doc, settings)
        try:
            text = extract_text(local_path, doc.mime_type or "")
        finally:
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        structure = _extract_structure(text, doc.original_name, call_extraction, settings)

        try:
            db.session.rollback()
        except Exception:
            pass
        db.session.expire_all()

        fresh = db.session.get(Document, doc_id)
        if not fresh:
            return False, "Dokument zniknął z bazy po ekstrakcji"
        fresh.extraction_cache_key   = _cache_key(text, prompt)
        fresh.extraction_cache_json  = json.dumps(structure, ensure_ascii=False)
        fresh.extraction_prompt_hash = ph
        fresh.extraction_status      = "done"
        fresh.extraction_error       = None
        db.session.commit()
        n = len(structure.get("sekcje", {}))
        log.info("extract_document OK: %s (%d sekcji)", doc.original_name, n)
        return True, None

    except Exception as exc:
        log.error("extract_document BŁĄD %s: %s", doc.original_name, exc, exc_info=True)
        _write_extract_error(doc_id, str(exc)[:500])
        return False, str(exc)


def _write_extract_error(doc_id: int, msg: str):
    from extensions import db
    from models import Document
    try:
        db.session.rollback()
    except Exception:
        pass
    db.session.expire_all()
    try:
        d = db.session.get(Document, doc_id)
        if d:
            d.extraction_status = "error"
            d.extraction_error  = msg
            db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def match_sections_ai(sekcje_old, sekcje_new, label_old, label_new, call_gemini, settings=None):
    """Use Gemini to semantically match sections between two document editions.

    Returns {"matched": [{"old": k, "new": k}], "only_in_old": [...], "only_in_new": [...]}.
    Falls back to key-based matching if the AI call fails.
    """
    keys_old = list(sekcje_old.keys())
    keys_new = list(sekcje_new.keys())

    previews_old = "\n".join(
        f'  "{k}": "{str(v)[:250].replace(chr(10), " ").strip()}"'
        for k, v in sekcje_old.items()
    )
    previews_new = "\n".join(
        f'  "{k}": "{str(v)[:250].replace(chr(10), " ").strip()}"'
        for k, v in sekcje_new.items()
    )

    prompt = (
        DEFAULT_PROMPT_MATCHING
        .replace("{label_old}", label_old)
        .replace("{label_new}", label_new)
        .replace("{sekcje_stare}", previews_old[:50_000])
        .replace("{sekcje_nowe}", previews_new[:50_000])
    )

    try:
        resp = call_gemini(prompt)
        raw = resp.text.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)

        matched  = result.get("matched",     [])
        only_old = result.get("only_in_old", [])
        only_new = result.get("only_in_new", [])

        # Safety: ensure every key appears exactly once
        covered_old = {m["old"] for m in matched} | set(only_old)
        covered_new = {m["new"] for m in matched} | set(only_new)
        for k in keys_old:
            if k not in covered_old:
                only_old.append(k)
                log.warning("match_sections_ai: sekcja '%s' (stara) pominięta przez AI — dodano do only_in_old", k)
        for k in keys_new:
            if k not in covered_new:
                only_new.append(k)
                log.warning("match_sections_ai: sekcja '%s' (nowa) pominięta przez AI — dodano do only_in_new", k)

        log.info("match_sections_ai OK  %d par  %d usuniętych  %d nowych",
                 len(matched), len(only_old), len(only_new))
        return {"matched": matched, "only_in_old": only_old, "only_in_new": only_new}

    except Exception as exc:
        log.warning("match_sections_ai błąd (%s): %s — fallback do dopasowania po kluczu",
                    type(exc).__name__, exc)
        common = [k for k in keys_old if k in sekcje_new]
        return {
            "matched":     [{"old": k, "new": k} for k in common],
            "only_in_old": [k for k in keys_old if k not in sekcje_new],
            "only_in_new": [k for k in keys_new if k not in sekcje_old],
        }


def build_section_pairs(matching):
    """Flatten a matching dict into an ordered list for batch processing.

    Each element: {"old": key_or_none, "new": key_or_none}.
    Order: matched pairs first, then only_in_old, then only_in_new.
    """
    pairs = []
    for m in matching.get("matched", []):
        pairs.append({"old": m["old"], "new": m["new"]})
    for k in matching.get("only_in_old", []):
        pairs.append({"old": k, "new": None})
    for k in matching.get("only_in_new", []):
        pairs.append({"old": None, "new": k})
    return pairs


def _sekcja_label(old_key, new_key):
    """Human-readable label for a section pair."""
    if old_key and new_key:
        return old_key if old_key == new_key else f"{old_key} → {new_key}"
    if old_key:
        return f"{old_key} [usunięta]"
    return f"{new_key} [nowa]"


def _compare_pair(text_old, text_new, label_old, label_new, call_gemini, on_progress, settings,
                  struct_old=None, struct_new=None, call_extraction=None):
    _call_ext = call_extraction or call_gemini
    if struct_old is None:
        struct_old = _extract_structure(text_old, label_old, _call_ext, settings)
    if struct_new is None:
        struct_new = _extract_structure(text_new, label_new, _call_ext, settings)

    sekcje_old = struct_old.get("sekcje", {})
    sekcje_new = struct_new.get("sekcje", {})

    on_progress("Dopasowywanie sekcji (faza 1/2)…")
    matching = match_sections_ai(sekcje_old, sekcje_new, label_old, label_new, _call_ext, settings)
    all_pairs = build_section_pairs(matching)

    log.debug("_compare_pair START  %s vs %s  par: %d (%d dopasowanych, %d usuniętych, %d nowych)",
              label_old, label_new, len(all_pairs),
              len(matching["matched"]), len(matching["only_in_old"]), len(matching["only_in_new"]))

    changes = []
    found = 0

    for idx, pair in enumerate(all_pairs, 1):
        old_key = pair["old"]
        new_key = pair["new"]
        label   = _sekcja_label(old_key, new_key)

        tresc_stara = sekcje_old.get(old_key, "[SEKCJA NIEOBECNA W TEJ EDYCJI]") if old_key else "[SEKCJA NIEOBECNA W TEJ EDYCJI]"
        tresc_nowa  = sekcje_new.get(new_key, "[SEKCJA NIEOBECNA W TEJ EDYCJI]") if new_key else "[SEKCJA NIEOBECNA W TEJ EDYCJI]"

        on_progress(f"Porównanie sekcji {idx}/{len(all_pairs)}: {label}" + (f" — {found} zmian" if found else ""))
        log.debug("_compare_pair  para %d/%d: %s", idx, len(all_pairs), label[:80])

        if tresc_stara == tresc_nowa:
            log.debug("_compare_pair  para %s — identyczna, pomijam", label)
            continue

        prompt = (
            (settings.comparison_prompt_comparison or DEFAULT_PROMPT_COMPARISON)
            .replace("{label_old}", label_old)
            .replace("{label_new}", label_new)
            .replace("{sekcja}", label)
            .replace("{tresc_stara}", tresc_stara[:4000])
            .replace("{tresc_nowa}", tresc_nowa[:4000])
        )
        try:
            resp = call_gemini(prompt)
            raw = resp.text.strip()
            if raw == "BRAK_ZMIAN":
                log.debug("_compare_pair  para %s — BRAK_ZMIAN", label)
            else:
                raw = raw.replace("```json", "").replace("```", "").strip()
                obj = json.loads(raw)
                obj["sekcja"] = label
                changes.append(obj)
                found += 1
                log.debug("_compare_pair  para %s — zmiana: typ=%s waga=%s",
                          label, obj.get("typ_zmiany", "?"), obj.get("waga", "?"))
        except Exception as e:
            log.warning("_compare_pair  para %s — błąd analizy (%s): %s",
                        label, type(e).__name__, e)
            changes.append({
                "sekcja": label,
                "zapis_stary": tresc_stara[:500],
                "zapis_nowy": tresc_nowa[:500],
                "typ_zmiany": "INNE",
                "waga": "NISKA",
                "komentarz_biznesowy": f"[Blad analizy: {str(e)[:200]}]",
            })
            found += 1

    log.debug("_compare_pair KONIEC  znaleziono %d zmian z %d par", found, len(all_pairs))
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


# ── Batch-comparison helpers (browser-driven, one HTTP req per N sections) ──

def make_gemini_caller(settings, stage: str = "comparison"):
    """Create a tracked Gemini caller for a specific pipeline stage.

    Returns (call_fn, tokens_dict).  The default stage is "comparison"
    since that is what routes/comparison.py uses for batch section calls.
    """
    from google import genai
    client = genai.Client(api_key=settings.gemini_api_key)
    tokens = {"in": 0, "out": 0}
    call_fn = _make_stage_caller(client, settings, stage, tokens)
    return call_fn, tokens


def get_pair_structures(doc_old, doc_new, settings):
    """Extract and cache document structures.

    Returns (struct_old, struct_new, all_keys, t_in, t_out).
    Uses pre-computed extraction cache when available (no Drive download needed).
    """
    call_gemini, tokens = make_gemini_caller(settings, stage="extraction")

    struct_old, _ = _structure_for_doc(doc_old, settings, call_gemini)
    struct_new, _ = _structure_for_doc(doc_new, settings, call_gemini)

    all_keys = sorted(set(
        list(struct_old.get("sekcje", {}).keys()) +
        list(struct_new.get("sekcje", {}).keys())
    ))
    log.debug("get_pair_structures  sekcji łącznie: %d", len(all_keys))
    return struct_old, struct_new, all_keys, tokens["in"], tokens["out"]


_SKIP_REDACTIONAL_NOTE = (
    "DODATKOWA ZASADA: Jesli jedyna roznica miedzy fragmentami to zmiana czysto redakcyjna "
    "(styl, formatowanie, numeracja, brzmienie bez zmiany znaczenia merytorycznego — "
    "bez wplywu na wymagania, terminy, kwoty ani kryteria oceny) — "
    "zwroc dokladnie to slowo (nic wiecej): BRAK_ZMIAN\n\n"
)


def compare_sections_batch(sekcje_old, sekcje_new, section_keys=None,
                            label_old="", label_new="", call_gemini=None, settings=None,
                            on_progress=None, section_offset=0, sections_total=None,
                            skip_redactional=False, section_pairs=None):
    """Process a batch of section pairs and return a list of changes.

    `section_pairs` (preferred): list of {"old": key_or_none, "new": key_or_none} dicts
    produced by build_section_pairs().  When None, falls back to the legacy `section_keys`
    list of string keys (old behaviour, key-based matching).
    """
    # Normalise to a list of (old_key, new_key, display_label) triples
    if section_pairs is not None:
        items = [
            (p["old"], p["new"], _sekcja_label(p["old"], p["new"]))
            for p in section_pairs
        ]
    else:
        items = [(k, k, k) for k in (section_keys or [])]

    changes = []
    total = sections_total if sections_total is not None else len(items)

    for i, (old_key, new_key, label) in enumerate(items, 1):
        if on_progress:
            global_i = section_offset + i
            pct = round(global_i / total * 100) if total > 0 else 0
            try:
                on_progress(f"sekcja {global_i}/{total} ({pct}%) {label}")
            except Exception:
                pass

        tresc_stara = sekcje_old.get(old_key, "[SEKCJA NIEOBECNA W TEJ EDYCJI]") if old_key else "[SEKCJA NIEOBECNA W TEJ EDYCJI]"
        tresc_nowa  = sekcje_new.get(new_key, "[SEKCJA NIEOBECNA W TEJ EDYCJI]") if new_key else "[SEKCJA NIEOBECNA W TEJ EDYCJI]"

        if tresc_stara == tresc_nowa:
            log.debug("compare_sections_batch  para %s — identyczna", label[:60])
            continue

        base_prompt = settings.comparison_prompt_comparison or DEFAULT_PROMPT_COMPARISON
        if skip_redactional:
            base_prompt = _SKIP_REDACTIONAL_NOTE + base_prompt

        prompt = (
            base_prompt
            .replace("{label_old}", label_old)
            .replace("{label_new}", label_new)
            .replace("{sekcja}", label)
            .replace("{tresc_stara}", tresc_stara[:4000])
            .replace("{tresc_nowa}", tresc_nowa[:4000])
        )
        try:
            resp = call_gemini(prompt)
            raw  = resp.text.strip()
            if raw == "BRAK_ZMIAN":
                log.debug("compare_sections_batch  para %s — BRAK_ZMIAN", label[:60])
            else:
                raw = raw.replace("```json", "").replace("```", "").strip()
                obj = json.loads(raw)
                obj["sekcja"] = label
                if skip_redactional and obj.get("typ_zmiany") == "ZMIANA_REDAKCYJNA":
                    log.debug("compare_sections_batch  para %s — pomijam ZMIANA_REDAKCYJNA", label[:60])
                else:
                    changes.append(obj)
                    log.debug("compare_sections_batch  para %s — zmiana: %s/%s",
                              label[:60], obj.get("typ_zmiany"), obj.get("waga"))
        except Exception as e:
            log.warning("compare_sections_batch  para %s — błąd (%s): %s",
                        label[:60], type(e).__name__, e)
            changes.append({
                "sekcja": label,
                "zapis_stary": tresc_stara[:500],
                "zapis_nowy": tresc_nowa[:500],
                "typ_zmiany": "INNE",
                "waga": "NISKA",
                "komentarz_biznesowy": f"[Blad analizy: {str(e)[:200]}]",
            })

    return changes


def generate_pair_summary(changes, label_old, label_new, competition_name, settings):
    """Generate per-file summary from accumulated changes.

    Returns (summary_text, t_in, t_out).
    """
    call_gemini, tokens = make_gemini_caller(settings, stage="summary")
    text = _file_summary(
        changes,
        label_old or "Edycja starsza",
        label_new or "Edycja nowsza",
        competition_name or "konkurs",
        call_gemini, settings,
    )
    return text, tokens["in"], tokens["out"]


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

        total_tokens = {"in": 0, "out": 0}
        call_extraction = _make_stage_caller(client, settings, "extraction", total_tokens)
        call_comparison = _make_stage_caller(client, settings, "comparison", total_tokens)
        call_summary_st = _make_stage_caller(client, settings, "summary",    total_tokens)
        call_edition    = _make_stage_caller(client, settings, "edition",    total_tokens)

        def save(detail=None):
            if detail is not None:
                job.status_detail = detail
            db.session.commit()

        def finish(ok=True):
            t_in  = total_tokens["in"]
            t_out = total_tokens["out"]
            cost  = (t_in / 1_000_000 * price["input"]) + (t_out / 1_000_000 * price["output"])
            job.tokens_input       = t_in
            job.tokens_output      = t_out
            job.estimated_cost_usd = round(cost, 6)
            job.finished_at        = datetime.utcnow()
            job.gemini_model_used  = model_name

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

                save(f"Plik {idx + 1}/{n}: {old_name} — Analiza struktury...")

                def on_progress(msg, _idx=idx, _name=old_name, _n=n):
                    save(f"Plik {_idx + 1}/{_n}: {_name} — {msg}")

                struct_old, text_old = _structure_for_doc(old_doc, settings, call_extraction)
                struct_new, text_new = _structure_for_doc(new_doc, settings, call_extraction)

                changes = _compare_pair(
                    text_old, text_new,
                    job.label_old or "Edycja starsza",
                    job.label_new or "Edycja nowsza",
                    call_comparison, on_progress, settings,
                    struct_old=struct_old, struct_new=struct_new,
                    call_extraction=call_extraction,
                )

                save(f"Plik {idx + 1}/{n}: {old_name} — Generuje podsumowanie pliku...")
                summary = _file_summary(
                    changes,
                    job.label_old or "Edycja starsza",
                    job.label_new or "Edycja nowsza",
                    job.competition_name or "konkurs",
                    call_summary_st, settings,
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
                (settings.comparison_prompt_edition or DEFAULT_PROMPT_EDITION_SUMMARY)
                .replace("{label_old}", job.label_old or "Edycja starsza")
                .replace("{label_new}", job.label_new or "Edycja nowsza")
                .replace("{competition_name}", job.competition_name or "konkurs")
                .replace("{n_files}", str(len(per_file_results)))
                .replace("{per_file_summaries}", per_file_summaries[:30_000])
            )
            job.edition_summary = call_edition(edition_prompt).text

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
