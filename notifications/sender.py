"""Send notifications via Slack and Email."""
from __future__ import annotations

import logging
import json
from datetime import datetime, timezone

from notifications.models import Notification, NotificationChannel, NotificationEvent

logger = logging.getLogger(__name__)


class SlackNotifier:
    """Send notifications via Slack."""

    def __init__(self, webhook_url: str | None = None):
        """Initialize with Slack webhook URL."""
        self.webhook_url = webhook_url

    async def send(self, notification: Notification) -> bool:
        """Send notification to Slack."""
        if not self.webhook_url:
            logger.warning("Slack webhook URL not configured")
            return False

        try:
            import aiohttp

            message = self._build_slack_message(notification)

            async with aiohttp.ClientSession() as session:
                async with session.post(self.webhook_url, json=message) as response:
                    if response.status == 200:
                        logger.info(f"Slack notification sent to {notification.recipient}")
                        return True
                    else:
                        logger.error(f"Slack error: {response.status}")
                        return False

        except Exception as e:
            logger.error(f"Error sending Slack notification: {e}")
            return False

    def _build_slack_message(self, notification: Notification) -> dict:
        """Build Slack message format."""
        return {
            "text": notification.subject,
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": notification.subject,
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": notification.message,
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "View Details",
                            },
                            "url": notification.action_url or "#",
                        },
                    ],
                } if notification.action_url else None,
            ],
        }


class EmailNotifier:
    """Send notifications via Email."""

    def __init__(self, smtp_host: str | None = None, smtp_port: int = 587):
        """Initialize with SMTP configuration."""
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port

    async def send(self, notification: Notification) -> bool:
        """Send notification via Email."""
        if not self.smtp_host:
            logger.warning("SMTP host not configured")
            return False

        try:
            # In production, use aiosmtplib or similar
            # For now, log the email
            logger.info(
                f"Email notification to {notification.recipient}: "
                f"Subject='{notification.subject}' Message='{notification.message}'"
            )
            return True

        except Exception as e:
            logger.error(f"Error sending email notification: {e}")
            return False

    def _build_email_html(self, notification: Notification) -> str:
        """Build HTML email template."""
        return f"""
        <html>
            <body style="font-family: Arial, sans-serif;">
                <h2>{notification.subject}</h2>
                <p>{notification.message}</p>
                {f'<a href="{notification.action_url}" style="background-color: #4CAF50; color: white; padding: 10px 20px; text-decoration: none;">View Details</a>' if notification.action_url else ''}
            </body>
        </html>
        """


class NotificationService:
    """Centralized notification dispatch."""

    def __init__(self, slack_webhook: str | None = None, smtp_host: str | None = None):
        """Initialize with Slack and Email configurations."""
        self.slack = SlackNotifier(slack_webhook)
        self.email = EmailNotifier(smtp_host)

    async def notify_escalation(self, exception_id: str, invoice_num: str, variance: float, recipient_email: str) -> None:
        """Send escalation notification."""
        notification = Notification(
            exception_id=exception_id,
            recipient=recipient_email,
            channel=NotificationChannel.SLACK,
            event_type=NotificationEvent.ESCALATION,
            subject=f"⚠️ Exception Escalated: {invoice_num}",
            message=f"Invoice {invoice_num} requires approval.\nVariance: ${variance:,.2f}",
            action_url=f"/dashboard?exception={exception_id}",
        )

        await self.slack.send(notification)

    async def notify_sla_breach(self, exception_id: str, hours_overdue: int, recipient_email: str) -> None:
        """Send SLA breach notification."""
        notification = Notification(
            exception_id=exception_id,
            recipient=recipient_email,
            channel=NotificationChannel.SLACK,
            event_type=NotificationEvent.SLA_BREACH,
            subject=f"🚨 SLA Breach: {hours_overdue}h Overdue",
            message=f"Exception {exception_id} has exceeded 24-hour SLA by {hours_overdue}h.",
            action_url=f"/dashboard?exception={exception_id}",
        )

        await self.slack.send(notification)

    async def send_daily_summary(self, recipient_email: str, pending_count: int, approved_count: int) -> None:
        """Send daily summary email."""
        notification = Notification(
            recipient=recipient_email,
            channel=NotificationChannel.EMAIL,
            event_type=NotificationEvent.APPROVAL,
            subject=f"Daily Summary: {pending_count} Pending | {approved_count} Approved",
            message=f"""
            Good morning,

            Your approval queue status:
            - Pending Approval: {pending_count}
            - Approved Today: {approved_count}

            Log in to review pending exceptions.
            """,
        )

        await self.email.send(notification)
