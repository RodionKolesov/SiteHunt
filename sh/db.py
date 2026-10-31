"""База SiteHunt (SQLite): найденные сайты, результаты проверки, контакты."""
import sqlite3

from .config import DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    host        TEXT PRIMARY KEY,   -- домен без www
    url         TEXT,               -- адрес из выдачи
    title       TEXT,
    niche       TEXT,
    city        TEXT,
    query       TEXT,               -- по какому запросу нашли
    engine      TEXT,
    found_at    TEXT DEFAULT (datetime('now')),
    checked_at  TEXT,
    status      TEXT,               -- ok | dead | parked | skip (агрегатор, сеть, не та ниша)
    final_url   TEXT,
    score       INTEGER,            -- 0–100: насколько сайт устарел (больше — хуже сайт, теплее клиент)
    reasons     TEXT,               -- JSON: [[код, текст, баллы], ...]
    facts       TEXT,               -- JSON: сырые замеры (https, viewport, год в подвале, jQuery, вес, время)
    name        TEXT,               -- название компании (из title / og:site_name)
    phones      TEXT,               -- JSON
    emails      TEXT,               -- JSON
    socials     TEXT,               -- JSON {host: url}
    address     TEXT,
    shot        TEXT                -- имя файла скриншота в data/shots
);
CREATE TABLE IF NOT EXISTS queries (
    q       TEXT,
    engine  TEXT,
    page    INTEGER,
    done_at TEXT DEFAULT (datetime('now')),
    found   INTEGER,
    PRIMARY KEY (q, engine, page)
);
"""


def connect():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con
