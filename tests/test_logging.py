import logging
import sys

import pytest

from tkn_genai_bridge.logging_utils import SUCCESS, ColorFormatter, configure_logging, supports_color


def test_level_and_color_contract():
    assert logging.INFO < SUCCESS < logging.WARNING
    assert logging.getLevelName(SUCCESS) == "SUCCESS"
    for level, color in [(SUCCESS, "32"), (logging.ERROR, "31"), (logging.CRITICAL, "31")]:
        record = logging.LogRecord("test", level, "", 1, "message", (), None)
        rendered = ColorFormatter(use_color=True).format(record)
        assert rendered.startswith(f"\x1b[{color}m[")
        assert rendered.endswith("\x1b[0m")
        assert "\x1b" not in ColorFormatter(use_color=False).format(record)


@pytest.mark.parametrize(
    "quiet,verbose,debug,info,success",
    [
        (False, False, False, True, True),
        (True, False, False, False, False),
        (False, True, True, True, True),
    ],
)
def test_log_filtering_stderr(quiet, verbose, debug, info, success, capsys):
    logger = configure_logging(quiet=quiet, verbose=verbose)
    logger.debug("debug")
    logger.info("info")
    logger.log(SUCCESS, "success")
    logger.error("error")
    output = capsys.readouterr()
    assert output.out == ""
    assert ("[DEBUG]" in output.err) == debug
    assert ("[INFO]" in output.err) == info
    assert ("[SUCCESS]" in output.err) == success
    assert "[ERROR]" in output.err
    assert "\x1b" not in output.err


def test_color_fallbacks(monkeypatch):
    class Tty:
        def isatty(self):
            return True

    from tkn_genai_bridge import logging_utils

    monkeypatch.setenv("NO_COLOR", "")
    assert not supports_color(Tty())
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("TERM", "dumb")
    assert not supports_color(Tty())
    monkeypatch.delenv("TERM")
    monkeypatch.setattr(logging_utils, "_windows_vt", lambda stream: False)
    monkeypatch.setattr(logging_utils.os, "name", "nt")
    assert not supports_color(Tty())
    assert not supports_color(sys.stderr)
