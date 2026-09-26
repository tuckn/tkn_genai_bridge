"""Image bytes must reach providers in order, without leaking into diagnostics."""

import base64
import hashlib
import json
import sys
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from tkn_genai_bridge import (
    AzureSettings,
    CliSettings,
    GenerationRecord,
    GenerationRequest,
    ImageInput,
    Profile,
    ProviderError,
    RequestError,
    Runtime,
    TokenPricing,
)
from tkn_genai_bridge.cli import main
from tkn_genai_bridge.images import MAX_IMAGE_BYTES
from tkn_genai_bridge.providers import cli
from tkn_genai_bridge.providers.base import ProviderResponse
from tkn_genai_bridge.providers.litellm import LiteLLMBackend

# A synthetic 1x1 PNG. No private documents are required by these tests.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII="
)


def with_images(request, count=2):
    return GenerationRequest(
        prompt=request.prompt,
        output_schema=request.output_schema,
        images=[ImageInput(data=PNG + bytes([i]), media_type="image/png") for i in range(count)],
    )


def test_file_snapshot_survives_source_changes_and_hides_path(tmp_path, request_object):
    source = tmp_path / "private 日本語 & image.png"
    source.write_bytes(PNG)
    image = ImageInput.from_file(source)
    source.write_bytes(b"changed")
    assert image.data == PNG
    request = request_object.model_copy(update={"images": [image]})
    plan = Runtime(Profile()).plan(request)
    assert plan.images[0].sha256 == hashlib.sha256(PNG).hexdigest()
    assert plan.images[0].size_bytes == len(PNG)
    for text in (repr(image), repr(request), plan.model_dump_json()):
        assert "private" not in text and str(source) not in text
        assert base64.b64encode(PNG).decode() not in text


@pytest.mark.parametrize(
    "data", [b"", b"not an image", b"GIF89a", b"<svg/>"], ids=["empty", "text", "gif", "svg"]
)
def test_invalid_file_rejected_without_path_or_contents(data, tmp_path):
    source = tmp_path / "private-input.png"
    source.write_bytes(data)
    with pytest.raises(RequestError) as exc:
        ImageInput.from_file(source)
    assert exc.value.code == "invalid_image"
    assert "private" not in str(exc.value)


def test_oversized_file_is_rejected(tmp_path):
    source = tmp_path / "large.png"
    with source.open("wb") as stream:
        stream.write(PNG)
        stream.seek(MAX_IMAGE_BYTES)
        stream.write(b"x")
    with pytest.raises(RequestError) as exc:
        ImageInput.from_file(source)
    assert exc.value.code == "invalid_image"


def test_unreadable_image_has_content_free_error(tmp_path):
    for path in (tmp_path / "private-missing.png", tmp_path):
        with pytest.raises(RequestError) as exc:
            ImageInput.from_file(path)
        assert exc.value.code == "image_io" and "private" not in str(exc.value)


@pytest.mark.parametrize(
    "data,media_type",
    [(PNG, "image/png"), (b"\xff\xd8\xff\xe0", "image/jpeg"), (b"RIFF0000WEBP", "image/webp")],
)
def test_media_type_from_content_not_extension(data, media_type, tmp_path):
    path = tmp_path / "image.wrong-extension"
    path.write_bytes(data)
    assert ImageInput.from_file(path).media_type == media_type


def test_direct_bytes_validate_media_type_and_hide_validation_input():
    with pytest.raises(ValidationError) as exc:
        ImageInput(data=b"private raw content", media_type="image/png")
    assert "private" not in str(exc.value)
    with pytest.raises(ValidationError):
        ImageInput(data=PNG, media_type="image/jpeg")


@pytest.mark.parametrize("provider", ["claude-code", "github-copilot", "antigravity"])
def test_unsupported_provider_rejected_before_backend_or_executable(provider, request_object, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unsupported images must fail before side effects")

    monkeypatch.setattr(cli, "resolve_executable", forbidden)
    monkeypatch.setattr("tkn_genai_bridge.runtime.resolve_executable", forbidden)
    request = with_images(request_object)
    runtime = Runtime(Profile(provider=provider))
    for operation in (
        lambda: runtime.plan(request, check_executable=True),
        lambda: runtime.generate(request),
    ):
        with pytest.raises(RequestError) as exc:
            operation()
        assert exc.value.code == "unsupported_images"


@pytest.mark.parametrize("fail", [False, True])
def test_codex_attaches_snapshots_and_cleans_up_on_success_and_failure(fail, request_object, monkeypatch):
    request = with_images(request_object)
    paths = []

    def run(command, prompt, cwd, timeout, **kwargs):
        assert command[-1] == "-" and prompt == request.prompt
        paths.extend(Path(command[i + 1]) for i, arg in enumerate(command) if arg == "--image")
        assert [path.read_bytes() for path in paths] == [image.data for image in request.images]
        assert all(path.parent == cwd for path in paths)
        assert [path.name for path in paths] == ["image-0001.png", "image-0002.png"]
        if fail:
            raise ProviderError("synthetic timeout", code="timeout")
        Path(command[command.index("--output-last-message") + 1]).write_text(
            '{"summary":"ok"}', encoding="utf-8"
        )
        return '{"type":"turn.completed","usage":{"input_tokens":123,"output_tokens":4}}'

    monkeypatch.setattr(cli, "run_process", run)
    runtime = Runtime(Profile(cli=CliSettings(executable=sys.executable)))
    plan = runtime.plan(request)
    if fail:
        with pytest.raises(ProviderError) as exc:
            runtime.generate(request)
        record = exc.value.record
    else:
        record = runtime.generate(request).record
        assert record.usage.input_tokens == 123
    assert record.images == plan.images and record.input_sha256 == plan.input_sha256
    assert paths and all(not path.exists() for path in paths)


@pytest.mark.parametrize("local", [True, False])
def test_real_sdk_delivers_all_image_bytes_in_order(local, request_object, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "synthetic-key")
    request = with_images(request_object)
    calls = []

    def handle(outgoing):
        calls.append(outgoing)
        body = json.loads(outgoing.content)
        message = body["messages"][0]
        if local:
            assert request.prompt in message["content"]
            assert [base64.b64decode(value) for value in message["images"]] == [
                image.data for image in request.images
            ]
            response = {
                "done": True,
                "done_reason": "stop",
                "message": {"content": '{"summary":"ok"}'},
                "prompt_eval_count": 123,
                "eval_count": 4,
            }
        else:
            assert message["content"][0] == {"type": "text", "text": request.prompt}
            assert [item["image_url"]["url"] for item in message["content"][1:]] == [
                image.data_url() for image in request.images
            ]
            response = {
                "choices": [{"finish_reason": "stop", "message": {"content": '{"summary":"ok"}'}}],
                "usage": {"prompt_tokens": 123, "completion_tokens": 4},
            }
        return httpx.Response(200, json=response)

    profile = (
        Profile(provider="ollama", model="synthetic-vision:latest")
        if local
        else Profile(
            provider="azure-openai",
            model="deployment",
            azure=AzureSettings(endpoint="https://example.openai.azure.com/openai/v1"),
        )
    )
    result = Runtime(profile, backend=LiteLLMBackend(transport=httpx.MockTransport(handle))).generate(request)
    assert len(calls) == 1
    assert result.record.usage.input_tokens == 123
    assert len(result.record.images) == 2


def test_identity_depends_on_image_content_and_order_but_not_settings(request_object):
    runtime = Runtime(Profile())
    original = with_images(request_object)
    plan = runtime.plan(original)
    for images in (original.images[::-1], original.images[:1], []):
        changed = runtime.plan(original.model_copy(update={"images": images}))
        assert changed.input_sha256 != plan.input_sha256
        assert changed.prompt_sha256 == plan.prompt_sha256
        assert changed.generation_settings_sha256 == plan.generation_settings_sha256


def test_image_token_total_unknown_until_caller_supplies_estimate(request_object):
    runtime = Runtime(
        Profile(
            model="vision",
            pricing={
                "vision": TokenPricing(
                    currency="USD",
                    pricing_date="2026-01-01",
                    input_per_million=1,
                    output_per_million=2,
                )
            },
        )
    )
    request = with_images(request_object)
    plan = runtime.plan(request, output_tokens=100)
    assert plan.token_estimate.input_tokens is None
    assert plan.cost_estimate.status == "unavailable"
    assert plan.cost_estimate.unavailable_reason == "usage_missing"
    supplied = runtime.plan(request, input_tokens=1000, input_tokens_method="vision-v1", output_tokens=100)
    assert supplied.cost_estimate.amount == pytest.approx(0.0012)
    assert supplied.input_sha256 == plan.input_sha256


def test_old_records_default_to_no_image_metadata(request_object):
    class Backend:
        def generate(self, profile, request):
            return ProviderResponse({"summary": "ok"})

    record = Runtime(Profile(), backend=Backend()).generate(request_object).record
    legacy = GenerationRecord.model_validate(record.model_dump(exclude={"images", "input_sha256"}))
    assert legacy.images == [] and legacy.input_sha256 is None


def test_cli_repeated_images_and_dry_run_no_writes(tmp_path, capsys, monkeypatch):
    config, prompt, schema = (tmp_path / name for name in ("config.yaml", "prompt.txt", "schema.json"))
    config.write_text(
        'schema_version: "1.1.0"\ndefault_profile: p\nprofiles:\n'
        '  p:\n    provider: ollama\n    model: vision\n',
        encoding="utf-8",
    )
    prompt.write_text("synthetic", encoding="utf-8")
    schema.write_text('{"type":"object"}', encoding="utf-8")
    source = tmp_path / "private image.png"
    source.write_bytes(PNG)
    args = [
        "generate",
        "--config",
        str(config),
        "--prompt-file",
        str(prompt),
        "--schema-file",
        str(schema),
        "--image",
        str(source),
        "--image",
        str(source),
        "--dry-run",
    ]

    def forbidden(*args, **kwargs):
        pytest.fail("dry-run must not call a provider or write temporary images")

    monkeypatch.setattr(LiteLLMBackend, "generate", forbidden)
    monkeypatch.setattr(cli.tempfile, "TemporaryDirectory", forbidden)
    before = {path: path.read_bytes() for path in tmp_path.iterdir()}
    assert main(args) == 0
    output = capsys.readouterr()
    plan = json.loads(output.out)
    assert len(plan["images"]) == 2 and plan["will_call_provider"] is False
    assert plan["token_estimate"]["input_tokens"] is None
    assert "private" not in output.out + output.err
    assert before == {path: path.read_bytes() for path in tmp_path.iterdir()}
    source.unlink()
    assert main(args) == 1
    error = capsys.readouterr()
    assert json.loads(error.out)["error"]["code"] == "image_io"
    assert "private" not in error.out + error.err
