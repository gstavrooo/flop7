import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The rebuild-package docs (FLOP7-SPEC.md, 00-READ-ME-FIRST.md, PHASE-*.md) are
# deliberately gitignored as private to the rebuild machine, so CI cannot assert
# their presence. Only published, tracked files belong here.
FOUNDATION_FILES = (
    "README.md",
    "AGENTS.md",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    ".github/workflows/ci.yml",
    "SECURITY.md",
)


def test_foundation_files_exist():
    for name in FOUNDATION_FILES:
        assert (ROOT / name).is_file(), name


def test_lockfiles_are_hashed():
    for name in ("requirements.txt", "requirements-dev.txt"):
        assert "--hash=sha256:" in (ROOT / name).read_text(encoding="utf-8"), name


def test_gitleaks_allowlist_covers_smoke_token():
    """CI scans full history, so the smoke script's deliberate bad token must stay allowlisted."""
    config = tomllib.loads((ROOT / "gitleaks.toml").read_text(encoding="utf-8"))
    smoke = (ROOT / "scripts/smoke.sh").read_text(encoding="utf-8")
    allowlists = [
        item for item in config.get("allowlists", []) if "scripts/smoke.sh" in item.get("paths", [])
    ]
    assert allowlists, "no gitleaks allowlist for scripts/smoke.sh"
    patterns = [re.compile(pattern) for pattern in allowlists[0]["regexes"]]
    literals = re.findall(r'FOREIGN_TOKEN="([^"]+)"', smoke)
    assert literals, "no deliberate foreign-token literal found in smoke.sh"
    for literal in literals:
        assert any(pattern.search(literal) for pattern in patterns), f"{literal} is not allowlisted"
