"""Image transport and failure contracts, using synthetic bytes without live services."""

import base64
import json
import sys
from pathlib import Path

import pytest

from tkn_genai_bridge import CliSettings, GenerationRequest, ImageInput, Profile, ProviderError, Runtime
from tkn_genai_bridge.providers import cli


def request_with_images(request):
    return GenerationRequest(
        prompt=request.prompt,
        output_schema=request.output_schema,
        images=[
            ImageInput(data=b"\xff\xd8\xfffirst", media_type="image/jpeg"),
            ImageInput(data=b"RIFF0000WEBPsecond", media_type="image/webp"),
            ImageInput(data=b"\xff\xd8\xfffirst", media_type="image/jpeg"),
        ],
    )


def profile(provider):
    return Profile(provider=provider, cli=CliSettings(executable=sys.executable))


def claude_result(**updates):
    return (
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "structured_output": {"summary": "ok"},
                "modelUsage": {"observed-model": {}},
                "usage": {"input_tokens": 12, "output_tokens": 3},
                **updates,
            }
        )
        + "\n"
    )


def test_claude_bytes_order_duplicates_and_terminal_usage(request_object, monkeypatch):
    request = request_with_images(request_object)
    request.output_schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"

    def run(command, prompt, cwd, timeout, **kwargs):
        assert command[command.index("--input-format") + 1] == "stream-json"
        assert command[command.index("--output-format") + 1] == "stream-json"
        assert "--verbose" in command and "--no-session-persistence" in command
        assert command[command.index("--tools") + 1] == ""
        assert "$schema" not in json.loads(command[command.index("--json-schema") + 1])
        assert not list(cwd.iterdir())  # Images travel through stdin, not files.
        assert len(prompt.splitlines()) == 1
        event = json.loads(prompt)
        blocks = event["message"]["content"]
        assert blocks[0] == {"type": "text", "text": request.prompt}
        assert [base64.b64decode(b["source"]["data"]) for b in blocks[1:]] == [
            image.data for image in request.images
        ]
        assert [b["source"]["media_type"] for b in blocks[1:]] == [
            image.media_type for image in request.images
        ]
        return '{"type":"assistant","message":{"usage":{"input_tokens":999}}}\n' + claude_result()

    monkeypatch.setattr(cli, "run_process", run)
    result = Runtime(profile("claude-code")).generate(request)
    assert result.record.response_model == "observed-model"
    assert result.record.usage.input_tokens == 12
    assert len(result.record.images) == 3
    assert "$schema" in request.output_schema


@pytest.mark.parametrize(
    "output,code",
    [
        ('{"type":"assistant"}\n', "missing_output"),
        ("private invalid output\n" + claude_result(), "incomplete_response"),
        (claude_result() + claude_result(), "incomplete_response"),
        (claude_result() + '{"type":"assistant"}\n', "incomplete_response"),
        (claude_result(is_error=True), "incomplete_response"),
        (claude_result(structured_output=None), "missing_output"),
    ],
)
def test_claude_rejects_incomplete_or_unsuccessful_stream(output, code, request_object, monkeypatch):
    monkeypatch.setattr(cli, "run_process", lambda *a, **kw: output)
    with pytest.raises(ProviderError) as exc:
        Runtime(profile("claude-code")).generate(request_with_images(request_object))
    assert exc.value.code == code
    assert "private" not in str(exc.value)
    assert len(exc.value.record.images) == 3


@pytest.mark.parametrize("timeout", [False, True])
def test_claude_process_failure_keeps_stream_usage(timeout, tmp_path, monkeypatch):
    output = '{"type":"system"}\n' + claude_result()

    class Process:
        returncode = 2

        def communicate(self, prompt, timeout):
            if fail_timeout:
                raise cli.subprocess.TimeoutExpired("claude", timeout)
            return output, "private"

    fail_timeout = timeout
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **kw: Process())
    monkeypatch.setattr(cli, "_stop_process", lambda process: output)
    with pytest.raises(ProviderError) as exc:
        cli.run_process(["claude"], "", tmp_path, 1, provider="claude-code")
    assert exc.value.metadata.response_model == "observed-model"
    assert exc.value.metadata.usage.completeness == "partial"
    assert exc.value.metadata.usage.known_subtotal.input_tokens == 12


@pytest.mark.parametrize("outcome", ["success", "timeout", "missing", "error", "wrong-path"])
def test_antigravity_snapshots_read_receipts_and_cleanup(outcome, request_object, monkeypatch):
    request = request_with_images(request_object)
    paths = []

    def run(command, prompt, cwd, timeout, **kwargs):
        assert command[command.index("--add-dir") + 1] == str(cwd)
        assert "--dangerously-skip-permissions" not in command
        content = json.loads(prompt)["message"]["content"]
        paths.extend(Path(p) for p in json.loads(content.splitlines()[1]))
        assert [p.read_bytes() for p in paths] == [image.data for image in request.images]
        assert all(p.is_absolute() and p.parent == cwd for p in paths)
        assert request.prompt in content
        if outcome == "timeout":
            raise ProviderError("synthetic timeout", code="timeout")
        events = []
        for path in paths[:1] if outcome == "missing" else paths:
            events.append(
                {
                    "event": "step_update",
                    "step_update": {
                        "state": "ERROR" if outcome == "error" else "DONE",
                        "step_type": "tool",
                        "tool_info": {
                            "name": "view_file",
                            "parameters": {
                                "AbsolutePath": str(cwd / "other.jpg")
                                if outcome == "wrong-path"
                                else str(path)
                            },
                        },
                    },
                }
            )
        events.append(
            {
                "event": "result",
                "result": {
                    "status": "SUCCESS",
                    "structured_output": {"summary": "ok"},
                    "usage": {"input_tokens": 100, "output_tokens": 10},
                },
            }
        )
        return "\n".join(json.dumps(e) for e in events)

    monkeypatch.setattr(cli, "run_process", run)
    runtime = Runtime(profile("antigravity"))
    if outcome == "success":
        record = runtime.generate(request).record
    else:
        with pytest.raises(ProviderError) as exc:
            runtime.generate(request)
        assert exc.value.code == ("timeout" if outcome == "timeout" else "image_read_failed")
        record = exc.value.record
    assert paths and all(not p.exists() for p in paths)
    assert len(record.images) == 3
    if outcome != "timeout":
        assert record.usage.input_tokens == 100
    assert "image-0001" not in record.model_dump_json()


@pytest.mark.parametrize("provider", ["claude-code", "antigravity"])
def test_plan_with_images_has_no_process_or_temporary_writes(provider, request_object, monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("plan must not launch a process or create files")

    monkeypatch.setattr(cli, "run_process", forbidden)
    monkeypatch.setattr(cli.tempfile, "TemporaryDirectory", forbidden)
    plan = Runtime(profile(provider)).plan(request_with_images(request_object), check_executable=True)
    assert len(plan.images) == 3 and not plan.will_call_provider
