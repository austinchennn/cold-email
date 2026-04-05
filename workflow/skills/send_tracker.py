"""
Send Status Tracker
====================
SQLite-backed ledger of every email sent by Agent5.

Schema (table: sent_emails)
---------------------------
  slug            TEXT  — professor identifier
  professor_name  TEXT
  to_email        TEXT
  subject         TEXT
  sent_at         TEXT  — ISO-8601 UTC timestamp
  gmail_message_id TEXT  — returned by Gmail API
  status          TEXT  — "sent" | "replied" | "bounced" | "failed"
  follow_up_at    TEXT  — ISO-8601: earliest time to send follow-up (NULL = not due)
  follow_up_sent  INTEGER — 0 or 1

Usage
-----
  tracker = SendTracker()
  tracker.record_sent(slug, name, email, subject, gmail_id, follow_up_days=3)
  tracker.mark_replied(gmail_id)
  due = tracker.get_due_followups()   # → list of rows where follow-up is due
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from config.settings import SEND_TRACKER_DB

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class SendTracker:
    """Persistent email send log with follow-up scheduling."""

    def __init__(self, db_path: Path = SEND_TRACKER_DB) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Public API ─────────────────────────────────────────────────────────────

    def record_sent(
        self,
        slug:             str,
        professor_name:   str,
        to_email:         str,
        subject:          str,
        gmail_message_id: Optional[str],
        follow_up_days:   int = 3,
    ) -> None:
        """
        Log a successfully sent email.

        follow_up_days : schedule a follow-up N days from now (0 = no follow-up)
        """
        status       = "sent" if gmail_message_id else "failed"
        follow_up_at = None
        if follow_up_days > 0 and gmail_message_id:
            dt           = datetime.now(timezone.utc) + timedelta(days=follow_up_days)
            follow_up_at = dt.isoformat()

        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO sent_emails
                  (slug, professor_name, to_email, subject,
                   sent_at, gmail_message_id, status, follow_up_at, follow_up_sent)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    slug, professor_name, to_email, subject,
                    _utcnow(), gmail_message_id, status, follow_up_at,
                ),
            )
        logger.info(
            f"SendTracker: recorded {status}  slug={slug}  id={gmail_message_id}"
        )

    def record_failure(self, slug: str, professor_name: str, to_email: str,
                       subject: str, error: str) -> None:
        """Log a send attempt that failed before reaching Gmail."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO sent_emails
                  (slug, professor_name, to_email, subject,
                   sent_at, gmail_message_id, status, follow_up_at, follow_up_sent)
                VALUES (?, ?, ?, ?, ?, NULL, 'failed', NULL, 0)
                """,
                (slug, professor_name, to_email, subject, _utcnow()),
            )
        logger.warning(f"SendTracker: failed  slug={slug}  reason={error}")

    def mark_replied(self, gmail_message_id: str) -> None:
        """Call this when you detect a reply (e.g. from a Gmail watch webhook)."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE sent_emails SET status='replied', follow_up_at=NULL "
                "WHERE gmail_message_id = ?",
                (gmail_message_id,),
            )
        logger.info(f"SendTracker: marked replied  id={gmail_message_id}")

    def get_due_followups(self) -> List[Dict]:
        """
        Return rows where:
          status = 'sent'  (not replied / bounced)
          follow_up_at <= NOW
          follow_up_sent = 0
        """
        now = _utcnow()
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT slug, professor_name, to_email, subject, gmail_message_id
                FROM sent_emails
                WHERE status = 'sent'
                  AND follow_up_at IS NOT NULL
                  AND follow_up_at <= ?
                  AND follow_up_sent = 0
                ORDER BY follow_up_at ASC
                """,
                (now,),
            ).fetchall()
        return [
            dict(zip(
                ["slug", "professor_name", "to_email", "subject", "gmail_message_id"],
                row,
            ))
            for row in rows
        ]

    def mark_followup_sent(self, gmail_message_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE sent_emails SET follow_up_sent=1 WHERE gmail_message_id=?",
                (gmail_message_id,),
            )

    def has_been_sent(self, slug: str) -> bool:
        """True if we already have a successful send record for this professor."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM sent_emails WHERE slug=? AND status='sent' LIMIT 1",
                (slug,),
            ).fetchone()
        return row is not None

    def stats(self) -> Dict:
        """Return aggregate counts per status."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM sent_emails GROUP BY status"
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    # ── Schema init ───────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sent_emails (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug             TEXT    NOT NULL,
                    professor_name   TEXT    NOT NULL,
                    to_email         TEXT    NOT NULL,
                    subject          TEXT    NOT NULL,
                    sent_at          TEXT    NOT NULL,
                    gmail_message_id TEXT,
                    status           TEXT    NOT NULL DEFAULT 'sent',
                    follow_up_at     TEXT,
                    follow_up_sent   INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_slug ON sent_emails(slug)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_gid  ON sent_emails(gmail_message_id)"
            )

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path))
