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


def run_comparison(job_id: int, app):
    """Glowna funkcja porownania — wywoływana w osobnym watku."""
    with app.app_context():
        from models import db, ComparisonJob, AppSettings
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

        # Cennik USD per 1M tokenow (czerwiec 2026)
        PRICING = {
            "gemini-2.5-flash":      {"input": 0.30,  "output": 2.50},
            "gemini-2.5-pro":        {"input": 1.25,  "output": 10.00},
            "gemini-2.5-flash-lite": {"input": 0.10,  "output": 0.40},
        }
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

        try:
            job.started_at = datetime.utcnow()
            # Krok 1: Ekstrakcja tekstu z plikow
            job.status = "extracting"
            save(f"Odczytuje pliki z dysku...")

            temp_data = json.loads(job.changes_json or "{}")
            path_old = temp_data.get("path_old")
            path_new = temp_data.get("path_new")

            job.status_detail = f"Ekstrakcja tekstu: {job.doc_old_name}"
            db.session.commit()
            text_old = extract_text(path_old, "")

            job.status_detail = f"Ekstrakcja tekstu: {job.doc_new_name}"
            db.session.commit()
            text_new = extract_text(path_new, "")

            chars_old = len(text_old)
            chars_new = len(text_new)

            # Krok 2: Ustrukturyzuj przez Gemini
            job.status = "chunking"
            save(f"Gemini analizuje strukturę dokumentu: {job.label_old} ({chars_old:,} znaków)...")

            def extract_structure(text, label):
                prompt = (settings.comparison_prompt_extraction or DEFAULT_PROMPT_EXTRACTION).replace(
                    "{document_text}", text[:300_000]
                )
                resp = call_gemini(prompt)
                raw = resp.text.strip().replace("```json", "").replace("```", "").strip()
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    words = text.split()
                    blocks = {}
                    for i in range(0, len(words), 500):
                        block_num = i // 500 + 1
                        blocks[f"Blok {block_num}"] = " ".join(words[i : i + 500])
                    return {"tytul": label, "sekcje": blocks}

            struct_old = extract_structure(text_old, job.label_old or "Edycja starsza")

            save(f"Gemini analizuje strukturę dokumentu: {job.label_new} ({chars_new:,} znaków)...")
            struct_new = extract_structure(text_new, job.label_new or "Edycja nowsza")

            sekcje_old = struct_old.get("sekcje", {})
            sekcje_new = struct_new.get("sekcje", {})
            all_keys = sorted(set(list(sekcje_old.keys()) + list(sekcje_new.keys())))

            job.progress_total = len(all_keys)
            job.progress_current = 0
            job.status = "comparing"
            save(f"Znaleziono {len(all_keys)} sekcji do porównania. Zaczynam analizę...")

            # Krok 3: Porownanie per-sekcja
            changes = []
            found_changes = 0

            for idx, sekcja in enumerate(all_keys, 1):
                tresc_stara = sekcje_old.get(sekcja, "[SEKCJA NIEOBECNA W TEJ EDYCJI]")
                tresc_nowa = sekcje_new.get(sekcja, "[SEKCJA NIEOBECNA W TEJ EDYCJI]")

                job.status_detail = (
                    f"Porównuję sekcję {idx}/{len(all_keys)}: {sekcja}"
                    + (f" — znaleziono {found_changes} zmian" if found_changes else "")
                )
                db.session.commit()

                if tresc_stara == tresc_nowa:
                    job.progress_current += 1
                    db.session.commit()
                    continue

                prompt = (
                    (settings.comparison_prompt_comparison or DEFAULT_PROMPT_COMPARISON)
                    .replace("{label_old}", job.label_old or "Edycja starsza")
                    .replace("{label_new}", job.label_new or "Edycja nowsza")
                    .replace("{sekcja}", sekcja)
                    .replace("{tresc_stara}", tresc_stara[:4000])
                    .replace("{tresc_nowa}", tresc_nowa[:4000])
                )

                try:
                    resp = call_gemini(prompt)
                    raw = resp.text.strip()

                    if raw == "BRAK_ZMIAN":
                        pass
                    else:
                        raw = raw.replace("```json", "").replace("```", "").strip()
                        change_obj = json.loads(raw)
                        change_obj["sekcja"] = sekcja
                        changes.append(change_obj)
                        found_changes += 1
                except Exception as e:
                    changes.append(
                        {
                            "sekcja": sekcja,
                            "zapis_stary": tresc_stara[:500],
                            "zapis_nowy": tresc_nowa[:500],
                            "typ_zmiany": "INNE",
                            "waga": "NISKA",
                            "komentarz_biznesowy": f"[Blad analizy tej sekcji: {str(e)[:200]}]",
                        }
                    )
                    found_changes += 1

                job.progress_current += 1
                job.changes_json = json.dumps(changes, ensure_ascii=False)
                db.session.commit()

                time.sleep(1.0)

            job.changes_json = json.dumps(changes, ensure_ascii=False)

            # Krok 4: Executive Summary
            job.status = "summarizing"
            save(f"Generuję Executive Summary dla {found_changes} znalezionych zmian...")

            if changes:
                changes_list_text = "\n".join(
                    [
                        f"- [{c.get('waga','?')}] {c.get('sekcja','?')} ({c.get('typ_zmiany','?')}): {c.get('komentarz_biznesowy','')[:300]}"
                        for c in changes
                    ]
                )
                summary_prompt = (
                    (settings.comparison_prompt_summary or DEFAULT_PROMPT_SUMMARY)
                    .replace("{label_old}", job.label_old or "Edycja starsza")
                    .replace("{label_new}", job.label_new or "Edycja nowsza")
                    .replace("{competition_name}", job.competition_name or "konkurs grantowy")
                    .replace("{changes_list}", changes_list_text[:20_000])
                )
                resp = call_gemini(summary_prompt)
                job.executive_summary = resp.text
            else:
                job.executive_summary = "**Brak istotnych roznic** — dokumenty sa identyczne lub roznice sa wylacznie redakcyjne."

            cost = (total_input / 1_000_000 * price["input"]) + (total_output / 1_000_000 * price["output"])
            job.status = "done"
            job.status_detail = f"Analiza zakonczona. Znaleziono {found_changes} zmian."
            job.gemini_model_used = model_name
            job.tokens_input = total_input
            job.tokens_output = total_output
            job.estimated_cost_usd = round(cost, 6)
            job.finished_at = datetime.utcnow()
            db.session.commit()

        except Exception as e:
            cost = (total_input / 1_000_000 * price["input"]) + (total_output / 1_000_000 * price["output"])
            job.status = "error"
            job.status_detail = None
            job.error_message = str(e)[:1000]
            job.tokens_input = total_input
            job.tokens_output = total_output
            job.estimated_cost_usd = round(cost, 6)
            job.finished_at = datetime.utcnow()
            db.session.commit()
