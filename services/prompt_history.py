from extensions import db
from models import PromptVersion


def record_prompt_version(prompt_key, content, source="manual"):
    if not content:
        return
    last = (
        PromptVersion.query
        .filter_by(prompt_key=prompt_key)
        .order_by(PromptVersion.id.desc())
        .first()
    )
    if last and last.content == content:
        return
    db.session.add(PromptVersion(prompt_key=prompt_key, content=content, source=source))


def get_prompt_history(prompt_key, limit=20):
    return (
        PromptVersion.query
        .filter_by(prompt_key=prompt_key)
        .order_by(PromptVersion.created_at.desc(), PromptVersion.id.desc())
        .limit(limit)
        .all()
    )


def get_all_prompt_histories(keys, limit=20):
    """Fetch version history for multiple prompt keys in a single DB query.

    Returns a dict {key: [PromptVersion, ...]} ordered newest-first, capped at
    `limit` entries per key. Uses one SELECT instead of N separate queries.
    """
    rows = (
        PromptVersion.query
        .filter(PromptVersion.prompt_key.in_(keys))
        .order_by(PromptVersion.created_at.desc(), PromptVersion.id.desc())
        .all()
    )
    result = {k: [] for k in keys}
    for row in rows:
        bucket = result.get(row.prompt_key)
        if bucket is not None and len(bucket) < limit:
            bucket.append(row)
    return result
