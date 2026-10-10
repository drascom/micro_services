"""LLM call log (T06, README §5.1). Prompt/reply TEXT is never stored here (size + secret risk), only sizes."""

VERSION = 5


def up(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS llm_calls ("
        " id INTEGER PRIMARY KEY, at INTEGER NOT NULL, purpose TEXT NOT NULL, site_id TEXT, stage TEXT, session TEXT,"
        " model TEXT, prompt_chars INTEGER, reply_chars INTEGER,"
        " input_tokens INTEGER, output_tokens INTEGER, cached_tokens INTEGER,"
        " ms INTEGER, ok INTEGER NOT NULL DEFAULT 0, error TEXT,"
        " background INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS llm_calls_site_at ON llm_calls(site_id, at)")
