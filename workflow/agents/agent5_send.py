"""
Agent 5 — Gmail Send Agent
============================
Input  : professor research dict (from Agent2) + email .txt path (Agent4)
Output : Gmail sent message-id  +  status row in data/send_status.db

Parsing protocol for Agent4 email files
-----------------------------------------
The file written by Agent4 always begins with:

    SUBJECT: <subject line>

    <body …>

    [Your Name]
    [Your Email]
    ...

Agent5 strips the SUBJECT: line to use as the email subject and sends
everything below (up to the first signature placeholder) as the body text.

Skip logic
----------
If the professor's email is empty (unknown) or a send record already exists
for this slug, the agent skips silently and returns None.
"""

import logging
import re
from pathlib import Path
from typing import Dict, Optional

from config.settings import EMAILS_DIR, GMAIL_ENABLED, GMAIL_FOLLOW_UP_DAYS
from skills.event_bus   import bus, Event, EventType
from skills.send_tracker import SendTracker

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(
    r"^\s*(\[Your Name\]|\[Your Email\]|\[GitHub|Best regards|Sincerely)",
    re.IGNORECASE | re.MULTILINE,
)


class Agent5Send:
    """Parse generated email file → send via Gmail → record result in SQLite."""

    AGENT_ID = 5

    def __init__(self) -> None:
        self._tracker: Optional[SendTracker] = None
        self._mailer = None  # lazy; only imported if GMAIL_ENABLED

    def run(self, professor_research: Dict, email_path: str) -> Optional[str]:
        """
        Parameters
        ----------
        professor_research : enriched dict from Agent2
        email_path         : path to the .txt email file written by Agent4

        Returns
        -------
        Gmail message-id string, or None if skipped / failed.
        """
        slug  = professor_research.get("slug",  "unknown")
        name  = professor_research.get("name",  slug)
        email = professor_research.get("email", "").strip()

        bus.post(Event(EventType.AGENT_START, self.AGENT_ID, {"professor": name}))

        try:
            # ── Guard: Gmail disabled ────────────────────────────────────────
            if not GMAIL_ENABLED:
                msg = "GMAIL_ENABLED=false — skipping send (dry-run mode)"
                logger.info(f"Agent5: {msg}")
                bus.post(Event(EventType.AGENT_STEP, self.AGENT_ID,
                               {"step": "Skipped (dry-run)"}))
                bus.post(Event(EventType.AGENT_COMPLETE, self.AGENT_ID,
                               {"skipped": True, "reason": msg}))
                return None

            # ── Guard: no email address ──────────────────────────────────────
            if not email or "@" not in email:
                msg = f"No valid email address for {name} — skipping"
                logger.warning(f"Agent5: {msg}")
                bus.post(Event(EventType.AGENT_STEP, self.AGENT_ID,
                               {"step": "Skipped (no email)"}))
                bus.post(Event(EventType.AGENT_COMPLETE, self.AGENT_ID,
                               {"skipped": True, "reason": msg}))
                return None

            # ── Guard: already sent ──────────────────────────────────────────
            tracker = self._get_tracker()
            if tracker.has_been_sent(slug):
                msg = f"Already sent to {name} — skipping duplicate"
                logger.info(f"Agent5: {msg}")
                bus.post(Event(EventType.AGENT_STEP, self.AGENT_ID,
                               {"step": "Skipped (already sent)"}))
                bus.post(Event(EventType.AGENT_COMPLETE, self.AGENT_ID,
                               {"skipped": True, "reason": msg}))
                return None

            # ── Step 1: Parse email file ─────────────────────────────────────
            bus.post(Event(EventType.AGENT_STEP, self.AGENT_ID,
                           {"step": "Parse email file"}))
            subject, body = self._parse_email_file(email_path)
            logger.info(f"Agent5: subject='{subject[:60]}'")

            # ── Step 2: Rate-check + send ────────────────────────────────────
            bus.post(Event(EventType.AGENT_STEP, self.AGENT_ID,
                           {"step": f"Send via Gmail → {email}"}))
            mailer   = self._get_mailer()
            gmail_id = mailer.send_email(
                to_email    = email,
                subject     = subject,
                body_text   = body,
                # resume PDF attachment is optional; LaTeX must be compiled first
                attachment_path = self._find_pdf(slug),
            )

            # ── Step 3: Record result ────────────────────────────────────────
            bus.post(Event(EventType.AGENT_STEP, self.AGENT_ID,
                           {"step": "Record in status DB"}))
            if gmail_id:
                tracker.record_sent(
                    slug             = slug,
                    professor_name   = name,
                    to_email         = email,
                    subject          = subject,
                    gmail_message_id = gmail_id,
                    follow_up_days   = GMAIL_FOLLOW_UP_DAYS,
                )
                bus.post(Event(EventType.AGENT_COMPLETE, self.AGENT_ID, {
                    "gmail_id":   gmail_id,
                    "to":         email,
                    "follow_up":  GMAIL_FOLLOW_UP_DAYS,
                }))
            else:
                tracker.record_failure(slug, name, email, subject, "send returned None")
                bus.post(Event(EventType.AGENT_ERROR, self.AGENT_ID,
                               {"error": "Gmail API returned no message-id"}))

            return gmail_id

        except Exception as exc:
            logger.error(f"Agent5 error for {name}: {exc}")
            bus.post(Event(EventType.AGENT_ERROR, self.AGENT_ID, {"error": str(exc)}))
            # Record failure so we don't retry silently
            try:
                self._get_tracker().record_failure(
                    slug, name, email, "", str(exc)
                )
            except Exception:
                pass
            raise

    # ── Private helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _parse_email_file(email_path: str) -> tuple[str, str]:
        """
        Extract (subject, body) from an Agent4-generated .txt file.
        Subject is on the first non-blank line as "SUBJECT: ..."
        Body is everything after the blank line that follows the subject.
        """
        text = Path(email_path).read_text(encoding="utf-8")
        lines = text.splitlines()

        subject = ""
        body_start = 0

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.upper().startswith("SUBJECT:"):
                subject   = stripped[8:].strip()
                # body begins after the next blank line
                for j in range(i + 1, len(lines)):
                    if lines[j].strip():
                        body_start = j
                        break
                break

        if not subject:
            subject = "Prospective PhD / research applicant"

        # Trim placeholder signature lines from body
        body_lines = lines[body_start:]
        trimmed: list[str] = []
        for ln in body_lines:
            if _PLACEHOLDER_RE.match(ln):
                break
            trimmed.append(ln)

        body = "\n".join(trimmed).strip()
        if not body:
            body = text.strip()

        return subject, body

    @staticmethod
    def _find_pdf(slug: str) -> Optional[Path]:
        """
        Look for a compiled PDF alongside the .tex resume.
        Returns None if not found (PDF compilation is out-of-scope for this agent).
        """
        from config.settings import TAILORED_RESUMES_DIR
        pdf = TAILORED_RESUMES_DIR / f"{slug}_resume.pdf"
        return pdf if pdf.exists() else None

    def _get_tracker(self) -> SendTracker:
        if self._tracker is None:
            self._tracker = SendTracker()
        return self._tracker

    def _get_mailer(self):
        if self._mailer is None:
            from skills.gmail_mailer import GmailMailer
            self._mailer = GmailMailer()
        return self._mailer
