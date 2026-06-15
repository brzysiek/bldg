from google import genai
from google.genai import types


def test_connection(api_key: str, model_name: str) -> dict:
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents="Odpowiedz krotko: czy dzialasz? Napisz tylko: TAK.",
        )
        return {"ok": True, "response": response.text.strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def summarize_document(text: str, settings) -> dict:
    """Returns {"summary": str, "description": str}."""
    import json as _json
    from seed import DEFAULT_PROMPT
    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = (settings.gemini_summary_prompt or DEFAULT_PROMPT).replace("{document_text}", text)
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
    )
    raw = response.text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw
    try:
        data = _json.loads(raw)
        return {
            "summary": str(data.get("podsumowanie", raw)),
            "description": str(data.get("opis", "")),
        }
    except (_json.JSONDecodeError, ValueError):
        return {"summary": raw, "description": ""}
