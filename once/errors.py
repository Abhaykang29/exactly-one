"""Exceptions raised by the layer and by the tool clients."""


class Denied(Exception):
    """Permission check failed (grant missing, revoked, expired, tool, per-action cap, budget)."""


class Stopped(Exception):
    """The kill switch fired: the grant was revoked."""


class SchemaError(Exception):
    """A system rejected the request shape (HTTP 422)."""

    def __init__(self, body: dict | None = None):
        self.body = body or {}
        super().__init__(f"schema error: {self.body}")


class Unknown(Exception):
    """Timeout or 5xx: we do not know if the action landed. verify() decides."""


class NeedsHuman(Exception):
    """The decision must not be executed automatically (e.g. the LLM proposed more than the order amount).
    run_effect records a `held` receipt with this reason and never acts."""

    def __init__(self, reason: str, decision: dict | None = None):
        self.reason = reason
        self.decision = decision or {}
        super().__init__(reason)
