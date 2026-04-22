# backend/lib/payments/stripe/live.py

from stripe import StripeClient

from env_loader import EnvLoader

from .base import AbstractBaseStripeClient


class StripeClientLive(AbstractBaseStripeClient):
    def __init__(self) -> None:
        self._client = StripeClient(
            EnvLoader.get("STRIPE_API_SK_LIVE"),
        )
