"""Check actual preparation launcher in both Windows PowerShell versions."""

import json
from pathlib import Path
import shutil
import subprocess

from test_launcher import ROOT, invoke, launch_root, pytestmark, shell


def test_preparation_launcher_parses(shell):
    path = str(ROOT / "prepare_isic2016.ps1").replace("'", "''")
    result = invoke(shell,
        "$tokens = $null; $parseErrors = $null; "
        f"$null = [System.Management.Automation.Language.Parser]::ParseFile('{path}', "
        "[ref]$tokens, [ref]$parseErrors); if ($parseErrors.Count) { $parseErrors | Out-String; exit 1 }", ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_preparation_launcher_resolves_paths_from_callers_directory(shell, launch_root, tmp_path):
    script = launch_root / "prepare_isic2016.ps1"
    shutil.copy2(ROOT / script.name, script)
    (launch_root / "dermademo/isic2016.py").write_text(
        "import json, pathlib, sys\n"
        "pathlib.Path('isic-launch.json').write_text(json.dumps(sys.argv[1:]))\n"
        "sys.exit(7)\n", encoding="ascii")
    caller = tmp_path / "caller"
    caller.mkdir()
    result = subprocess.run([shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(script), "-ArchiveDirectory", "raw", "-OutputDirectory", "prepared", "-Download",
        "-ValidationFraction", "0.25", "-DemoCount", "20"], cwd=caller, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=20)
    assert result.returncode == 7, result.stdout + result.stderr
    arguments = json.loads((launch_root / "isic-launch.json").read_text())
    assert arguments == ["--archives", str(caller / "raw"), "--output", str(caller / "prepared"),
        "--seed", "alien-home-isic2016-v1", "--validation-fraction", "0.25", "--demo-count", "20",
        "--connections", "4", "--download"]
