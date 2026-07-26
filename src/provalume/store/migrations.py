"""Linear, forward-only schema migrations (ADR-0003).

Rules, all enforced by :mod:`provalume.store.db`:

* Migrations apply in order; each runs inside its own transaction together with
  the version bump, so a crash leaves a consistent earlier version.
* **No down-migrations.** A rollback is: restore a backup, or export and
  re-import. Un-inventing a supersession chain cannot be done correctly.
* A database newer than the code is **refused**, not opened optimistically.
* Migrations may rebuild projections. They must never destroy journal rows.

Appending to :data:`MIGRATIONS` is the only supported way to change the schema.
Editing an existing entry changes what already-migrated databases contain versus
what fresh ones contain, which is how two installations silently diverge.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# 001 — the journal, memories, transitions, and full-text search
# ---------------------------------------------------------------------------
#
# Two things here are load-bearing and easy to miss:
#
# 1. The append-only triggers. Application discipline is not enough: the threat
#    model includes someone opening the file with `sqlite3` (T14), and a trigger
#    stops the casual case that a code convention does not.
#
# 2. `events.seq` is INTEGER PRIMARY KEY AUTOINCREMENT while `event_id` is the
#    logical identity. The chain is ordered by `seq` (local, dense, monotonic),
#    and identity is `event_id` (global, collision-resistant). Conflating them
#    would make an imported event's position depend on its origin machine.

_001_INITIAL = """
CREATE TABLE events (
    seq                    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id               TEXT    NOT NULL UNIQUE,
    schema_version         INTEGER NOT NULL,
    event_type             TEXT    NOT NULL,
    recorded_at            TEXT    NOT NULL,
    occurred_at            TEXT,
    project_id             TEXT    NOT NULL,
    repository_id          TEXT,
    run_id                 TEXT,
    task_id                TEXT,
    attempt_id             TEXT,
    agent_profile          TEXT,
    adapter                TEXT,
    model                  TEXT,
    effort                 TEXT,
    branch                 TEXT,
    worktree               TEXT,
    base_commit            TEXT,
    commit_sha             TEXT,
    causal_parent_event_id TEXT,
    source                 TEXT    NOT NULL,
    payload                TEXT    NOT NULL,
    payload_hash           TEXT    NOT NULL,
    event_hash             TEXT    NOT NULL,
    prev_event_hash        TEXT    NOT NULL,
    redaction              TEXT    NOT NULL DEFAULT '{}',
    integrity              TEXT    NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_events_project_seq   ON events(project_id, seq);
CREATE INDEX idx_events_type          ON events(project_id, event_type, seq);
CREATE INDEX idx_events_run           ON events(run_id, seq);
CREATE INDEX idx_events_task          ON events(task_id, seq);
CREATE INDEX idx_events_attempt       ON events(attempt_id, seq);
CREATE INDEX idx_events_branch        ON events(project_id, branch, seq);
CREATE INDEX idx_events_commit        ON events(commit_sha);
CREATE INDEX idx_events_payload_hash  ON events(payload_hash);
CREATE INDEX idx_events_source        ON events(project_id, source, seq);
CREATE INDEX idx_events_recorded_at   ON events(project_id, recorded_at);

-- Append-only, enforced by the database rather than by convention (T14).
CREATE TRIGGER events_no_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'provalume: events are append-only; UPDATE is refused');
END;

CREATE TRIGGER events_no_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'provalume: events are append-only; DELETE is refused');
END;

-- The chain head, kept as a single row so appending does not need a MAX() scan
-- and a rollback is detectable by comparing an externally-pinned head (T16).
CREATE TABLE journal_head (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    event_hash  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    event_count INTEGER NOT NULL
);
INSERT INTO journal_head (id, event_hash, seq, event_count) VALUES (1, '', 0, 0);

-- --------------------------------------------------------------------------
-- Memories: projections, therefore mutable and fully rebuildable.
-- --------------------------------------------------------------------------
CREATE TABLE memories (
    memory_id          TEXT PRIMARY KEY,
    schema_version     INTEGER NOT NULL,
    memory_type        TEXT NOT NULL,
    content            TEXT NOT NULL,
    text               TEXT NOT NULL,

    scope_level        TEXT NOT NULL,
    project_id         TEXT NOT NULL,
    repository_id      TEXT,
    branch             TEXT,

    run_id             TEXT,
    task_id            TEXT,
    attempt_id         TEXT,
    source_event_ids   TEXT NOT NULL DEFAULT '[]',
    source             TEXT NOT NULL,
    author_agent       TEXT,
    adapter            TEXT,
    model              TEXT,
    effort             TEXT,
    commit_sha         TEXT,

    valid_at           TEXT NOT NULL,
    invalid_at         TEXT,
    recorded_at        TEXT NOT NULL,

    supersedes_id      TEXT,
    trust_state        TEXT NOT NULL,
    verification_state TEXT NOT NULL,
    review_state       TEXT NOT NULL,
    integration_state  TEXT NOT NULL,

    access_count       INTEGER NOT NULL DEFAULT 0,
    last_accessed_at   TEXT,

    content_hash       TEXT NOT NULL,
    redaction          TEXT NOT NULL DEFAULT '{}',
    poisoning_risk     REAL NOT NULL DEFAULT 0.0,
    poisoning_matches  TEXT NOT NULL DEFAULT '[]',
    subject_key        TEXT NOT NULL DEFAULT '',

    FOREIGN KEY (supersedes_id) REFERENCES memories(memory_id)
);

CREATE INDEX idx_memories_project_type ON memories(project_id, memory_type);
CREATE INDEX idx_memories_trust        ON memories(project_id, trust_state);
CREATE INDEX idx_memories_branch       ON memories(project_id, branch);
CREATE INDEX idx_memories_subject      ON memories(project_id, subject_key);
CREATE INDEX idx_memories_current      ON memories(project_id, invalid_at);
CREATE INDEX idx_memories_supersedes   ON memories(supersedes_id);
CREATE INDEX idx_memories_commit       ON memories(commit_sha);
CREATE INDEX idx_memories_recorded_at  ON memories(project_id, recorded_at);
CREATE INDEX idx_memories_content_hash ON memories(project_id, content_hash);

-- --------------------------------------------------------------------------
-- Lifecycle transitions. Refusals are stored too (allowed = 0): a promotion
-- attempt that vanishes silently is what an attacker wants (ADR-0005).
-- --------------------------------------------------------------------------
CREATE TABLE memory_transitions (
    transition_id      TEXT PRIMARY KEY,
    memory_id          TEXT NOT NULL,
    from_state         TEXT NOT NULL,
    to_state           TEXT NOT NULL,
    policy_rule        TEXT NOT NULL,
    evidence_event_ids TEXT NOT NULL DEFAULT '[]',
    actor              TEXT NOT NULL,
    actor_source       TEXT NOT NULL,
    recorded_at        TEXT NOT NULL,
    scope              TEXT NOT NULL DEFAULT '',
    allowed            INTEGER NOT NULL DEFAULT 1,
    note               TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_transitions_memory ON memory_transitions(memory_id, recorded_at);
CREATE INDEX idx_transitions_rule   ON memory_transitions(policy_rule);
CREATE INDEX idx_transitions_allow  ON memory_transitions(allowed, recorded_at);

-- --------------------------------------------------------------------------
-- Failure signatures. The mechanism behind "this has failed before" (ADR-0007).
-- --------------------------------------------------------------------------
CREATE TABLE failure_signatures (
    signature      TEXT NOT NULL,
    project_id     TEXT NOT NULL,
    memory_id      TEXT NOT NULL,
    command        TEXT NOT NULL DEFAULT '',
    error_kind     TEXT NOT NULL DEFAULT '',
    occurrences    INTEGER NOT NULL DEFAULT 1,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    resolved_by_id TEXT,
    PRIMARY KEY (signature, project_id, memory_id)
);

CREATE INDEX idx_failsig_project ON failure_signatures(project_id, signature);
CREATE INDEX idx_failsig_memory  ON failure_signatures(memory_id);

-- --------------------------------------------------------------------------
-- Contradictions: detected, never auto-resolved. Recency is not correctness,
-- and the newer record may be the poisoned one (ADR-0009).
-- --------------------------------------------------------------------------
CREATE TABLE contradictions (
    contradiction_id TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    subject_key      TEXT NOT NULL,
    memory_id_a      TEXT NOT NULL,
    memory_id_b      TEXT NOT NULL,
    detected_at      TEXT NOT NULL,
    resolved         INTEGER NOT NULL DEFAULT 0,
    resolution_note  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX idx_contradictions_a ON contradictions(memory_id_a, resolved);
CREATE INDEX idx_contradictions_b ON contradictions(memory_id_b, resolved);
CREATE INDEX idx_contradictions_p ON contradictions(project_id, subject_key);

-- --------------------------------------------------------------------------
-- Links between memories: a gotcha and what resolved it, a lesson and its
-- later approval. Stored as edges so a resolution can be attached long after
-- the failure was recorded.
-- --------------------------------------------------------------------------
CREATE TABLE memory_links (
    link_id     TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL,
    from_id     TEXT NOT NULL,
    to_id       TEXT NOT NULL,
    link_type   TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (from_id, to_id, link_type)
);

CREATE INDEX idx_links_from ON memory_links(from_id, link_type);
CREATE INDEX idx_links_to   ON memory_links(to_id, link_type);

-- --------------------------------------------------------------------------
-- Full-text search. External-content FTS5: the index stores no copy of the
-- text, so there is exactly one place a memory's text lives and the index can
-- never disagree with the row (which would be a silent correctness bug).
--
-- `unicode61 remove_diacritics 2` is chosen over `porter` deliberately: this
-- corpus is full of commands, flags, and file paths, where stemming
-- `--no-verify` or `pytest` is actively harmful.
-- --------------------------------------------------------------------------
CREATE VIRTUAL TABLE memories_fts USING fts5(
    text,
    subject_key,
    content='memories',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER memories_fts_insert AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text, subject_key)
    VALUES (new.rowid, new.text, new.subject_key);
END;

CREATE TRIGGER memories_fts_delete AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text, subject_key)
    VALUES ('delete', old.rowid, old.text, old.subject_key);
END;

CREATE TRIGGER memories_fts_update AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text, subject_key)
    VALUES ('delete', old.rowid, old.text, old.subject_key);
    INSERT INTO memories_fts(rowid, text, subject_key)
    VALUES (new.rowid, new.text, new.subject_key);
END;

-- --------------------------------------------------------------------------
-- Optional vectors. The table exists unconditionally so enabling the extra
-- never needs a migration; it stays empty when vectors are unused.
-- --------------------------------------------------------------------------
CREATE TABLE memory_vectors (
    memory_id  TEXT NOT NULL,
    model_id   TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector     BLOB NOT NULL,
    PRIMARY KEY (memory_id, model_id)
);

CREATE INDEX idx_vectors_model ON memory_vectors(model_id);

-- --------------------------------------------------------------------------
-- Projection bookkeeping: which event the projections have been built through,
-- so `rebuild --check` can prove consistency without replaying everything.
-- --------------------------------------------------------------------------
CREATE TABLE projection_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    last_event_seq  INTEGER NOT NULL DEFAULT 0,
    rebuilt_at      TEXT NOT NULL DEFAULT '',
    projector_version INTEGER NOT NULL DEFAULT 1
);
INSERT INTO projection_state (id, last_event_seq) VALUES (1, 0);

-- --------------------------------------------------------------------------
-- Project registry. Present so `doctor` and `audit` can report what a database
-- contains without scanning the journal.
-- --------------------------------------------------------------------------
CREATE TABLE projects (
    project_id  TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT ''
);
"""

_002_FRESHNESS = """
-- ----------------------------------------------------------------------------
-- The freshness axis (ADR-0020). Both additions are projections: derived from
-- journal events, mutable, fully rebuilt by `provalume rebuild`.
-- ----------------------------------------------------------------------------

-- Orthogonal to trust_state, deliberately a separate column: a record can be
-- integrated and stale at once, and that combination must be expressible.
-- 'unverifiable' is the default because current must be earned by a recorded
-- blast radius — records predating the axis are not grandfathered.
ALTER TABLE memories ADD COLUMN freshness TEXT NOT NULL DEFAULT 'unverifiable';

CREATE INDEX idx_memories_freshness ON memories(project_id, freshness);

-- The latest blast radius per record, one row per path, so the commit
-- watcher's intersection is an indexed lookup rather than a journal scan.
-- Replaced wholesale when a record accrues a newer radius (latest wins).
CREATE TABLE blast_radii (
    record_id   TEXT NOT NULL,
    project_id  TEXT NOT NULL,
    method      TEXT NOT NULL,
    path        TEXT NOT NULL,
    PRIMARY KEY (record_id, path)
);

CREATE INDEX idx_blast_radii_path ON blast_radii(project_id, path);
"""

_003_FRESHNESS_TRIGGERS = """
-- ----------------------------------------------------------------------------
-- Per-trigger freshness bookkeeping (ADR-0020, M2 review). A relevance verdict
-- answers ONE trigger; deriving freshness from "the last event seen" let a
-- single irrelevant verdict discharge every outstanding trigger — a false
-- `current`, the exact failure this axis exists to prevent. The outstanding
-- set is a projection: derived from events, cleared and rebuilt by
-- `provalume rebuild`.
-- ----------------------------------------------------------------------------
CREATE TABLE freshness_triggers (
    project_id      TEXT NOT NULL,
    record_id       TEXT NOT NULL,
    trigger_commit  TEXT NOT NULL,
    PRIMARY KEY (project_id, record_id, trigger_commit)
);

-- blast_radii gains project_id in its key: project scoping as constraint,
-- not convention. Rebuilt rather than altered (SQLite cannot change a PK).
CREATE TABLE blast_radii_v2 (
    record_id   TEXT NOT NULL,
    project_id  TEXT NOT NULL,
    method      TEXT NOT NULL,
    path        TEXT NOT NULL,
    PRIMARY KEY (project_id, record_id, path)
);
INSERT INTO blast_radii_v2 SELECT record_id, project_id, method, path FROM blast_radii;
DROP TABLE blast_radii;
ALTER TABLE blast_radii_v2 RENAME TO blast_radii;
CREATE INDEX idx_blast_radii_path ON blast_radii(project_id, path);
"""

_004_TRIGGER_DISCHARGE = """
-- ----------------------------------------------------------------------------
-- Discharge becomes a flag, not a delete (ADR-0020, M3). Once relevance
-- verdicts discharge triggers, an idempotent re-scan can no longer key on the
-- outstanding set alone — a discharged trigger would re-book on every scan of
-- the same commit. The row is the memory that the commit was already seen;
-- the flag is whether it still demands attention. Still a projection.
-- ----------------------------------------------------------------------------
ALTER TABLE freshness_triggers ADD COLUMN discharged INTEGER NOT NULL DEFAULT 0;
"""

_005_TRIGGER_ASSESSED = """
-- ----------------------------------------------------------------------------
-- A trigger remembers whether its relevance was ever assessed (ADR-0020, M3
-- review). "Seen" alone cannot distinguish a trigger whose assessment
-- happened from one whose assessment failed open — and skipping both made a
-- transient git error permanently pin a trivia landing at `suspect`, with no
-- re-scan able to recover it. A re-scan now re-assesses seen-but-unassessed
-- triggers. Default 0 on upgrade: a pre-existing database recovers on its
-- next rebuild (the flag is a projection of `relevance.assessed` events); an
-- explicit re-scan before that rebuild may append one duplicate verdict,
-- which is append-only noise, not a derivation change.
-- ----------------------------------------------------------------------------
ALTER TABLE freshness_triggers ADD COLUMN assessed INTEGER NOT NULL DEFAULT 0;
"""

#: Every migration, in order. Index + 1 is the resulting schema version.
MIGRATIONS: Final[tuple[str, ...]] = (
    _001_INITIAL,
    _002_FRESHNESS,
    _003_FRESHNESS_TRIGGERS,
    _004_TRIGGER_DISCHARGE,
    _005_TRIGGER_ASSESSED,
)

#: The schema version this build writes and expects.
SCHEMA_VERSION: Final = len(MIGRATIONS)
