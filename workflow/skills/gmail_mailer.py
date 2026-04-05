"""
Gmail Mailer
============
Handles Google OAuth 2.0 auth, message construction, rate-limited sending,
and exponential-backoff retries via the Gmail REST API.

First-time setup
----------------
1. Go to Google Cloud Console → APIs & Services → Credentials
2. Create an OAuth 2.0 Client ID (Desktop App)
3. Download the JSON and save it as:
       config/gmail_credentials.json   (or set GMAIL_CREDENTIALS_PATH in .env)
4. First `GmailMailer()` call will open a browser tab to authorise.
   After approval, token.json is written and refreshed automatically forever.

Usage
-----
  mailer = GmailMailer()
  ok = mailer.send_email(
      to_email    = "professor@university.edu",
      subject     = "Prospective PhD student — NLP / low-resource NER",
      body_text   = "Dear Prof. ...",
      attachment_path = "/path/to/alice_resume.pdf",   # optional
  )
"""

from __future__ import annotations

import base64
import logging
import os
import random
import time
from email.mime.application import MIMEApplication
from email.mime.multipart   import MIMEMultipart
from email.mime.text        import MIMEText
from pathlib import Path
from typing import Optional

import backoff
from google.auth.transport.requests import Request
from google.oauth2.credentials        import Credentials
from google_auth_oauthlib.flow        import InstalledAppFlow
from googleapiclient.discovery        import build
from googleapiclient.errors           import HttpError

from config.settings import (
    GMAIL_CREDENTIALS_PATH,
    GMAIL_TOKEN_PATH,
    GMAIL_DAILY_LIMIT,
    GMAIL_DELAY_MIN,
    GMAIL_DELAY_MAX,
)

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

# How many HTTP 429 / 5xx before giving up on one email
_MAX_SEND_TRIES = 4


class RateLimiter:
    """
    In-process daily counter + random inter-email delay.

    daily_limit : max emails per calendar day  (Gmail free: ≤500; safe: 200)
    delay_range : (min_seconds, max_seconds) random sleep between sends
    """

    def __init__(
        self,
        daily_limit: int = GMAIL_DAILY_LIMIT,
        delay_range: tuple = (GMAIL_DELAY_MIN, GMAIL_DELAY_MAX),
    ) -> None:
        self._limit       = daily_limit
        self._delay_range = delay_range
        self._date        = self._today()
        self._count       = 0

    def check_and_wait(self) -> None:
        """
        Call this BEFORE each send attempt.
        Sleeps the random inter-email delay, then raises if daily cap reached.
        """
        today = self._today()
        if today != self._date:          # new calendar day → reset counter
            self._date  = today
            self._count = 0

        if self._count >= self._limit:
            raise RuntimeError(
                f"Daily Gmail send limit reached ({self._limit}). "
                "Aborting to protect account. Restart tomorrow."
            )

        if self._count > 0:              # no sleep before the very first email
            delay = random.uniform(*self._delay_range)
            logger.info(f"RateLimiter: waiting {delay:.1f}s before next send …")
            time.sleep(delay)

    def record_sent(self) -> None:
        self._count += 1
        logger.info(f"RateLimiter: sent today = {self._count}/{self._limit}")

    @staticmethod
    def _today() -> str:
        import datetime
        return datetime.date.today().isoformat()


class GmailMailer:
    """
    Wraps the Gmail API v1 with:
      • automatic OAuth token refresh
      • exponential-backoff retry on transient errors
      • per-email random delay (anti-spam)
      • daily send-count guard
    """

    def __init__(
        self,
        credentials_path: Optional[Path] = None,
        token_path: Optional[Path] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ) -> None:
        self._creds_path  = Path(credentials_path or GMAIL_CREDENTIALS_PATH)
        self._token_path  = Path(token_path       or GMAIL_TOKEN_PATH)
        self.rate_limiter = rate_limiter or RateLimiter()
        self.service      = self._authenticate()

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _authenticate(self):
        """
        OAuth flow with automatic token refresh.
        On first run: opens a browser tab.
        On every subsequent run: silently refreshes the token.
        """
        creds: Optional[Credentials] = None

        if self._token_path.exists():
            creds = Credentials.from_authorized_user_file(
                str(self._token_path), _SCOPES
            )

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("GmailMailer: refreshing expired OAuth token …")
                creds.refresh(Request())
            else:
                if not self._creds_path.exists():
                    raise FileNotFoundError(
                        f"Gmail credentials file not found: {self._creds_path}\n"
                        "Download it from Google Cloud Console → Credentials "
                        "and save to config/gmail_credentials.json"
                    )
                flow  = InstalledAppFlow.from_client_secrets_file(
                    str(self._creds_path), _SCOPES
                )
                creds = flow.run_local_server(port=0)
                logger.info("GmailMailer: new OAuth token obtained.")

            self._token_path.write_text(creds.to_json())
            logger.info(f"GmailMailer: token saved → {self._token_path}")

        return build("gmail", "v1", credentials=creds)

    # ── Message construction ──────────────────────────────────────────────────

    def build_message(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        attachment_path: Optional[Path] = None,
    ) -> dict:
        """
        Build a MIME message and return the base64url-encoded dict
        expected by the Gmail API.
        """
        if attachment_path and Path(attachment_path).exists():
            msg = MIMEMultipart()
            msg["To"]      = to_email
            msg["Subject"] = subject
            msg.attach(MIMEText(body_text, "plain", "utf-8"))

            att_path = Path(attachment_path)
            with att_path.open("rb") as f:
                part = MIMEApplication(f.read(), Name=att_path.name)
            part["Content-Disposition"] = (
                f'attachment; filename="{att_path.name}"'
            )
            msg.attach(part)
        else:
            msg = MIMEMultipart()
            msg["To"]      = to_email
            msg["Subject"] = subject
            msg.attach(MIMEText(body_text, "plain", "utf-8"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        return {"raw": raw}

    # ── Sending ───────────────────────────────────────────────────────────────

    def send_email(
        self,
        to_email: str,
        subject: str,
        body_text: str,
        attachment_path: Optional[Path] = None,
    ) -> Optional[str]:
        """
        Rate-check → build → send with backoff.

        Returns
        -------
        Gmail message-id string on success, None on failure.
        """
        logger.info(f"GmailMailer: preparing to send → {to_email}")
        self.rate_limiter.check_and_wait()

        msg_body = self.build_message(to_email, subject, body_text, attachment_path)

        try:
            result = self._send_with_backoff(msg_body)
            gmail_id: str = result["id"]
            self.rate_limiter.record_sent()
            logger.info(f"GmailMailer: sent ✓  id={gmail_id}")
            return gmail_id
        except Exception as exc:
            logger.error(f"GmailMailer: send failed to {to_email}: {exc}")
            return None

    @backoff.on_exception(
        backoff.expo,
        (HttpError, Exception),
        max_tries=_MAX_SEND_TRIES,
        giveup=lambda e: isinstance(e, HttpError) and e.resp.status in (400, 401, 403),
        logger=logger,
    )
    def _send_with_backoff(self, msg_body: dict) -> dict:
        return (
            self.service.users()
            .messages()
            .send(userId="me", body=msg_body)
            .execute()
        )
