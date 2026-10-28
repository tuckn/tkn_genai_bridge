import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tkn_genai_bridge import CliSettings, Profile, ProviderError, Runtime
from tkn_genai_bridge.providers import cli


@pytest.fixture
def npm_install(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli.platform, "machine", lambda: "AMD64")

    def install(*, local=False, hoisted=False, arch="x64", suffix=".cmd"):
        prefix = tmp_path / "npm with spaces"
        modules = prefix / "node_modules"
        shim = (modules / ".bin" if local else prefix) / f"copilot{suffix}"
        shim.parent.mkdir(parents=True, exist_ok=True)
        # Deliberately not a runnable npm shim: discovery must never interpret its contents.
        shim.write_text("DO NOT EXECUTE OR EVALUATE THIS SHIM", encoding="utf-8")
        package = modules / "@github" / "copilot"
        package.mkdir(parents=True)
        name = f"@github/copilot-win32-{arch}"
        (package / "package.json").write_text(
            json.dumps(
                {
                    "name": "@github/copilot",
                    "bin": {"copilot": "npm-loader.js"},
                    "optionalDependencies": {name: "1.0.94"},
                }
            ),
            encoding="utf-8",
        )
        native = (modules if hoisted else package / "node_modules") / "@github" / f"copilot-win32-{arch}"
        native.mkdir(parents=True)
        (native / "package.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "os": ["win32"],
                    "cpu": [arch],
                    "exports": {".": "./copilot.exe"},
                }
            ),
            encoding="utf-8",
        )
        executable = native / "copilot.exe"
        executable.write_bytes(b"synthetic executable")
        monkeypatch.setattr(cli.shutil, "which", lambda name: str(shim))
        return shim, executable

    return install


def test_explicit_native_executable_takes_priority(npm_install, tmp_path):
    npm_install()
    executable = tmp_path / "explicit with spaces" / "custom-copilot.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"")
    profile = Profile(provider="github-copilot", cli=CliSettings(executable=str(executable)))
    assert cli.resolve_executable(profile) == str(executable.resolve())


def test_path_native_executable_is_preserved(tmp_path, monkeypatch):
    executable = tmp_path / "path with spaces" / "copilot.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"")
    monkeypatch.setattr(cli.shutil, "which", lambda name: str(executable))
    assert cli.resolve_executable(Profile(provider="github-copilot")) == str(executable)


@pytest.mark.parametrize("local,hoisted", [(False, False), (False, True), (True, False), (True, True)])
@pytest.mark.parametrize("suffix", [".cmd", ".ps1", ".CMD"])
def test_npm_shim_resolves_native_package(npm_install, local, hoisted, suffix):
    _, executable = npm_install(local=local, hoisted=hoisted, suffix=suffix)
    assert cli.resolve_executable(Profile(provider="github-copilot")) == str(executable.resolve())


@pytest.mark.parametrize("machine,arch", [("AMD64", "x64"), ("x86_64", "x64"), ("ARM64", "arm64")])
def test_npm_architecture_selection(npm_install, monkeypatch, machine, arch):
    _, executable = npm_install(arch=arch)
    monkeypatch.setattr(cli.platform, "machine", lambda: machine)
    assert cli.resolve_executable(Profile(provider="github-copilot")) == str(executable.resolve())


def test_explicit_npm_shim_can_resolve(npm_install, monkeypatch):
    shim, executable = npm_install()
    monkeypatch.setattr(cli.shutil, "which", lambda name: pytest.fail("explicit path must take priority"))
    profile = Profile(provider="github-copilot", cli=CliSettings(executable=str(shim)))
    assert cli.resolve_executable(profile) == str(executable.resolve())


def test_npm_missing_binary_explains_paths_and_remedy(npm_install):
    shim, executable = npm_install()
    executable.unlink()
    with pytest.raises(ProviderError) as exc:
        cli.resolve_executable(Profile(provider="github-copilot"))
    assert exc.value.code == "invalid_executable"
    assert not exc.value.submission_unknown
    message = str(exc.value)
    assert str(shim) in message
    assert str(executable) in message
    assert "native executable not found" in message
    assert "cli.executable" in message
    assert "optional dependencies" in message


def test_npm_missing_native_package_is_rejected(npm_install):
    _, executable = npm_install()
    (executable.parent / "package.json").unlink()
    with pytest.raises(ProviderError, match="cannot read npm package metadata"):
        cli.resolve_executable(Profile(provider="github-copilot"))


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("metadata", ["not json", "[]", '{"name":"unrelated-package"}'])
def test_npm_unknown_metadata_is_rejected(npm_install, native, metadata):
    shim, executable = npm_install()
    manifest = (
        executable.parent / "package.json"
        if native
        else shim.parent / "node_modules" / "@github" / "copilot" / "package.json"
    )
    manifest.write_text(metadata, encoding="utf-8")
    with pytest.raises(ProviderError) as exc:
        cli.resolve_executable(Profile(provider="github-copilot"))
    assert str(manifest) in str(exc.value)
    assert "cli.executable" in str(exc.value)


def test_npm_unsupported_architecture_does_not_guess(npm_install, monkeypatch):
    npm_install()
    monkeypatch.setattr(cli.platform, "machine", lambda: "x86")
    with pytest.raises(ProviderError, match="unsupported Windows architecture"):
        cli.resolve_executable(Profile(provider="github-copilot"))


@pytest.mark.parametrize("name", ["copilot.bat", "custom.cmd", "custom.ps1"])
def test_unsupported_copilot_shim_is_rejected(npm_install, monkeypatch, name):
    shim, _ = npm_install()
    unsupported = shim.with_name(name)
    unsupported.write_text("DO NOT EXECUTE", encoding="utf-8")
    monkeypatch.setattr(cli.shutil, "which", lambda name: str(unsupported))
    with pytest.raises(ProviderError, match="shell shims are not supported") as exc:
        cli.resolve_executable(Profile(provider="github-copilot"))
    assert str(unsupported) in str(exc.value)
    assert "cli.executable" in str(exc.value)


def test_standalone_shim_without_npm_package_is_rejected(tmp_path, monkeypatch):
    shim = tmp_path / "copilot.cmd"
    shim.write_text("arbitrary script", encoding="utf-8")
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(cli.shutil, "which", lambda name: str(shim))
    with pytest.raises(ProviderError, match="cannot read npm package metadata") as exc:
        cli.resolve_executable(Profile(provider="github-copilot"))
    assert str(shim) in str(exc.value)


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_non_windows_path_behavior_is_unchanged(npm_install, monkeypatch, platform):
    shim, _ = npm_install()
    monkeypatch.setattr(cli.sys, "platform", platform)
    with pytest.raises(ProviderError, match="shell shims are not supported"):
        cli.resolve_executable(Profile(provider="github-copilot"))
    executable = shim.with_suffix("")
    executable.write_text("#!/usr/bin/env node", encoding="utf-8")
    monkeypatch.setattr(cli.shutil, "which", lambda name: str(executable))
    assert cli.resolve_executable(Profile(provider="github-copilot")) == str(executable)


def test_other_providers_do_not_resolve_copilot_packages(npm_install):
    npm_install()
    with pytest.raises(ProviderError, match="shell shims are not supported"):
        cli.resolve_executable(Profile(provider="claude-code"))


def test_resolved_binary_is_launched_without_shell(npm_install, monkeypatch, request_object):
    _, executable = npm_install()
    calls = []

    def popen(command, **kwargs):
        calls.append(command)
        assert command[0] == str(executable.resolve())
        assert kwargs["shell"] is False
        assert kwargs["cwd"] != Path.cwd()
        return SimpleNamespace(
            communicate=lambda prompt, timeout: ('{"summary":"synthetic"}', ""),
            returncode=0,
        )

    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    result = Runtime(Profile(provider="github-copilot")).generate(request_object)
    assert result.data == {"summary": "synthetic"}
    assert len(calls) == 1


def test_missing_explicit_executable_does_not_fall_back_to_npm(npm_install, tmp_path, monkeypatch):
    npm_install()
    profile = Profile(
        provider="github-copilot",
        cli=CliSettings(executable=str(tmp_path / "missing.exe")),
    )
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    with pytest.raises(ProviderError) as exc:
        cli.resolve_executable(profile)
    assert exc.value.code == "executable_missing"
