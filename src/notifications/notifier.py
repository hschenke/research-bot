"""E-Mail-Versandservice via Resend API oder SMTP (STARTTLS) ohne PII-Leakage in Logs."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

from src.config.constants import EMAIL_HTTP_TIMEOUT_SECONDS
from src.config.settings import AppSettings
from src.domain.enums import EmailProvider
from src.domain.exceptions import InputValidationError
from src.domain.models import ResearchJob, ResearchReport
from src.notifications.email_renderer import build_report_app_url, render_report_email_html
from src.security.sanitization import validate_email_address

logger = logging.getLogger(__name__)

_RESEND_ENDPOINT = "https://api.resend.com/emails"


def mask_email_for_log(email: str) -> str:
    """Maskiert eine E-Mail-Adresse datenschutzkonform für Anwendungs-Logs (`h***@domain.de`)."""
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    prefix = local[0] if local else "*"
    return f"{prefix}***@{domain}"


class EmailNotifier:
    """Versendet formatierte HTML-Ergebnisberichte über den konfigurierten E-Mail-Provider."""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings

    def notify_report_ready(
        self,
        job: ResearchJob,
        report: ResearchReport,
        override_recipient: str = "",
    ) -> bool:
        """Rendert den Bericht als HTML-Mail und sendet ihn an den gewünschten Empfänger."""
        raw_recipient = override_recipient.strip() or job.notify_email.strip() or self._settings.email_to.strip()
        if not raw_recipient:
            logger.info("Kein E-Mail-Empfänger für Job '%s' angegeben – Versand übersprungen.", job.id)
            return False

        try:
            recipient = validate_email_address(raw_recipient)
        except InputValidationError as exc:
            logger.warning("Ungültige Empfängeradresse für Job '%s': %s", job.id, exc)
            return False

        if not self._settings.has_email_configured:
            logger.info("Kein aktiver E-Mail-Provider konfiguriert – Versand übersprungen.")
            return False

        app_url = build_report_app_url(
            base_url=self._settings.streamlit_app_url,
            job_id=report.job_id,
            app_password=self._settings.app_password,
        )
        html_body = render_report_email_html(report=report, app_report_url=app_url)
        subject = f"🔬 Recherche fertig: {report.title[:90]}"

        if self._settings.email_provider == EmailProvider.RESEND:
            return self._send_via_resend(recipient, subject, html_body)
        if self._settings.email_provider == EmailProvider.SMTP:
            return self._send_via_smtp(recipient, subject, html_body)
        return False

    def _send_via_resend(self, recipient: str, subject: str, html_body: str) -> bool:
        masked = mask_email_for_log(recipient)
        try:
            response = requests.post(
                _RESEND_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self._settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": self._settings.email_from,
                    "to": [recipient],
                    "subject": subject,
                    "html": html_body,
                },
                timeout=EMAIL_HTTP_TIMEOUT_SECONDS,
            )
            if response.status_code in {200, 201}:
                logger.info("Ergebnis-E-Mail via Resend erfolgreich an %s gesendet.", masked)
                return True
            logger.error("Resend API antwortete mit HTTP %d.", response.status_code)
            return False
        except requests.RequestException as exc:
            logger.error("Netzwerkfehler beim Resend-Versand an %s: %s", masked, exc)
            return False

    def _send_via_smtp(self, recipient: str, subject: str, html_body: str) -> bool:
        masked = mask_email_for_log(recipient)
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._settings.smtp_user
        msg["To"] = recipient
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        tls_context = ssl.create_default_context()
        try:
            with smtplib.SMTP(
                self._settings.smtp_host,
                self._settings.smtp_port,
                timeout=EMAIL_HTTP_TIMEOUT_SECONDS,
            ) as server:
                server.starttls(context=tls_context)
                server.login(self._settings.smtp_user, self._settings.smtp_password)
                server.sendmail(self._settings.smtp_user, [recipient], msg.as_string())
            logger.info("Ergebnis-E-Mail via SMTP erfolgreich an %s gesendet.", masked)
            return True
        except (smtplib.SMTPException, OSError) as exc:
            logger.error("SMTP-Versand an %s fehlgeschlagen: %s", masked, exc)
            return False
