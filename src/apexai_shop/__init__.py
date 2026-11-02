"""Python client for the Shop Bot merchant API, by ApexAi.

>>> from apexai_shop import Shop
>>> with Shop() as shop:                 # reads SHOP_API_URL and SHOP_API_TOKEN
...     print(shop.store().store.name)
"""

from . import models
from ._version import __version__
from .async_client import AsyncShop
from .client import Shop
from .results import (
    AsyncConfirmationRequired,
    Completed,
    ConfirmationRequired,
    PendingApproval,
    WriteResult,
)
from .webhooks import WebhookEvent, WebhookVerificationError, verify_webhook
from .errors import (
    ApprovalRequiredError,
    AuthenticationError,
    BadRequestError,
    ChangedError,
    ConfirmKeyDeliveryError,
    ConfirmationInvalidError,
    ConflictError,
    ConnectionError,
    DailyCapError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    ShopError,
    StoreSuspendedError,
    WritesDisabledError,
)

__all__ = [
    "__version__",
    "Shop",
    "AsyncShop",
    "models",
    "WriteResult",
    "Completed",
    "ConfirmationRequired",
    "AsyncConfirmationRequired",
    "PendingApproval",
    "ShopError",
    "AuthenticationError",
    "PermissionDeniedError",
    "ApprovalRequiredError",
    "WritesDisabledError",
    "NotFoundError",
    "BadRequestError",
    "ConflictError",
    "ChangedError",
    "ConfirmKeyDeliveryError",
    "ConfirmationInvalidError",
    "StoreSuspendedError",
    "RateLimitError",
    "DailyCapError",
    "ServerError",
    "ConnectionError",
    "verify_webhook",
    "WebhookEvent",
    "WebhookVerificationError",
]
