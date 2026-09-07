#!/usr/bin/env python3
# Step 11B FK-safe probe cleanup (single source of truth; owner order
# 2026-09-06 21:05:46). Used by the production legs driver and by the
# independent cleanup proof on a DB copy. ASCII-clean; no token material.
#
# FK graph (inspected via PRAGMA foreign_key_list on the production kimi DB,
# 2026-09-06 21:0x):
#   transcript_events (PK session_id,seq) <- transcript_event_identities
#                                          <- session_transcript_active_events
#   session_windows (PK session_id)       <- acp_parent_stream_events
#                                          <- session_conversations
#                                          <- session_transcript_index_state
#                                          <- trajectory_runtime_events
#                                          <- transcript_events
#                                          <- transcript_rewrite_watermarks
#   session_nodes (PK session_key)        <- board_tabs
#                                          <- heartbeat_outcomes
#                                          <- session_members
#                                          <- session_participants
#                                          <- session_progress_cards
#                                          <- session_suggestions
#                                          <- session_windows (session_key)
#   board_tabs (PK tab_id)                <- board_widgets
# All edges are ON DELETE CASCADE. Python sqlite3 connections default to
# PRAGMA foreign_keys=OFF, so parent deletes do NOT cascade unless enabled:
# the original harness bug deleted parents with FK off and left orphans
# (session_transcript_active_events row 56168), which then failed the agent's
# foreign_key_check on the next admission. This module deletes children
# explicitly first and enables FK enforcement, then verifies with
# PRAGMA foreign_key_check. Scope is strictly the probe session key.
import sqlite3

CHILD_OF_TRANSCRIPT = [
    "transcript_event_identities",
    "session_transcript_active_events",
]
CHILD_OF_SESSION_WINDOW = [
    "acp_parent_stream_events",
    "session_conversations",
    "session_transcript_index_state",
    "trajectory_runtime_events",
    "transcript_rewrite_watermarks",
    "transcript_events",
]
CHILD_OF_SESSION_NODE = [
    "board_tabs",
    "heartbeat_outcomes",
    "session_members",
    "session_participants",
    "session_progress_cards",
    "session_suggestions",
]
# board_widgets references board_tabs via (session_key) and (tab_id)


def probe_session_ids(con, session_key):
    return [r[0] for r in con.execute(
        "SELECT session_id FROM session_windows WHERE session_key=?", (session_key,)
    ).fetchall()]


def reset_session(db_path, session_key):
    """FK-safe removal of every probe-owned row for session_key.

    Children are deleted before parents (explicit), FK enforcement is ON so
    any missed dependent fails loudly instead of silently orphaning, and a
    final PRAGMA foreign_key_check must return zero rows.
    """
    con = sqlite3.connect(db_path, timeout=30)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        sids = probe_session_ids(con, session_key)
        if not sids:
            # No session_windows row; still drop any stray session_nodes rows
            # (children first) so repeated resets are idempotent.
            con.execute("DELETE FROM board_widgets WHERE session_key=?", (session_key,))
            for t in CHILD_OF_SESSION_NODE:
                con.execute("DELETE FROM %s WHERE session_key=?" % t, (session_key,))
            con.execute("DELETE FROM session_nodes WHERE session_key=?", (session_key,))
            con.commit()
            _assert_fk_clean(con)
            return
        qmarks = ",".join("?" * len(sids))
        # children of transcript_events first
        for t in CHILD_OF_TRANSCRIPT:
            con.execute("DELETE FROM %s WHERE session_id IN (%s)" % (t, qmarks), sids)
        # transcript_events itself plus the other session_windows children
        for t in CHILD_OF_SESSION_WINDOW:
            con.execute("DELETE FROM %s WHERE session_id IN (%s)" % (t, qmarks), sids)
        # fts5 index rows are probe-owned (no FK, but must not survive the
        # session); fts5 only supports DELETE by rowid
        try:
            fids = [r[0] for r in con.execute(
                "SELECT rowid FROM session_transcript_fts WHERE session_id IN (%s)" % qmarks,
                sids,
            ).fetchall()]
            for rid in fids:
                con.execute("DELETE FROM session_transcript_fts WHERE rowid=?", (rid,))
        except Exception:
            pass  # fts cleanup best-effort; FK gate is authoritative
        # session_windows rows (child of session_nodes via session_key)
        con.execute("DELETE FROM session_windows WHERE session_key=?", (session_key,))
        # session_nodes subtree: board_widgets before board_tabs, then the rest
        con.execute("DELETE FROM board_widgets WHERE session_key=?", (session_key,))
        for t in CHILD_OF_SESSION_NODE:
            con.execute("DELETE FROM %s WHERE session_key=?" % t, (session_key,))
        con.execute("DELETE FROM session_nodes WHERE session_key=?", (session_key,))
        con.commit()
        _assert_fk_clean(con)
    finally:
        con.close()


def fk_violations(db_path):
    con = sqlite3.connect(db_path, timeout=30)
    try:
        return con.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        con.close()


def _assert_fk_clean(con):
    bad = con.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        raise RuntimeError("FK violation after reset: " + repr(bad[:5]))
