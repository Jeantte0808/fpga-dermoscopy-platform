"""Exercise the actual launch script with Windows PowerShell and PowerShell 7."""

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows launcher")


def invoke(shell, command, cwd, env=None):
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=20,
    )


def launch_script(shell, cwd, arguments=(), env=None):
    # Only this child process runs our locally generated fixture script; no
    # persistent execution-policy setting is changed on the user's computer.
    return subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(cwd / "start_demo.ps1"), *arguments],
        cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=20,
    )


@pytest.fixture(params=["powershell.exe", "pwsh.exe"])
def shell(request):
    executable = shutil.which(request.param)
    if executable is None:
        pytest.skip(f"{request.param} unavailable")
    return executable


@pytest.fixture
def launch_root(tmp_path):
    # Stage a minimal real venv plus a harmless fake dermademo module. The
    # launcher actually executes Python; the test never opens a desktop window.
    shutil.copy2(ROOT / "start_demo.ps1", tmp_path / "start_demo.ps1")
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(sys.executable, scripts / "python.exe")
    config = Path(sys.prefix) / "pyvenv.cfg"
    if not config.exists():
        pytest.skip("Launcher integration test requires a venv Python interpreter")
    shutil.copy2(config, scripts.parent / "pyvenv.cfg")
    module = tmp_path / "dermademo"
    module.mkdir()
    (module / "__init__.py").write_text("", encoding="ascii")
    (module / "__main__.py").write_text(
        "import json, os, pathlib, sys\n"
        "pathlib.Path('launch.json').write_text(json.dumps(sys.argv[1:]))\n"
        "sys.exit(int(os.environ.get('DEMO_LAUNCHER_TEST_EXIT', '0')))\n",
        encoding="ascii",
    )
    return tmp_path


def test_launcher_parses_in_both_powershell_versions(shell):
    path = str(ROOT / "start_demo.ps1").replace("'", "''")
    result = invoke(
        shell,
        "$tokens = $null; $parseErrors = $null; "
        f"$null = [System.Management.Automation.Language.Parser]::ParseFile('{path}', "
        "[ref]$tokens, [ref]$parseErrors); "
        "if ($parseErrors.Count) { $parseErrors | Out-String | Write-Output; exit 1 }",
        ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("arguments, expected", [((), []), (("-Empty",), []), (("-Sample",), ["--sample"])])
def test_launcher_runs_python_with_correct_arguments(shell, launch_root, arguments, expected):
    result = launch_script(shell, launch_root, arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((launch_root / "launch.json").read_text()) == expected


def test_launcher_propagates_python_exit_code(shell, launch_root):
    env = dict(os.environ, DEMO_LAUNCHER_TEST_EXIT="7")
    result = launch_script(shell, launch_root, env=env)
    assert result.returncode == 7, result.stdout + result.stderr


def test_launcher_missing_environment_has_readable_error(shell, tmp_path):
    shutil.copy2(ROOT / "start_demo.ps1", tmp_path / "start_demo.ps1")
    result = launch_script(shell, tmp_path)
    assert result.returncode != 0
    assert "Please create .venv" in result.stderr
    assert "ParserError" not in result.stderr
