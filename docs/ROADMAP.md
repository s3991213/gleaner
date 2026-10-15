# Roadmap

## Future development (DO NOT BUILD NOW; only keep the code easy to extend)

Podcast transcript search: find the episode and timestamp where a phrase or concept was said.

- Ingest runs on the Mac, not Azure: read podcast RSS audio enclosures, use podcast:transcript tags when present, otherwise transcribe locally with mlx-whisper
- Chunk transcripts into ~30s segments with timestamps; SQLite FTS5 for exact phrases plus small embeddings for concept search
- Search API returns episode, timestamp, short snippet and a deep link. Never publish full transcripts
- To prepare now: keep feed parsing in app/parser.py reusable and preserve enclosure URLs (audio links) in parsed items, but do not show them in the UI yet

## Groundwork already in place

- `app/parser.py` has no web framework imports. `parse_feed(content, source_url)` can be called from a local ingest script.
- Every parsed item carries `enclosures: [{url, type, length}]`. The API returns them; the UI ignores them.
