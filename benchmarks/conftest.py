from __future__ import annotations

from pathlib import Path

import pytest

import agentic_exception_sdk


@pytest.fixture(autouse=True)
def assert_installed_wheel() -> None:
    module_path = Path(agentic_exception_sdk.__file__).resolve()
    repo_src = Path(__file__).resolve().parents[1] / "src"
    assert not module_path.is_relative_to(repo_src), (
        "benchmarks must run against an installed wheel, not the editable source tree"
    )
