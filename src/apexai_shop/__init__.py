"""Python client for the Shop Bot merchant API, by ApexAi.

>>> from apexai_shop import Shop
>>> with Shop() as shop:                 # reads SHOP_API_URL and SHOP_API_TOKEN
...     print(shop.store().store.name)
"""

from . import models
from ._version import __version__
from .async_client import AsyncShop
from .client import Shop
from .errors import (
    AuthenticationError,
    BadRequestError,
    ConnectionError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    ShopError,
)

__all__ = [
    "__version__",
    "Shop",
    "AsyncShop",
    "models",
    "ShopError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "BadRequestError",
    "RateLimitError",
    "ServerError",
    "ConnectionError",
]
