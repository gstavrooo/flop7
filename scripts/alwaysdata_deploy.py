"""Restart the parallel alwaysdata site and wait until its public URL is healthy.

The site itself is created once in the alwaysdata admin (type ``user_program``).
This helper only finds it by address and restarts it, so a deploy always runs the
exact code that was pulled onto the remote host by the workflow.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

Api = Callable[[str, str, dict[str, Any] | None], Any]
Opener = Callable[..., Any]

DEFAULT_API_BASE = "https://api.alwaysdata.com/v1"
SITE_TYPE = "user_program"


class AlwaysDataError(RuntimeError):
    """A fatal problem talking to alwaysdata or validating its state."""


def basic_auth(token: str, account: str) -> str:
    """alwaysdata uses HTTP Basic with ``<token> account=<name>`` as the user and no password."""
    credentials = f"{token} account={account}:"
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def api_call(
    method: str,
    path: str,
    token: str,
    account: str,
    body: dict[str, Any] | None = None,
    *,
    base: str = DEFAULT_API_BASE,
) -> Any:
    """One authenticated alwaysdata API request; failures include the response body."""
    request = urllib.request.Request(
        f"{base.rstrip('/')}{path}",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers={
            "Authorization": basic_auth(token, account),
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise AlwaysDataError(f"alwaysdata API {method} {path}: HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise AlwaysDataError(f"alwaysdata API {method} {path}: {exc.reason}") from None
    return json.loads(raw) if raw else {}


def entries(payload: Any) -> list[dict[str, Any]]:
    """Normalise an alwaysdata list response (plain list or ``results`` wrapper)."""
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("results", [])
    else:
        items = []
    return [item for item in items if isinstance(item, dict)]


def address_list(site: dict[str, Any]) -> list[str]:
    """Site addresses can be a list or a single string depending on the API version."""
    value = site.get("addresses") or []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value if item]


def find_site(sites: Any, address: str) -> dict[str, Any] | None:
    """Find the site owning ``address`` in a list-sites response."""
    wanted = address.lower().removeprefix("https://").removeprefix("http://").rstrip("/")
    for site in entries(sites):
        if any(item.lower().rstrip("/") == wanted for item in address_list(site)):
            return site
    return None


def check_site(site: dict[str, Any]) -> None:
    """Refuse to restart a site that is not the uvicorn user-program site we set up."""
    site_type = site.get("type")
    if site_type not in (None, SITE_TYPE):
        raise AlwaysDataError(f"site type is {site_type!r}, expected {SITE_TYPE!r}")
    if site.get("signal_reload") not in (None, ""):
        raise AlwaysDataError("site has hot restart enabled; set signal_reload to None for uvicorn")


def service_url(address: str) -> str:
    """The public HTTPS URL for the site address."""
    url = address if "://" in address else f"https://{address}"
    if not url.startswith("https://"):
        raise AlwaysDataError(f"site has no HTTPS address: {address!r}")
    return url.rstrip("/")


def probe_health(url: str, opener: Opener = urllib.request.urlopen) -> bool:
    """One GET of /api/health; any transport or JSON error means 'not healthy yet'."""
    try:
        with opener(f"{url}/api/health", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except OSError, ValueError:
        return False
    return payload.get("ok") is True


def wait_for_health(
    url: str,
    *,
    max_polls: int = 20,
    interval: int = 3,
    sleep: Callable[[int], None] = time.sleep,
    opener: Opener = urllib.request.urlopen,
) -> None:
    """Poll the public URL until FastAPI answers healthy, or fail loudly."""
    for attempt in range(1, max_polls + 1):
        if probe_health(url, opener):
            return
        print(f"alwaysdata: waiting for health ({attempt}/{max_polls})", flush=True)
        if attempt < max_polls:
            sleep(interval)
    raise AlwaysDataError(f"{url}/api/health did not become healthy within {max_polls * interval} s")


def main() -> None:
    token = os.environ.get("ALWAYS_DATA_API_KEY")
    account = os.environ.get("ALWAYS_DATA_ACCOUNT")
    if not token or not account:
        raise AlwaysDataError("ALWAYS_DATA_API_KEY and ALWAYS_DATA_ACCOUNT must be set")
    address = os.environ.get("ALWAYS_DATA_ADDRESS") or f"{account}.alwaysdata.net"
    base = os.environ.get("ALWAYS_DATA_API_BASE", DEFAULT_API_BASE)

    def call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        return api_call(method, path, token, account, body, base=base)

    site = find_site(call("GET", "/site/"), address)
    if site is None:
        raise AlwaysDataError(f"no alwaysdata site with address {address!r}; create it in the admin first")
    check_site(site)
    site_id = site.get("id")
    if not site_id:
        raise AlwaysDataError("site has no id")

    print(f"alwaysdata: restarting site {site_id}", flush=True)
    call("POST", f"/site/{site_id}/restart/")
    url = service_url(address)
    wait_for_health(url)

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.writelines(f"site_id={site_id}\nservice_url={url}\n")
    print(f"alwaysdata: healthy at {url}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except AlwaysDataError as error:
        print(f"alwaysdata: error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
