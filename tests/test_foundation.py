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
)


def test_foundation_files_exist():
    for name in FOUNDATION_FILES:
        assert (ROOT / name).is_file(), name


def test_lockfiles_are_hashed():
    for name in ("requirements.txt", "requirements-dev.txt"):
        assert "--hash=sha256:" in (ROOT / name).read_text(encoding="utf-8"), name
