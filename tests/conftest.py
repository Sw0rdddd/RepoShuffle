from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def project(tmp_path):
    def create(files: dict[str, str | bytes]) -> Path:
        root = tmp_path / "project"
        root.mkdir(exist_ok=True)
        for name, content in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        return root

    return create


@pytest.fixture
def run_module():
    """Execute only the controlled fixtures constructed by this test suite."""

    def run(root: Path, module: str) -> str:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, "-B", "-m", module],
            cwd=root,
            check=False,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout

    return run
