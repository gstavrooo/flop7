"""Keep only the newest GHCR image versions for this repository (spec section 9.2).

Free private package storage is roughly 500 MB, so the deploy run deletes old,
untagged versions after the new image is live. The tag being deployed is never
deleted, and neither are explicitly tagged versions.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

API_BASE = "https://api.github.com"
DEFAULT_KEEP = 3


class PruneError(RuntimeError):
    """A fatal problem talking to GitHub's package API."""


def request(method: str, path: str, token: str, *, base: str = API_BASE) -> Any:
    req = urllib.request.Request(
        f"{base.rstrip('/')}{path}",
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "flop7-image-prune",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise PruneError(f"GitHub API {method} {path}: HTTP {exc.code}: {detail}") from None
    except urllib.error.URLError as exc:
        raise PruneError(f"GitHub API {method} {path}: {exc.reason}") from None
    return json.loads(raw) if raw else {}


def tags_of(version: dict[str, Any]) -> set[str]:
    return set(version.get("metadata", {}).get("container", {}).get("tags", []) or [])


def select_versions(
    versions: list[dict[str, Any]],
    keep: int = DEFAULT_KEEP,
    protected: frozenset[str] = frozenset(),
) -> list[str]:
    """IDs to delete: everything older than `keep` except protected tags."""
    ordered = sorted(versions, key=lambda version: version.get("created_at", ""), reverse=True)
    keep_ids = {version["id"] for version in ordered[:keep]}
    keep_ids.update(version["id"] for version in ordered if tags_of(version) & protected)
    return [version["id"] for version in ordered if version["id"] not in keep_ids]


def main() -> None:
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise PruneError("GH_TOKEN is not set")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    sha = os.environ.get("GITHUB_SHA", "")
    if not repository or not sha:
        raise PruneError("GITHUB_REPOSITORY and GITHUB_SHA must be set")
    package = repository.rsplit("/", 1)[-1].lower()
    keep = int(os.environ.get("IMAGE_KEEP", str(DEFAULT_KEEP)))

    path = f"/user/packages/container/{package}/versions?per_page=100"
    versions = request("GET", path, token)
    if not isinstance(versions, list):
        raise PruneError(f"unexpected versions response: {type(versions).__name__}")
    to_delete = select_versions(versions, keep, frozenset({sha}))
    if not to_delete:
        print(f"prune: nothing to delete ({len(versions)} versions)", flush=True)
        return
    for version_id in to_delete:
        request("DELETE", f"/user/packages/container/{package}/versions/{version_id}", token)
    print(f"prune: deleted {len(to_delete)} old version(s), kept {keep} newest", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (PruneError, ValueError) as error:
        print(f"prune: error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
