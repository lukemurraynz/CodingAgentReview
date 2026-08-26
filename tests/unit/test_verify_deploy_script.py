import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "verify-deploy.ps1"


def test_verify_deploy_script_runs_green_locally() -> None:
    missing_tools = [tool for tool in ("pwsh", "az", "azd") if shutil.which(tool) is None]
    if missing_tools:
        pytest.skip(f"missing required tools: {', '.join(missing_tools)}")

    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(SCRIPT_PATH)],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "[PASS] azd provision help" in completed.stdout
    assert "[PASS] az bicep build" in completed.stdout
    assert "[PASS] azd environment parameters" in completed.stdout
    assert "[PASS] azure.yaml hook scripts" in completed.stdout
    assert "Summary: 4 passed, 0 failed" in completed.stdout
