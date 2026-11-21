"""Unit tests for the install-eosim composite action's tag resolver.

Covers ``.github/actions/install-eosim/resolve_eosim_tag.py`` — the
fail-closed tag normalization/validation that replaces the dead
``EOSIM_VERSION: "0.1.0"`` pin family. No network access: remote checks are
tested via monkeypatched ``tag_exists``.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE = (
    REPO_ROOT / ".github" / "actions" / "install-eosim" / "resolve_eosim_tag.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("resolve_eosim_tag", MODULE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["resolve_eosim_tag"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    if not MODULE.exists():
        pytest.skip(f"{MODULE} not found")
    return _load()


def test_normalize_accepts_bare_and_v_prefixed(mod):
    assert mod.normalize_tag("3.0.2") == "v3.0.2"
    assert mod.normalize_tag("v3.0.2") == "v3.0.2"
    assert mod.normalize_tag("  v1.5.0\n") == "v1.5.0"


def test_normalize_rejects_non_tag_shapes(mod):
    for bad in [
        "", "v3", "3.0", "v3.0.2.1", "latest", "v3.0.2-rc1", "vX.Y.Z",
        "../../etc/passwd", "v3.0.2; rm -rf /",
    ]:
        with pytest.raises(ValueError):
            mod.normalize_tag(bad)


def test_resolve_without_remote_check_is_pure(mod):
    assert mod.resolve("3.0.2") == "v3.0.2"


def test_resolve_remote_failure_is_fail_closed(mod, monkeypatch):
    monkeypatch.setattr(mod, "tag_exists", lambda tag, upstream=None: False)
    with pytest.raises(ValueError, match="no such tag"):
        mod.resolve("0.1.0", check_remote=True)


def test_resolve_remote_success(mod, monkeypatch):
    monkeypatch.setattr(mod, "tag_exists", lambda tag, upstream=None: True)
    assert mod.resolve("v3.0.2", check_remote=True) == "v3.0.2"


def test_cli_rejects_bad_tag(mod, capsys):
    assert mod.main(["not-a-tag"]) == 1
    assert "must look like vX.Y.Z" in capsys.readouterr().err


def test_cli_prints_resolved_tag(mod, capsys, monkeypatch):
    monkeypatch.setattr(mod, "tag_exists", lambda tag, upstream=None: True)
    assert mod.main(["3.0.2", "--check-remote"]) == 0
    assert capsys.readouterr().out.strip() == "v3.0.2"


ACTION = REPO_ROOT / ".github" / "actions" / "install-eosim" / "action.yml"

# Stands in for curl: GitHub answers a release-asset URL with a 302 to its
# storage host, so the status is 200 only when curl follows redirects (-L).
# A wheel that does not exist is a 404 either way.
FAKE_CURL = """#!/bin/sh
follow=no
for arg in "$@"; do
  case "$arg" in
    -L|--location) follow=yes ;;
  esac
done
if [ -n "$NO_WHEEL" ]; then
  printf 404
elif [ "$follow" = yes ]; then
  printf 200
else
  printf 302
fi
"""

FAKE_PIP = """#!/bin/sh
printf '%s\\n' "$*" >> "$PIP_LOG"
"""


def _wheel_step_script(tag):
    yaml = pytest.importorskip("yaml")
    steps = yaml.safe_load(ACTION.read_text(encoding="utf-8"))["runs"]["steps"]
    (step,) = [s for s in steps if s.get("name") == "Install EoSim wheel"]
    return step["run"].replace("${{ steps.resolve.outputs.tag }}", tag)


def _run_wheel_step(tmp_path, tag, wheel_exists=True):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("curl", FAKE_CURL), ("pip", FAKE_PIP)):
        tool = bin_dir / name
        tool.write_text(body, encoding="utf-8")
        tool.chmod(0o755)
    pip_log = tmp_path / "pip.log"
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", PIP_LOG=str(pip_log))
    env.pop("NO_WHEEL", None)
    if not wheel_exists:
        env["NO_WHEEL"] = "1"
    proc = subprocess.run(
        [bash, "-e", "-c", _wheel_step_script(tag)],
        capture_output=True, text=True, env=env, timeout=30,
    )
    calls = pip_log.read_text(encoding="utf-8") if pip_log.exists() else ""
    return proc, calls


def test_wheel_mode_follows_the_release_redirect(tmp_path):
    proc, calls = _run_wheel_step(tmp_path, "v3.0.2")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "eosim-3.0.2-py3-none-any.whl" in calls


def test_wheel_mode_still_fails_closed_without_a_wheel(tmp_path):
    proc, calls = _run_wheel_step(tmp_path, "v3.0.2", wheel_exists=False)
    assert proc.returncode == 1
    assert "HTTP 404" in proc.stdout
    assert calls == ""
