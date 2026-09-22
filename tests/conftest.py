from pathlib import Path

import pytest

from tkn_genai_runtime import GenerationRequest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(tmp_path)


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
