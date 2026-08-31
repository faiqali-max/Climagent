"""Modular payment/subscription provider interface.

Only a local/mock provider is bundled. Real providers (Stripe, Paddle, LemonSqueezy...)
can be added later by implementing the same interface and reading their keys from env.
No payment secrets are hardcoded anywhere in this codebase.
"""
import os
import time

from lib import db, plans


class PaymentProvider:
    name = "base"

    def create_checkout(self, user, plan):
        raise NotImplementedError

    def handle_webhook(self, payload):
        raise NotImplementedError


class MockProvider(PaymentProvider):
    """Offline checkout: upgrades the user immediately. Used for demos/tests."""

    name = "manual"

    def create_checkout(self, user, plan):
        if plan not in plans.PLANS:
            raise ValueError("Unknown plan.")
        plans.set_subscription(str(user["id"]), plan, provider=self.name)
        return {"checkout_url": None, "status": "active", "plan": plan}

    def handle_webhook(self, payload):
        return {"status": "ok"}


def get_provider():
    """Return the provider configured via env. Defaults to the mock provider."""
    provider_name = os.getenv("PAYMENT_PROVIDER", "manual").strip().lower()
    if provider_name == "stripe":
        return _StripeProvider()
    return MockProvider()


class _StripeProvider(PaymentProvider):
    name = "stripe"

    def create_checkout(self, user, plan):
        raise NotImplementedError("Stripe provider requires PAYMENT_SECRET_KEY and an API client.")


def upgrade_user(user_id, plan):
    """Local/manual plan upgrade used by the admin dashboard."""
    if plan not in plans.PLANS:
        raise ValueError("Unknown plan.")
    plans.set_subscription(str(user_id), plan, provider="manual")
    return True


def webhook():
    return get_provider().handle_webhook({})


def list_subscriptions(limit=200):
    rows = db.run(
        "SELECT s.*, u.email FROM subscriptions s JOIN users u ON u.id = s.user_id "
        "ORDER BY s.id DESC LIMIT ?",
        (limit,),
    )
    return rows or []


def _now():
    return time.time()
