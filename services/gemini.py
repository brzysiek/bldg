import logging as _logging

from google import genai
from google.genai import types

log = _logging.getLogger(__name__)


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
    import time as _time
    from seed import DEFAULT_PROMPT

    model = (getattr(settings, "doc_summary_model", None) or "").strip() \
            or (settings.gemini_model or "").strip() \
            or "gemini-2.5-flash"
    temp       = getattr(settings, "doc_summary_temperature", None)
    max_tokens = getattr(settings, "doc_summary_max_tokens", None)
    system     = (getattr(settings, "doc_summary_system", None) or "").strip()

    config_kwargs = {}
    if temp is not None:
        config_kwargs["temperature"] = float(temp)
    if max_tokens:
        config_kwargs["max_output_tokens"] = int(max_tokens)
    if system:
        config_kwargs["system_instruction"] = system
    config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

    prompt = (settings.gemini_summary_prompt or DEFAULT_PROMPT).replace("{document_text}", text)

    log.debug(
        "summarize_document REQUEST  model=%s  prompt=%d znaków  "
        "temperature=%s  max_output_tokens=%s  system=%d znaków",
        model, len(prompt),
        temp       if temp       is not None else "default",
        max_tokens if max_tokens is not None else "default",
        len(system) if system else 0,
    )

    client = genai.Client(api_key=settings.gemini_api_key)
    kwargs = dict(model=model, contents=prompt)
    if config:
        kwargs["config"] = config

    t0 = _time.monotonic()
    response = client.models.generate_content(**kwargs)
    elapsed  = _time.monotonic() - t0

    t_in = t_out = 0
    u = getattr(response, "usage_metadata", None)
    if u:
        t_in  = getattr(u, "prompt_token_count",     0) or 0
        t_out = getattr(u, "candidates_token_count", 0) or 0

    raw = (response.text or "").strip()
    log.debug(
        "summarize_document RESPONSE  elapsed=%.2fs  tok_in=%d  tok_out=%d  "
        "response_len=%d znaków  preview=%.120r",
        elapsed, t_in, t_out, len(raw), raw[:120],
    )

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
