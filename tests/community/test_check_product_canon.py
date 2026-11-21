"""Tests for scripts/check-product-canon.py.

The checker excludes build and vendor folders by name. Only folders inside
the repository may count: the folders the checkout itself sits in are not
the repo's content.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check-product-canon.py"


def _make_repo(parent: Path) -> Path:
    repo = parent / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "scripts" / SCRIPT.name)
    (repo / "docs").mkdir()
    (repo / "docs" / "roster.md").write_text("We ship 15 products.\n", encoding="utf-8")
    return repo


def _run(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / SCRIPT.name)],
        capture_output=True, text=True, timeout=60,
    )


@pytest.mark.parametrize("parent", ["work", "build", "dist", "venv", "node_modules"])
def test_violation_found_whatever_the_checkout_lives_under(tmp_path, parent):
    repo = _make_repo(tmp_path / parent)
    proc = _run(repo)
    assert proc.returncode == 1, proc.stdout
    assert "Scanning 1 file(s)" in proc.stdout
    assert "docs/roster.md:1" in proc.stdout.replace("\\", "/")


def test_build_folder_inside_the_repo_is_still_skipped(tmp_path):
    repo = _make_repo(tmp_path / "work")
    (repo / "docs" / "roster.md").write_text("Thirteen products.\n", encoding="utf-8")
    (repo / "build").mkdir()
    (repo / "build" / "out.md").write_text("We ship 15 products.\n", encoding="utf-8")
    proc = _run(repo)
    assert proc.returncode == 0, proc.stdout
    assert "Scanning 1 file(s)" in proc.stdout


def test_symlink_in_excluded_folder_pointing_outside_is_skipped(tmp_path):
    repo = _make_repo(tmp_path / "work")
    (repo / "docs" / "roster.md").write_text("Thirteen products.\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("We ship 15 products.\n", encoding="utf-8")
    (repo / "node_modules").mkdir()
    try:
        (repo / "node_modules" / "x.md").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    proc = _run(repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Scanning 1 file(s)" in proc.stdout
