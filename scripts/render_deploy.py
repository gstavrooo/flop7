"""Phase 6 deploy helper: create/patch the Render service and ship the tested tag.

The image reference comes from the GitHub-provided GITHUB_REPOSITORY/GITHUB_SHA
variables, never from a workflow expression inside a `run:` block (spec 9.2 step 7).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

Api = Callable[[str, str, dict[str, Any] | None], Any]

DEFAULT_API_BASE = "https://api.render.com/v1"
SERVICE_NAME = "flop7"
PLAN = "free"
REGION = "frankfurt"
FAILED_DEPLOY_STATUSES = frozenset(
    {"failed", "build_failed", "update_failed", "pre_deploy_failed", "canceled", "deactivated"}
)


class RenderError(RuntimeError):
    """A fatal problem talking to Render or validating its state."""


def api_call(
    method: str,
    path: str,
    token: str,
    body: dict[str, Any] | None = None,
    *,
    base: str = DEFAULT_API_BASE,
) -> Any:
    """One authenticated Render API request; failures include the response body."""
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{base.rstrip('/')}{path}",
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RenderError(f"Render API {method} {path}: HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise RenderError(f"Render API {method} {path}: {exc.reason}") from None
    return json.loads(raw) if raw else {}


def unwrap(payload: Any) -> Any:
    """Render wraps list entries and some responses in a named object."""
    if isinstance(payload, dict):
        for key in ("service", "credential", "deploy", "owner"):
            value = payload.get(key)
            if isinstance(value, dict):
                return value
    return payload


def entries(payload: Any) -> list[dict[str, Any]]:
    """Normalise a Render list response into plain objects."""
    if not isinstance(payload, list):
        return []
    return [unwrap(item) for item in payload if isinstance(item, (dict, list))]


def find_service(services: Any, name: str) -> dict[str, Any] | None:
    """Find the named service in a List services response."""
    for service in entries(services):
        if service.get("name") == name:
            return service
    return None


def find_credential(credentials: Any, explicit: str | None) -> str | None:
    """Pick the GitHub registry credential for a private image, if needed."""
    if explicit:
        return explicit
    github = [item for item in entries(credentials) if item.get("registry") == "GITHUB"]
    if len(github) > 1:
        raise RenderError("multiple GitHub registry credentials; set RENDER_REGISTRY_CREDENTIAL_ID")
    return github[0]["id"] if github else None


def resolve_owner(service: dict[str, Any] | None, owners: Any, explicit: str | None) -> str:
    """The workspace ID: the existing service, an explicit variable, or a single workspace."""
    if explicit:
        return explicit
    if service and service.get("ownerId"):
        return service["ownerId"]
    workspaces = entries(owners)
    users = [owner for owner in workspaces if owner.get("type") == "user"]
    candidates = users or workspaces
    if len(candidates) != 1:
        raise RenderError("cannot pick a Render workspace; set RENDER_OWNER_ID")
    return candidates[0]["id"]


def check_service_config(service: dict[str, Any]) -> None:
    """Refuse to deploy into a service that is not the spec'd free/Frankfurt one."""
    details = service.get("serviceDetails", {})
    for key, expected in (("plan", PLAN), ("region", REGION), ("runtime", "image")):
        actual = details.get(key)
        if actual is not None and actual != expected:
            raise RenderError(
                f"service {key} is {actual!r}, expected {expected!r}; fix it manually in Render"
            )
    if service.get("autoDeployTrigger") not in (None, "off") or service.get("autoDeploy") not in (None, "no"):
        raise RenderError("auto-deploy is enabled on the service; disable it in Render before deploying")


def image_config(owner_id: str, image_url: str, credential_id: str | None) -> dict[str, Any]:
    """The Render image object; the credential is optional for public images."""
    config: dict[str, Any] = {"ownerId": owner_id, "imagePath": image_url}
    if credential_id:
        config["registryCredentialId"] = credential_id
    return config


def build_create_body(owner_id: str, image_url: str, credential_id: str | None) -> dict[str, Any]:
    return {
        "type": "web_service",
        "name": SERVICE_NAME,
        "ownerId": owner_id,
        "autoDeployTrigger": "off",
        "image": image_config(owner_id, image_url, credential_id),
        "serviceDetails": {
            "runtime": "image",
            "plan": PLAN,
            "region": REGION,
            "healthCheckPath": "/api/health",
        },
    }


def build_patch_body(owner_id: str, image_url: str, credential_id: str | None) -> dict[str, Any]:
    return {"image": image_config(owner_id, image_url, credential_id), "autoDeployTrigger": "off"}


def service_url(service: dict[str, Any]) -> str:
    """The public URL: current API uses serviceDetails.url, older one serviceUrl."""
    url = service.get("serviceUrl") or service.get("serviceDetails", {}).get("url")
    if not url or not str(url).startswith("https://"):
        raise RenderError(f"service has no public HTTPS URL: {url!r}")
    return str(url)


def wait_for_live(
    api: Api,
    service_id: str,
    deploy_id: str,
    *,
    max_polls: int = 60,
    interval: int = 15,
    sleep: Callable[[int], None] = time.sleep,
) -> dict[str, Any]:
    """Poll service + the exact deploy until both say live; fail loudly on failure."""
    for attempt in range(1, max_polls + 1):
        service = unwrap(api("GET", f"/services/{service_id}", None))
        deploy = unwrap(api("GET", f"/services/{service_id}/deploys/{deploy_id}", None))
        status = deploy.get("status")
        if status == "live" and service.get("status", "live") == "live":
            return service
        if status in FAILED_DEPLOY_STATUSES:
            raise RenderError(f"deploy {deploy_id} failed with status {status!r}")
        print(f"render: deploy {status or 'pending'} ({attempt}/{max_polls})", flush=True)
        if attempt < max_polls:
            sleep(interval)
    raise RenderError(f"deploy {deploy_id} did not reach live within {max_polls * interval} s")


def main() -> None:
    token = os.environ.get("RENDER_API_KEY")
    if not token:
        raise RenderError("RENDER_API_KEY is not set")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    sha = os.environ.get("GITHUB_SHA", "")
    if not repository or not sha:
        raise RenderError("GITHUB_REPOSITORY and GITHUB_SHA must be set")
    image_url = f"ghcr.io/{repository.lower()}:{sha}"
    name = os.environ.get("SERVICE_NAME", SERVICE_NAME)
    base = os.environ.get("RENDER_API_BASE", DEFAULT_API_BASE)

    def call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        return api_call(method, path, token, body, base=base)

    existing = find_service(call("GET", "/services?limit=100", None), name)
    credential_id = find_credential(
        call("GET", "/registrycredentials?limit=100", None),
        os.environ.get("RENDER_REGISTRY_CREDENTIAL_ID"),
    )
    owner_id = resolve_owner(
        existing,
        call("GET", "/owners?limit=100", None),
        os.environ.get("RENDER_OWNER_ID"),
    )

    if existing:
        check_service_config(existing)
        service_id = existing["id"]
        print(f"render: patching service {service_id} to {image_url}", flush=True)
        call("PATCH", f"/services/{service_id}", build_patch_body(owner_id, image_url, credential_id))
    else:
        created = unwrap(call("POST", "/services", build_create_body(owner_id, image_url, credential_id)))
        service_id = created["id"]
        print(f"render: created service {service_id}", flush=True)

    triggered = unwrap(call("POST", f"/services/{service_id}/deploys?trigger=true", {"imageUrl": image_url}))
    deploy_id = triggered["id"]
    service = wait_for_live(call, service_id, deploy_id)
    url = service_url(service)

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.writelines(
                f"{key}={value}\n"
                for key, value in {
                    "service_id": service_id,
                    "service_url": url,
                    "image_url": image_url,
                    "deploy_id": deploy_id,
                    "dashboard_url": service.get("dashboardUrl", ""),
                }.items()
            )
    print(f"render: live at {url}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except RenderError as error:
        print(f"render: error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
