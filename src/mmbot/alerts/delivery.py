from __future__ import annotations

import asyncio
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from enum import Enum
from typing import Any

import httpx


class AlertSeverity(str, Enum):
    info = "info"
    warning = "warning"
    critical = "critical"
    emergency = "emergency"


@dataclass(frozen=True)
class AlertMessage:
    severity: AlertSeverity
    title: str
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeliveryResult:
    channel: str
    delivered: bool
    response: str


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    async def send(self, alert: AlertMessage) -> DeliveryResult:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        text = f"[{alert.severity.value.upper()}] {alert.title}\n{alert.body}"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True})
        return DeliveryResult("telegram", response.status_code < 400, response.text[:500])


class DiscordNotifier:
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def send(self, alert: AlertMessage) -> DeliveryResult:
        payload = {"content": f"**[{alert.severity.value.upper()}] {alert.title}**\n{alert.body}"}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(self.webhook_url, json=payload)
        return DeliveryResult("discord", response.status_code < 400, response.text[:500])


class EmailNotifier:
    def __init__(self, smtp_host: str, smtp_port: int, username: str, password: str, sender: str, recipients: list[str], use_tls: bool = True):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.sender = sender
        self.recipients = recipients
        self.use_tls = use_tls

    async def send(self, alert: AlertMessage) -> DeliveryResult:
        return await asyncio.to_thread(self._send_sync, alert)

    def _send_sync(self, alert: AlertMessage) -> DeliveryResult:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message["Subject"] = f"[{alert.severity.value.upper()}] {alert.title}"
        message.set_content(alert.body)
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as smtp:
            if self.use_tls:
                smtp.starttls()
            smtp.login(self.username, self.password)
            smtp.send_message(message)
        return DeliveryResult("email", True, "sent")


class AlertRouter:
    def __init__(self):
        self.routes: dict[AlertSeverity, list[Any]] = {severity: [] for severity in AlertSeverity}

    def add_route(self, minimum_severity: AlertSeverity, notifier: Any) -> None:
        order = list(AlertSeverity)
        min_index = order.index(minimum_severity)
        for severity in order[min_index:]:
            self.routes[severity].append(notifier)

    async def route(self, alert: AlertMessage) -> list[DeliveryResult]:
        notifiers = self.routes.get(alert.severity, [])
        results: list[DeliveryResult] = []
        for notifier in notifiers:
            try:
                results.append(await notifier.send(alert))
            except Exception as exc:
                results.append(DeliveryResult(notifier.__class__.__name__, False, str(exc)))
        return results


class AlertEscalationPolicy:
    def __init__(self, critical_after_failures: int = 2):
        self.critical_after_failures = critical_after_failures
        self.failures: dict[str, int] = {}

    def escalate(self, alert: AlertMessage, delivery_results: list[DeliveryResult]) -> AlertMessage:
        failed = [result for result in delivery_results if not result.delivered]
        if not failed:
            return alert
        key = alert.title
        self.failures[key] = self.failures.get(key, 0) + len(failed)
        if self.failures[key] >= self.critical_after_failures and alert.severity in {AlertSeverity.info, AlertSeverity.warning}:
            return AlertMessage(AlertSeverity.critical, alert.title, f"Escalated after delivery failures.\n{alert.body}", alert.metadata)
        return alert
