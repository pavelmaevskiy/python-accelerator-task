"""Idempotency helpers: a stable fingerprint of what a key was first used for."""

import hashlib


def request_fingerprint(*parts: str | None) -> str:
    """Same key + different endpoint/target/body must not replay another request's response."""
    joined = "\x1f".join("" if p is None else p for p in parts)
    return hashlib.sha256(joined.encode()).hexdigest()
