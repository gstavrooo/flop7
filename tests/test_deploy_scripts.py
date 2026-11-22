"""Offline checks for the deploy helpers and go-live triggers (spec 9.2 and 9.3)."""

from __future__ import annotations

import base64
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Self

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render = load("render_deploy")
prune = load("prune_ghcr")
alwaysdata = load("alwaysdata_deploy")


def test_find_service_handles_wrapped_list() -> None:
    services = [
        {"service": {"id": "srv-1", "name": "other"}, "cursor": "c1"},
        {"service": {"id": "srv-2", "name": "flop7"}, "cursor": "c2"},
    ]
    assert render.find_service(services, "flop7")["id"] == "srv-2"
    assert render.find_service(services, "missing") is None


def test_credential_selection() -> None:
    credentials = [
        {"credential": {"id": "cred-1", "registry": "DOCKER"}},
        {"credential": {"id": "cred-2", "registry": "GITHUB"}},
    ]
    assert render.find_credential(credentials, None) == "cred-2"
    assert render.find_credential(credentials, "explicit") == "explicit"
    assert render.find_credential([], None) is None
    two_github = credentials + [{"credential": {"id": "cred-3", "registry": "GITHUB"}}]
    with pytest.raises(render.RenderError, match="multiple GitHub"):
        render.find_credential(two_github, None)


def test_owner_resolution() -> None:
    owners = [{"owner": {"id": "u1", "type": "user"}}, {"owner": {"id": "t1", "type": "team"}}]
    assert render.resolve_owner(None, owners, None) == "u1"
    assert render.resolve_owner({"ownerId": "srv-owner"}, owners, None) == "srv-owner"
    assert render.resolve_owner(None, owners, "explicit") == "explicit"
    with pytest.raises(render.RenderError, match="workspace"):
        render.resolve_owner(None, [{"owner": {"id": "a"}}, {"owner": {"id": "b"}}], None)


def test_build_bodies_and_config_check() -> None:
    body = render.build_create_body("owner-1", "ghcr.io/gstavrooo/flop7:abc", "cred-2")
    assert body["type"] == "web_service"
    assert body["name"] == "flop7"
    assert body["autoDeployTrigger"] == "off"
    assert body["image"]["registryCredentialId"] == "cred-2"
    assert body["serviceDetails"]["plan"] == "free"
    assert body["serviceDetails"]["region"] == "frankfurt"
    assert "registryCredentialId" not in render.image_config("owner-1", "image:tag", None)

    patch = render.build_patch_body("owner-1", "ghcr.io/gstavrooo/flop7:abc", None)
    assert patch["image"]["imagePath"] == "ghcr.io/gstavrooo/flop7:abc"
    assert patch["autoDeployTrigger"] == "off"

    good = {
        "serviceDetails": {"plan": "free", "region": "frankfurt", "runtime": "image"},
        "autoDeployTrigger": "off",
    }
    render.check_service_config(good)
    bad_region = {**good, "serviceDetails": {**good["serviceDetails"], "region": "oregon"}}
    with pytest.raises(render.RenderError, match="region"):
        render.check_service_config(bad_region)
    with pytest.raises(render.RenderError, match="auto-deploy"):
        render.check_service_config({**good, "autoDeploy": "yes"})


def test_service_url_and_wait_for_live() -> None:
    assert render.service_url({"serviceUrl": "https://flop7.onrender.com"}) == "https://flop7.onrender.com"
    assert render.service_url({"serviceDetails": {"url": "https://flop7.onrender.com"}}) == (
        "https://flop7.onrender.com"
    )
    with pytest.raises(render.RenderError, match="HTTPS URL"):
        render.service_url({})

    responses = {
        ("GET", "/services/srv-1"): {"service": {"id": "srv-1", "status": "live"}},
        ("GET", "/services/srv-1/deploys/dep-1"): {"deploy": {"id": "dep-1", "status": "live"}},
    }
    calls: list[tuple[str, str]] = []

    def api(method: str, path: str, body: dict | None) -> dict:
        calls.append((method, path))
        return responses[(method, path)]

    service = render.wait_for_live(api, "srv-1", "dep-1", sleep=lambda _: None)
    assert service["id"] == "srv-1"
    assert calls == [("GET", "/services/srv-1"), ("GET", "/services/srv-1/deploys/dep-1")]


def test_trigger_deploy_handles_created_and_queued() -> None:
    old = {"deploy": {"id": "dep-old", "status": "live"}, "cursor": "c1"}
    new = {"deploy": {"id": "dep-new", "status": "created"}, "cursor": "c2"}
    image = "ghcr.io/gstavrooo/flop7:abc"

    def with_body(method: str, path: str, body: dict | None) -> dict:
        if method == "POST":
            assert body == {"imageUrl": image}
            return {"deploy": {"id": "dep-body"}}
        return [old]

    assert render.trigger_deploy(with_body, "srv-1", image) == "dep-body"

    calls = {"list": 0}

    def queued(method: str, path: str, body: dict | None) -> dict | list:
        if method == "POST":
            assert body == {"imageUrl": image}
            return {}  # Render 202 Queued has no response body
        calls["list"] += 1
        return [old] if calls["list"] == 1 else [new]

    assert render.trigger_deploy(queued, "srv-1", image, sleep=lambda _: None) == "dep-new"
    assert calls["list"] == 2

    def stuck(method: str, path: str, body: dict | None) -> dict | list:
        return {} if method == "POST" else [old]

    with pytest.raises(render.RenderError, match="no new deploy"):
        render.trigger_deploy(stuck, "srv-1", image, max_polls=2, interval=1, sleep=lambda _: None)


def test_wait_for_live_failure_and_timeout() -> None:
    def failing(method: str, path: str, body: dict | None) -> dict:
        if path.endswith("/deploys/dep-1"):
            return {"deploy": {"status": "build_failed"}}
        return {"service": {"status": "live"}}

    with pytest.raises(render.RenderError, match="build_failed"):
        render.wait_for_live(failing, "srv-1", "dep-1", sleep=lambda _: None)

    def pending(method: str, path: str, body: dict | None) -> dict:
        if path.endswith("/deploys/dep-1"):
            return {"deploy": {"status": "update_in_progress"}}
        return {"service": {"status": "update_in_progress"}}

    with pytest.raises(render.RenderError, match="did not reach live"):
        render.wait_for_live(pending, "srv-1", "dep-1", max_polls=2, interval=1, sleep=lambda _: None)


def test_prune_keeps_newest_and_protects_current_tag() -> None:
    versions = [
        {"id": "v1", "created_at": "2026-11-19T10:00:00Z", "metadata": {"container": {"tags": ["old"]}}},
        {"id": "v2", "created_at": "2026-11-19T09:00:00Z", "metadata": {"container": {"tags": []}}},
        {"id": "v3", "created_at": "2026-11-19T08:00:00Z", "metadata": {"container": {"tags": []}}},
        {"id": "v4", "created_at": "2026-11-19T07:00:00Z", "metadata": {"container": {"tags": ["current"]}}},
    ]
    assert prune.tags_of(versions[0]) == {"old"}
    assert prune.select_versions(versions, 2) == ["v3", "v4"]
    assert prune.select_versions(versions, 2, frozenset({"current"})) == ["v3"]
    assert prune.select_versions(versions, 3) == ["v4"]


def test_deploy_workflow_go_live_triggers() -> None:
    """Phase 7: push-on-merge, manual dispatch and the daily Dependabot catch-up must stay set."""
    workflow = (SCRIPTS.parent / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    assert "branches: [main]" in workflow
    assert 'paths-ignore: ["**.md"]' in workflow
    assert "workflow_dispatch:" in workflow
    assert '- cron: "23 5 * * *"' in workflow


def test_alwaysdata_basic_auth() -> None:
    header = alwaysdata.basic_auth("TOKEN", "gstavrooo")
    decoded = base64.b64decode(header.removeprefix("Basic ")).decode("utf-8")
    assert decoded == "TOKEN account=gstavrooo:"


def test_alwaysdata_find_site_by_address() -> None:
    sites = [
        {"id": 1, "type": "php", "addresses": ["other.alwaysdata.net"]},
        {"id": 2, "type": "user_program", "addresses": ["gstavrooo.alwaysdata.net"]},
    ]
    assert alwaysdata.find_site(sites, "gstavrooo.alwaysdata.net")["id"] == 2
    assert alwaysdata.find_site({"results": sites}, "https://gstavrooo.alwaysdata.net/")["id"] == 2
    assert alwaysdata.find_site(sites, "missing.alwaysdata.net") is None


def test_alwaysdata_check_site_rejects_wrong_config() -> None:
    alwaysdata.check_site({"type": "user_program", "signal_reload": None})
    with pytest.raises(alwaysdata.AlwaysDataError, match="site type"):
        alwaysdata.check_site({"type": "wsgi"})
    with pytest.raises(alwaysdata.AlwaysDataError, match="hot restart"):
        alwaysdata.check_site({"type": "user_program", "signal_reload": "SIGHUP"})


def test_alwaysdata_service_url_requires_https() -> None:
    assert alwaysdata.service_url("gstavrooo.alwaysdata.net") == "https://gstavrooo.alwaysdata.net"
    assert alwaysdata.service_url("https://gstavrooo.alwaysdata.net/") == "https://gstavrooo.alwaysdata.net"
    with pytest.raises(alwaysdata.AlwaysDataError, match="HTTPS"):
        alwaysdata.service_url("http://gstavrooo.alwaysdata.net")


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_alwaysdata_health_poll_succeeds_and_rejects_bad_json() -> None:
    """The opener must receive timeout as a keyword: urlopen's second positional arg is data."""
    responses = [b'{"ok": false}', b'{"ok": true}']
    calls: list[str] = []

    def opener(url: str, *, timeout: float) -> FakeResponse:
        assert timeout == 10
        calls.append(url)
        return FakeResponse(responses.pop(0))

    url = "https://gstavrooo.alwaysdata.net"
    alwaysdata.wait_for_health(url, max_polls=2, interval=1, sleep=lambda _: None, opener=opener)
    assert calls == [f"{url}/api/health"] * 2
    assert alwaysdata.probe_health(url, lambda _u, *, timeout: FakeResponse(b"not json")) is False


def test_alwaysdata_health_poll_times_out() -> None:
    def opener(_url: str, *, timeout: float) -> FakeResponse:
        return FakeResponse(b'{"ok": false}')

    with pytest.raises(alwaysdata.AlwaysDataError, match="did not become healthy"):
        alwaysdata.wait_for_health(
            "https://gstavrooo.alwaysdata.net",
            max_polls=2,
            interval=1,
            sleep=lambda _: None,
            opener=opener,
        )


def test_alwaysdata_workflow_is_dispatch_only() -> None:
    """The live host must never deploy on a push to main."""
    workflow = (SCRIPTS.parent / ".github/workflows/deploy-alwaysdata.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "branches: [main]" not in workflow
    assert "ALWAYS_DATA_API_KEY" in workflow


def test_render_deploy_steps_are_paused_but_kept() -> None:
    """Render deployment is paused without deleting its setup."""
    workflow = (SCRIPTS.parent / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    assert workflow.count("vars.RENDER_DEPLOY_ENABLED == 'true'") == 4
    assert (SCRIPTS / "render_deploy.py").is_file()
    assert "RENDER_API_KEY" in workflow
