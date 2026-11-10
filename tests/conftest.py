import tempfile
from pathlib import Path

import pytest

from tkn_genai_bridge import GenerationRequest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, tmp_path_factory, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(tmp_path)
    # Provider folders, and the removal of abandoned ones, never touch the real temporary area.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path_factory.mktemp("system-temp")))


@pytest.fixture
def request_object():
    return GenerationRequest(
        prompt="Summarize this synthetic input.",
        output_schema={
            "type": "object",
            "properties": {"summary": {"type": "string", "minLength": 1}},
            "required": ["summary"],
            "additionalProperties": False,
        },
    )
