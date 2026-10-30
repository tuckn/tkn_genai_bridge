"""Effort settings survive CLI/Python calls without inventing provider defaults."""

import json
import sys

import pytest

from tkn_genai_bridge import GenerationPlan, Profile, ProviderError, Runtime
from tkn_genai_bridge.cli import main
from tkn_genai_bridge.providers.base import ProviderResponse


@pytest.mark.parametrize("effort", [None, "high"])
def test_python_plan_result_observer_and_legacy_plan(effort, request_object):
    records = []

    class Backend:
        def generate(self, profile, request):
            assert profile.reasoning_effort == effort
            return ProviderResponse({"summary": "ok"})

    with Runtime(Profile(reasoning_effort=effort), backend=Backend(), observer=records.append) as runtime:
        plan = runtime.plan(request_object)
        result = runtime.generate(request_object)
    assert plan.reasoning_effort == result.record.reasoning_effort == effort
    assert result.model_dump()["record"]["reasoning_effort"] == effort
    assert records[0].reasoning_effort == effort
    legacy = GenerationPlan.model_validate(plan.model_dump(exclude={"reasoning_effort"}))
    assert legacy.reasoning_effort is None


@pytest.mark.parametrize("mode", ["plan", "success", "failure"])
@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("override", [False, True])
def test_cli_effort_precedence_and_output(tmp_path, capsys, monkeypatch, mode, json_output, override):
    config = tmp_path / "config.yaml"
    config.write_text(
        'schema_version: "1.1.0"\nprofiles:\n  codex-default:\n'
        '    reasoning_effort: low\n    cli:\n      executable: '
        + json.dumps(sys.executable)
        + "\n",
        encoding="utf-8",
    )
    prompt, schema = tmp_path / "prompt.txt", tmp_path / "schema.json"
    prompt.write_text("Synthetic input", encoding="utf-8")
    schema.write_text('{"type":"object"}', encoding="utf-8")
    effort = "high" if override else "low"
    calls = []

    def generate(self, profile, request):
        calls.append(profile.reasoning_effort)
        assert profile.reasoning_effort == effort
        if mode == "failure":
            raise ProviderError("synthetic failure")
        return ProviderResponse({"summary": "ok"})

    monkeypatch.setattr("tkn_genai_bridge.providers.cli.CliBackend.generate", generate)
    args = [
        "generate", "--config", str(config), "--no-project-config",
        "--prompt-file", str(prompt), "--schema-file", str(schema),
    ]
    if override:
        args += ["--reasoning-effort", effort]
    if mode == "plan":
        args += ["--dry-run"]
    if json_output:
        args += ["--json"]
    assert main(args) == (1 if mode == "failure" else 0)
    output = capsys.readouterr()
    if json_output:
        data = json.loads(output.out)
        record = data if mode == "plan" else data["record"]
        assert record["reasoning_effort"] == effort
    else:
        key = "reasoning_effort" if mode == "plan" else "record.reasoning_effort"
        assert f"{key}={effort}" in output.out.splitlines()
    assert calls == ([] if mode == "plan" else [effort])
    assert "reasoning_effort: low" in config.read_text(encoding="utf-8")
