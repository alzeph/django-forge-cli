"""Tests de forge.console.enable_utf8_output (fix encodage Windows)."""

from __future__ import annotations

import pytest

from forge.console import enable_utf8_output


class _FakeStream:
    """Flux texte minimal exposant encoding + reconfigure, comme sys.stdout."""

    def __init__(self, encoding: str) -> None:
        self.encoding = encoding
        self.reconfigured_to: str | None = None

    def reconfigure(self, encoding: str, errors: str | None = None) -> None:
        self.reconfigured_to = encoding
        self.encoding = encoding


def test_reconfigures_non_utf8_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    out, err = _FakeStream("cp1252"), _FakeStream("cp1252")
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)

    enable_utf8_output()

    assert out.reconfigured_to == "utf-8"
    assert err.reconfigured_to == "utf-8"


def test_skips_streams_already_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    # "UTF-8", "utf8"… doivent tous être reconnus comme déjà bons.
    out, err = _FakeStream("UTF-8"), _FakeStream("utf8")
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)

    enable_utf8_output()

    assert out.reconfigured_to is None
    assert err.reconfigured_to is None


def test_stream_without_reconfigure_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Bare:
        encoding = "cp1252"

    monkeypatch.setattr("sys.stdout", _Bare())
    monkeypatch.setattr("sys.stderr", _Bare())

    enable_utf8_output()  # ne doit pas lever


def test_reconfigure_error_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Boom:
        encoding = "cp1252"

        def reconfigure(self, encoding: str, errors: str | None = None) -> None:
            raise OSError("flux redirigé, non reconfigurable")

    monkeypatch.setattr("sys.stdout", _Boom())
    monkeypatch.setattr("sys.stderr", _Boom())

    enable_utf8_output()  # ne doit pas lever
