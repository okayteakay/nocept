"""Notification system for invoice exception escalations and resolutions.

Supports Slack webhooks and SMTP email notifications.
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import httpx

from config.settings import AppConfig
from models.exception import InvoiceException, ExceptionState
from models.resolution import ResolutionMemo, ResolutionAction

logger = logging.getLogger(__name__)


class Notifier:
    """Send notifications via Slack and email."""

    def __init__(self, config: AppConfig):
        """Initialise notifier with config.

        Args:
            config: AppConfig instance with notification settings
        """
        self.config = config
        self.slack_webhook_url = config.slack_webhook_url
        self.slack_channel = config.slack_escalation_channel
        self.smtp_host = config.smtp_host
        self.smtp_port = config.smtp_port
        self.smtp_user = config.smtp_user
        self.smtp_password = config.smtp_password
        self.smtp_from_email = config.smtp_from_email
        self.notification_emails = (
            [e.strip() for e in config.notification_email_to.split(",")]
            if config.notification_email_to
            else []
        )

    def notify_escalation(
        self,
        exception: InvoiceException,
        memo: ResolutionMemo,
    ) -> None:
        """Notify AP team of escalated exception requiring human review.

        Args:
            exception: The exception being escalated
            memo: Resolution memo with root cause and evidence
        """
        logger.info(
            f"Sending escalation notification for exception {exception.exception_id}"
        )

        subject = (
            f"🚨 Invoice Exception Escalated: {exception.invoice.invoice_number}"
        )

        # Slack notification
        if self.slack_webhook_url:
            self._send_slack_escalation(exception, memo)
        else:
            logger.warning("Slack webhook URL not configured; skipping Slack notification")

        # Email notification
        if self.notification_emails:
            self._send_email_escalation(subject, exception, memo)
        else:
            logger.warning(
                "No notification email addresses configured; skipping email notification"
            )

    def notify_resolution(
        self,
        exception: InvoiceException,
        memo: ResolutionMemo,
        final_state: ExceptionState,
    ) -> None:
        """Notify AP team of resolved exception (optional).

        Only sends if explicitly configured; default is silent resolution.

        Args:
            exception: The resolved exception
            memo: Resolution memo
            final_state: Final state (RESOLVED or ESCALATED)
        """
        if final_state != ExceptionState.RESOLVED:
            return  # Only notify on actual resolution, not escalation

        logger.info(
            f"Sending resolution notification for exception {exception.exception_id}"
        )

        subject = f"✅ Invoice Exception Resolved: {exception.invoice.invoice_number}"

        # Optional Slack notification
        if self.slack_webhook_url and memo.action == ResolutionAction.AUTO_APPROVE:
            try:
                self._send_slack_resolution(exception, memo)
            except Exception as e:
                logger.warning(f"Failed to send Slack resolution notification: {e}")

    def _send_slack_escalation(
        self,
        exception: InvoiceException,
        memo: ResolutionMemo,
    ) -> None:
        """Send Slack message for escalated exception.

        Args:
            exception: The exception
            memo: Resolution memo with details
        """
        try:
            invoice = exception.invoice
            po = exception.purchase_order
            variance_str = f"${exception.total_variance_usd:,.2f}"
            if exception.total_variance_usd < 0:
                variance_str += " (credit)"

            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "🚨 Invoice Exception Escalated",
                        "emoji": True,
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Invoice*\n{invoice.invoice_number}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*PO*\n{po.po_number}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Supplier*\n{invoice.supplier_name}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Variance*\n{variance_str}",
                        },
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Exception Type(s)*\n{', '.join(t.value for t in exception.exception_types)}",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Root Cause*\n{memo.root_cause.value}",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Recommendation*\n{memo.summary}",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"👉 <http://localhost:8502|View in Dashboard>",
                    },
                },
            ]

            payload = {
                "channel": self.slack_channel,
                "blocks": blocks,
            }

            with httpx.Client() as client:
                response = client.post(
                    self.slack_webhook_url,
                    json=payload,
                    timeout=10,
                )
                response.raise_for_status()

            logger.info(
                f"Slack escalation notification sent for exception "
                f"{exception.exception_id}"
            )

        except Exception as e:
            logger.error(f"Failed to send Slack escalation notification: {e}")

    def _send_slack_resolution(
        self,
        exception: InvoiceException,
        memo: ResolutionMemo,
    ) -> None:
        """Send Slack message for resolved exception.

        Args:
            exception: The exception
            memo: Resolution memo
        """
        try:
            invoice = exception.invoice

            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "✅ Invoice Exception Auto-Resolved",
                        "emoji": True,
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Invoice*\n{invoice.invoice_number}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*PO*\n{exception.purchase_order.po_number}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Action*\n{memo.action.value}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Confidence*\n{memo.confidence:.0%}",
                        },
                    ],
                },
            ]

            payload = {
                "channel": self.slack_channel,
                "blocks": blocks,
            }

            with httpx.Client() as client:
                response = client.post(
                    self.slack_webhook_url,
                    json=payload,
                    timeout=10,
                )
                response.raise_for_status()

            logger.info(
                f"Slack resolution notification sent for exception "
                f"{exception.exception_id}"
            )

        except Exception as e:
            logger.error(f"Failed to send Slack resolution notification: {e}")

    def _send_email_escalation(
        self,
        subject: str,
        exception: InvoiceException,
        memo: ResolutionMemo,
    ) -> None:
        """Send email notification for escalated exception.

        Args:
            subject: Email subject
            exception: The exception
            memo: Resolution memo
        """
        try:
            invoice = exception.invoice
            po = exception.purchase_order

            html_body = f"""
<html>
  <body style="font-family: Arial, sans-serif; color: #333;">
    <h2 style="color: #d32f2f;">⚠️ Invoice Exception Escalated to Human Review</h2>

    <h3>Exception Summary</h3>
    <table style="border-collapse: collapse; width: 100%;">
      <tr style="background-color: #f5f5f5;">
        <td style="border: 1px solid #ddd; padding: 10px;"><strong>Invoice</strong></td>
        <td style="border: 1px solid #ddd; padding: 10px;">{invoice.invoice_number}</td>
      </tr>
      <tr>
        <td style="border: 1px solid #ddd; padding: 10px;"><strong>PO</strong></td>
        <td style="border: 1px solid #ddd; padding: 10px;">{po.po_number}</td>
      </tr>
      <tr style="background-color: #f5f5f5;">
        <td style="border: 1px solid #ddd; padding: 10px;"><strong>Supplier</strong></td>
        <td style="border: 1px solid #ddd; padding: 10px;">{invoice.supplier_name}</td>
      </tr>
      <tr>
        <td style="border: 1px solid #ddd; padding: 10px;"><strong>Variance</strong></td>
        <td style="border: 1px solid #ddd; padding: 10px;">${exception.total_variance_usd:,.2f}</td>
      </tr>
      <tr style="background-color: #f5f5f5;">
        <td style="border: 1px solid #ddd; padding: 10px;"><strong>Exception Type</strong></td>
        <td style="border: 1px solid #ddd; padding: 10px;">
          {', '.join(t.value for t in exception.exception_types)}
        </td>
      </tr>
    </table>

    <h3>Analysis Results</h3>
    <p><strong>Root Cause:</strong> {memo.root_cause.value}</p>
    <p><strong>Recommendation:</strong> {memo.summary}</p>
    <p><strong>Confidence:</strong> {memo.confidence:.0%}</p>

    <h3>Action Required</h3>
    <p>Please review this exception in the dashboard and take appropriate action:</p>
    <ul>
      <li><strong>Approve:</strong> Accept the exception and process the invoice</li>
      <li><strong>Reject:</strong> Reject the invoice and request correction</li>
      <li><strong>Clarify:</strong> Request additional information from supplier</li>
    </ul>

    <p>
      <a href="http://localhost:8502" style="background-color: #2196F3; color: white; padding: 12px 20px; text-decoration: none; border-radius: 4px; display: inline-block;">
        Open Dashboard
      </a>
    </p>

    <hr style="margin-top: 20px; border: none; border-top: 1px solid #ddd;">
    <p style="font-size: 12px; color: #999;">
      Exception ID: {exception.exception_id}<br>
      Generated at: {memo.generated_at.isoformat()}
    </p>
  </body>
</html>
"""

            self._send_smtp_email(subject, html_body)

        except Exception as e:
            logger.error(f"Failed to send email escalation notification: {e}")

    def _send_email_resolution(
        self,
        subject: str,
        exception: InvoiceException,
        memo: ResolutionMemo,
    ) -> None:
        """Send email notification for resolved exception.

        Args:
            subject: Email subject
            exception: The exception
            memo: Resolution memo
        """
        try:
            invoice = exception.invoice

            html_body = f"""
<html>
  <body style="font-family: Arial, sans-serif; color: #333;">
    <h2 style="color: #2e7d32;">✅ Invoice Exception Automatically Resolved</h2>

    <h3>Details</h3>
    <table style="border-collapse: collapse; width: 100%;">
      <tr style="background-color: #f5f5f5;">
        <td style="border: 1px solid #ddd; padding: 10px;"><strong>Invoice</strong></td>
        <td style="border: 1px solid #ddd; padding: 10px;">{invoice.invoice_number}</td>
      </tr>
      <tr>
        <td style="border: 1px solid #ddd; padding: 10px;"><strong>PO</strong></td>
        <td style="border: 1px solid #ddd; padding: 10px;">{exception.purchase_order.po_number}</td>
      </tr>
      <tr style="background-color: #f5f5f5;">
        <td style="border: 1px solid #ddd; padding: 10px;"><strong>Action</strong></td>
        <td style="border: 1px solid #ddd; padding: 10px;">{memo.action.value}</td>
      </tr>
      <tr>
        <td style="border: 1px solid #ddd; padding: 10px;"><strong>Confidence</strong></td>
        <td style="border: 1px solid #ddd; padding: 10px;">{memo.confidence:.0%}</td>
      </tr>
    </table>

    <hr style="margin-top: 20px; border: none; border-top: 1px solid #ddd;">
    <p style="font-size: 12px; color: #999;">
      Exception ID: {exception.exception_id}
    </p>
  </body>
</html>
"""

            self._send_smtp_email(subject, html_body)

        except Exception as e:
            logger.error(f"Failed to send email resolution notification: {e}")

    def _send_smtp_email(self, subject: str, html_body: str) -> None:
        """Send email via SMTP.

        Args:
            subject: Email subject
            html_body: HTML email body

        Raises:
            Exception: If SMTP send fails
        """
        if not self.notification_emails:
            logger.warning("No notification email addresses configured")
            return

        if not self.smtp_user or not self.smtp_password:
            logger.warning("SMTP credentials not configured; skipping email")
            return

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.smtp_from_email
            msg["To"] = ", ".join(self.notification_emails)

            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(
                    self.smtp_from_email,
                    self.notification_emails,
                    msg.as_string(),
                )

            logger.info(
                f"Email notification sent to {len(self.notification_emails)} recipient(s)"
            )

        except Exception as e:
            logger.error(f"SMTP send failed: {e}")
            raise
