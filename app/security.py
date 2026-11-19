"""Seat tokens and response hardening (spec sections 4.4 and 5)."""

from __future__ import annotations

import hashlib
import hmac
import secrets

# Transport: URL fragment only (never sent to the server or written to logs).
TOKEN_BYTES = 16
GAME_ID_BYTES = 12

TOKEN_REQUIRED = "this device does not control that seat"
GAME_NOT_FOUND = "game not found"
STALE_WRITE = "game changed in the meantime, reload and try again"
BAD_NAMES = "each name must be 1 to 20 characters after trimming"

# A strict CSP for a no-build static app: external scripts/styles are never injected.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


def new_game_id() -> str:
    return secrets.token_urlsafe(GAME_ID_BYTES)


def new_seat_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """SHA-256 hex digest — the only representation ever stored."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: str, expected_hash: str | None) -> bool:
    """Constant-time comparison; a missing seat still does the digest work."""
    digest = hash_token(token)
    return hmac.compare_digest(digest, expected_hash or "0" * 64)
