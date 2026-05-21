"""SQLite schema 定义与 migration 版本。

v0.1 策略：
- FTS5 采用 `trigram` tokenizer（SQLite ≥ 3.34 自带），对中文按三字连字切分，
  召回尚可、零依赖，后续再评估 ngram / jieba。
- FTS5 不使用 external content，直接存一份，避免维护同步触发器；
  fixture 级数据量下双份存储可接受。正式同步落库时再切换为 external content。
"""

from __future__ import annotations

SCHEMA_VERSION = 9


SCHEMA_V1_SQL = """
CREATE TABLE IF NOT EXISTS laws (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    short_title TEXT,
    aliases TEXT NOT NULL DEFAULT '[]',   -- JSON array
    level TEXT NOT NULL,
    issuing_body TEXT,
    document_number TEXT,
    released_at TEXT,                     -- ISO date
    effective_at TEXT,
    repealed_at TEXT,
    status TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_checked_at TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_laws_level ON laws(level);
CREATE INDEX IF NOT EXISTS idx_laws_status ON laws(status);
CREATE INDEX IF NOT EXISTS idx_laws_released_at ON laws(released_at);
CREATE INDEX IF NOT EXISTS idx_laws_title ON laws(title);

CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    law_id TEXT NOT NULL REFERENCES laws(id) ON DELETE CASCADE,
    number TEXT NOT NULL,                 -- 标准化阿拉伯数字，例 "71"
    number_display TEXT NOT NULL,         -- "第七十一条"
    part TEXT,
    title TEXT,
    text TEXT NOT NULL,
    position INTEGER NOT NULL,
    UNIQUE (law_id, number)
);

CREATE INDEX IF NOT EXISTS idx_articles_law_position ON articles(law_id, position);

CREATE TABLE IF NOT EXISTS categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id TEXT REFERENCES categories(id) ON DELETE SET NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS law_categories (
    law_id TEXT NOT NULL REFERENCES laws(id) ON DELETE CASCADE,
    category_id TEXT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    PRIMARY KEY (law_id, category_id)
);

CREATE TABLE IF NOT EXISTS revisions (
    id TEXT PRIMARY KEY,
    law_id TEXT NOT NULL REFERENCES laws(id) ON DELETE CASCADE,
    version_label TEXT NOT NULL,
    released_at TEXT NOT NULL,
    effective_at TEXT,
    notes TEXT,
    content_hash TEXT NOT NULL
);

-- 条文全文索引
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    article_id UNINDEXED,
    law_id UNINDEXED,
    law_title,
    number_display UNINDEXED,
    text,
    tokenize = 'trigram'
);

-- 法规标题/别名索引
CREATE VIRTUAL TABLE IF NOT EXISTS laws_fts USING fts5(
    law_id UNINDEXED,
    title,
    short_title,
    aliases,
    tokenize = 'trigram'
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


SCHEMA_V2_SQL = SCHEMA_V1_SQL.replace(
    "content_hash TEXT NOT NULL\n);",
    "content_hash TEXT NOT NULL,\n    snapshot_json TEXT\n);",
)


SCHEMA_V3_SQL = SCHEMA_V2_SQL + """

CREATE TABLE IF NOT EXISTS norm_packs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    summary TEXT,
    scope TEXT,
    maintainer TEXT,
    version_policy TEXT NOT NULL DEFAULT 'current',
    source_kind TEXT NOT NULL DEFAULT 'manual',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS norm_pack_items (
    id TEXT PRIMARY KEY,
    pack_id TEXT NOT NULL REFERENCES norm_packs(id) ON DELETE CASCADE,
    item_type TEXT NOT NULL,
    law_id TEXT,
    law_title TEXT,
    article_number TEXT,
    article_number_display TEXT,
    role TEXT NOT NULL,
    reason TEXT,
    note TEXT,
    reference_text TEXT,
    position INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_norm_packs_name ON norm_packs(name);
CREATE INDEX IF NOT EXISTS idx_norm_pack_items_pack_position
    ON norm_pack_items(pack_id, position);
"""


SCHEMA_V4_SQL = SCHEMA_V3_SQL + """

CREATE TABLE IF NOT EXISTS norm_sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    short_name TEXT,
    aliases TEXT NOT NULL DEFAULT '[]',
    source_type TEXT NOT NULL,
    authority TEXT,
    binding_scope TEXT,
    jurisdiction TEXT,
    effective_at TEXT,
    repealed_at TEXT,
    source_url TEXT,
    source_name TEXT NOT NULL,
    source_checked_at TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS norm_clauses (
    id TEXT PRIMARY KEY,
    norm_source_id TEXT NOT NULL REFERENCES norm_sources(id) ON DELETE CASCADE,
    number TEXT,
    number_display TEXT,
    title TEXT,
    text TEXT NOT NULL,
    position INTEGER NOT NULL,
    UNIQUE(norm_source_id, position)
);

CREATE INDEX IF NOT EXISTS idx_norm_sources_type ON norm_sources(source_type);
CREATE INDEX IF NOT EXISTS idx_norm_clauses_source_position
    ON norm_clauses(norm_source_id, position);

CREATE VIRTUAL TABLE IF NOT EXISTS norm_sources_fts USING fts5(
    norm_source_id UNINDEXED,
    name,
    short_name,
    aliases,
    tokenize = 'trigram'
);

CREATE VIRTUAL TABLE IF NOT EXISTS norm_clauses_fts USING fts5(
    clause_id UNINDEXED,
    norm_source_id UNINDEXED,
    norm_source_name,
    number_display UNINDEXED,
    text,
    tokenize = 'trigram'
);
"""


SCHEMA_V5_SQL = SCHEMA_V4_SQL.replace(
    "    article_number_display TEXT,\n"
    "    role TEXT NOT NULL,\n",
    "    article_number_display TEXT,\n"
    "    norm_source_id TEXT,\n"
    "    norm_source_name TEXT,\n"
    "    clause_number TEXT,\n"
    "    clause_number_display TEXT,\n"
    "    role TEXT NOT NULL,\n",
)


SCHEMA_V6_SQL = SCHEMA_V5_SQL.replace(
    "    metadata_json TEXT NOT NULL DEFAULT '{}',\n"
    "    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n",
    "    metadata_json TEXT NOT NULL DEFAULT '{}',\n"
    "    dependencies_json TEXT NOT NULL DEFAULT '{}',\n"
    "    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n",
    1,
)


SCHEMA_V7_SQL = SCHEMA_V6_SQL + """

CREATE TABLE IF NOT EXISTS law_relations (
    id TEXT PRIMARY KEY,
    relation_type TEXT NOT NULL,
    from_law_id TEXT NOT NULL,
    from_law_title TEXT,
    to_law_id TEXT NOT NULL,
    to_law_title TEXT,
    effective_at TEXT,
    source_name TEXT NOT NULL,
    source_url TEXT,
    source_checked_at TEXT NOT NULL,
    notes TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_law_relations_from
    ON law_relations(from_law_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_law_relations_to
    ON law_relations(to_law_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_law_relations_effective
    ON law_relations(effective_at);

CREATE TABLE IF NOT EXISTS applicability_rules (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    domain TEXT NOT NULL DEFAULT 'all',
    primary_law_id TEXT NOT NULL,
    primary_law_title TEXT,
    fallback_law_id TEXT,
    fallback_law_title TEXT,
    effective_from TEXT,
    effective_to TEXT,
    rule_text TEXT NOT NULL,
    transition_text TEXT,
    source_name TEXT NOT NULL,
    source_url TEXT,
    source_checked_at TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'seed',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_applicability_rules_topic
    ON applicability_rules(topic);
CREATE INDEX IF NOT EXISTS idx_applicability_rules_domain
    ON applicability_rules(domain);
CREATE INDEX IF NOT EXISTS idx_applicability_rules_window
    ON applicability_rules(effective_from, effective_to);
CREATE INDEX IF NOT EXISTS idx_applicability_rules_primary
    ON applicability_rules(primary_law_id);
CREATE INDEX IF NOT EXISTS idx_applicability_rules_fallback
    ON applicability_rules(fallback_law_id);
"""


SCHEMA_V8_SQL = SCHEMA_V7_SQL + """

-- 文号 → source detail_id 反查索引。
--
-- 公开法规 / 司法文件 / 公报案例的"文号"（如 ``法释〔2023〕13号``、``法发
-- 〔2019〕254号``、``中办发〔2020〕5号``）在用户社区里是更稳定的引用形式，
-- 比标题更不易歧义。本表把 ``(document_number, source)`` → 源主键
-- （``court_gongbao`` 用 detail_id；``flk_npc`` 用 bbbs）建立反查，让
-- ``chinalaw fetch "法释〔2023〕13号"`` 可绕过远程标题搜索直接命中。
--
-- 索引在 fetch / sync / fixture 入库时由 cleaning 层抽取的 ``document_number``
-- 自动写入；同一文号若在不同源都被入库，每条记录独立保留（PRIMARY KEY 含 source）。
CREATE TABLE IF NOT EXISTS document_number_index (
    document_number TEXT NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    law_id TEXT,
    title TEXT,
    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (document_number, source)
);

CREATE INDEX IF NOT EXISTS idx_document_number_index_doc
    ON document_number_index(document_number);
"""


SCHEMA_V9_SQL = SCHEMA_V8_SQL + """

-- 法条级注释 / 释义材料索引。
--
-- 该表组只承载本地授权材料的元数据与摘录，不作为公开再分发数据源。上游
-- law-data 可按 law_id + article_number 生成 bundle，由 chinalaw-cli 导入后
-- 供本机 agent 在取条文时按需查询。
CREATE TABLE IF NOT EXISTS commentary_books (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    publisher TEXT,
    edition TEXT,
    published_at TEXT,
    isbn TEXT,
    source_name TEXT NOT NULL,
    source_url TEXT,
    license_scope TEXT NOT NULL DEFAULT 'local_only',
    notes TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS article_commentaries (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES commentary_books(id) ON DELETE CASCADE,
    law_id TEXT NOT NULL,
    law_title TEXT,
    article_number TEXT NOT NULL,
    article_number_display TEXT,
    page_start INTEGER,
    page_end INTEGER,
    excerpt TEXT,
    summary TEXT,
    ocr_confidence REAL,
    boundary_confidence REAL,
    qa_status TEXT NOT NULL DEFAULT 'unchecked',
    license_scope TEXT NOT NULL DEFAULT 'local_only',
    source_hash TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    position INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_commentary_books_title
    ON commentary_books(title);
CREATE INDEX IF NOT EXISTS idx_article_commentaries_article
    ON article_commentaries(law_id, article_number);
CREATE INDEX IF NOT EXISTS idx_article_commentaries_book
    ON article_commentaries(book_id, position);
"""
