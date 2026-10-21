"""Provider-agnostic email sending. Domain code depends on the Protocol, never on a vendor."""

import hashlib
import logging
from typing import Protocol

logger = logging.getLogger(__name__)

EmailContext = dict[str, str]


class EmailSender(Protocol):
    async def send(self, template: str, to: str, context: EmailContext) -> None: ...


class LoggingEmailSender:
    """Dev adapter: logs the template and a hash of the recipient, never PII or tokens."""

    async def send(self, template: str, to: str, context: EmailContext) -> None:
        recipient = hashlib.sha256(to.lower().encode()).hexdigest()[:12]
        logger.info(
            "email sent template=%s to_hash=%s context_keys=%s",
            template,
            recipient,
            sorted(context),
        )
