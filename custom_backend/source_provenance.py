"""Bibliography attached to portable analysis reports."""

from coscreen.db import get_article


def source_articles(db, rows: list[dict]) -> dict:
    sources = {}
    for source_key in {row['source_key'] for row in rows}:
        article = get_article(db, source_key)
        if article:
            sources[source_key] = {key: getattr(article, key) for key in
                                   ('title', 'authors', 'year', 'journal', 'doi')}
    return sources
