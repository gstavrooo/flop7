from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FOUNDATION_FILES = (
    "README.md",
    "AGENTS.md",
    "FLOP7-SPEC.md",
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
