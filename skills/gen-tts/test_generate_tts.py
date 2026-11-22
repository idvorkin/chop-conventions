"""Unit tests for the pure parts of generate-tts.py.

The Gemini call and ffmpeg are always patched: no network, no paid request.
"""

import importlib.util
import os
import sys
import wave
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "generate_tts", Path(__file__).resolve().parent / "generate-tts.py"
)
tts = importlib.util.module_from_spec(_SPEC)
sys.modules["generate_tts"] = tts
_SPEC.loader.exec_module(tts)


@pytest.mark.parametrize(
    "line,expected",
    [
        ("GOOGLE_API_KEY=abc", ("GOOGLE_API_KEY", "abc")),
        ('GOOGLE_API_KEY="abc"', ("GOOGLE_API_KEY", "abc")),
        ("GOOGLE_API_KEY='abc'", ("GOOGLE_API_KEY", "abc")),
        ('export GOOGLE_API_KEY="a=b c"', ("GOOGLE_API_KEY", "a=b c")),
        ("  export   K = v  ", ("K", "v")),
        ('K="unbalanced', ("K", '"unbalanced')),
        ("exported=1", ("exported", "1")),
    ],
)
def test_parse_env_line(line, expected):
    assert tts.parse_env_line(line) == expected


def test_load_env_strips_export_and_quotes(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# c\nexport TTS_TEST_KEY_A="quoted"\nTTS_TEST_KEY_B=plain\n')
    monkeypatch.delenv("TTS_TEST_KEY_A", raising=False)
    monkeypatch.setenv("TTS_TEST_KEY_B", "already")
    tts.load_env(str(env))
    try:
        assert os.environ["TTS_TEST_KEY_A"] == "quoted"
        assert os.environ["TTS_TEST_KEY_B"] == "already"  # never overrides
    finally:
        os.environ.pop("TTS_TEST_KEY_A", None)


def _product(chain: str) -> float:
    total = 1.0
    for part in chain.split(","):
        step = float(part.removeprefix("atempo="))
        assert 0.5 <= step <= 2.0
        total *= step
    return total


@pytest.mark.parametrize("speed", [0.1, 0.25, 0.3, 0.5, 1.0, 1.8, 2.0, 3.0, 8.0, 100.0])
def test_atempo_chain_stays_in_band_and_multiplies_to_speed(speed):
    assert _product(tts.atempo_filter_chain(speed)) == pytest.approx(speed)


def test_atempo_chain_below_half_is_chained():
    assert tts.atempo_filter_chain(0.25) == "atempo=0.5,atempo=0.5"


@pytest.mark.parametrize(
    "bad", [0, -1, 0.05, 101, float("nan"), float("inf"), "2", None, True]
)
def test_validate_speed_rejects(bad):
    with pytest.raises(ValueError):
        tts.validate_speed(bad)


def test_generate_one_bad_speed_fails_before_api_call(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(tts, "call_gemini_tts", lambda *a, **k: calls.append(a))
    job = tts.TTSJob(
        text="hi", output=str(tmp_path / "o.wav"), voice="Kore", speed=0.01
    )
    result = tts.generate_one(job, "http://unused.invalid", "key")
    assert result.success is False
    assert "speed" in result.error
    assert calls == []


def test_generate_one_reports_duration_after_speed_change(
    tmp_path, monkeypatch, capsys
):
    rate = tts.DEFAULT_SAMPLE_RATE
    pcm = b"\x00\x00" * rate * 2  # 2.0s from the API

    def fake_speed(wav_path, speed):
        # Stand-in for ffmpeg: rewrite the file at 1/speed of its length.
        with wave.open(wav_path, "rb") as w:
            frames = w.readframes(w.getnframes())
        tts.pcm_to_wav(frames[: int(len(frames) / speed)], wav_path, rate)

    monkeypatch.setattr(tts, "call_gemini_tts", lambda *a, **k: (pcm, rate))
    monkeypatch.setattr(tts, "post_process_speed", fake_speed)
    out = str(tmp_path / "o.wav")
    job = tts.TTSJob(text="hi", output=out, voice="Kore", speed=2.0)
    result = tts.generate_one(job, "http://unused.invalid", "key")
    assert result.success is True
    assert tts.wav_duration_s(out) == 1.0
    assert "~1.0s" in capsys.readouterr().err
