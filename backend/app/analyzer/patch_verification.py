from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.app.schemas.api import PatchVerificationResponse


MAX_OUTPUT_LENGTH = 4000
PYTEST_TIMEOUT_SECONDS = 60


def run_pytest_verification(workspace_root: Path) -> PatchVerificationResponse:
    """Run the allowlisted post-apply check in the configured workspace."""
    checked_at = datetime.now(timezone.utc)

    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=PYTEST_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return PatchVerificationResponse(
            requested=True,
            tool="pytest",
            status="error",
            output=f"pytest exceeded the {PYTEST_TIMEOUT_SECONDS}-second timeout.",
            checked_at=checked_at,
        )
    except OSError as error:
        return PatchVerificationResponse(
            requested=True,
            tool="pytest",
            status="error",
            output=f"pytest could not be started: {error}",
            checked_at=checked_at,
        )

    output = (completed.stdout + completed.stderr).strip()
    if len(output) > MAX_OUTPUT_LENGTH:
        output = output[-MAX_OUTPUT_LENGTH:]

    return PatchVerificationResponse(
        requested=True,
        tool="pytest",
        status="passed" if completed.returncode == 0 else "failed",
        exit_code=completed.returncode,
        output=output or None,
        checked_at=checked_at,
    )
