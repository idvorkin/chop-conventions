#!/usr/bin/env python3
"""Unit tests for larry-voice.py — pure functions only, no network / no ffmpeg.

Run with: python3 -m unittest test_larry_voice.py
The HTTP opener and subprocess.run are injected/mocked; nothing here hits
ElevenLabs, Gemini, or ffmpeg. Uses stdlib unittest so the repo `just
fast-test` hook (unittest discover under system Python) runs it.
"""

import importlib.util
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

# Load larry-voice.py (hyphenated filename) as a module without executing __main__.
_SPEC = importlib.util.spec_from_file_location(
    "larry_voice", Path(__file__).resolve().parent / "larry-voice.py"
)
lv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(lv)


# ----- fakes --------------------------------------------------------------


class _FakeResp:
    def __init__(self, status, body, headers):
        self.status = status
        self._body = body
        self.headers = headers

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(status, body, headers):
    def _open(req, timeout=None):
        return _FakeResp(status, body, headers)

    return _open


def _judge_response(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


# ----- key resolution -----------------------------------------------------


class KeyResolution(unittest.TestCase):
    def test_prefers_process_env(self):
        key = lv.resolve_api_key(
            {"ELEVEN_API_KEY": "from_env"},
            {"ELEVEN_API_KEY": "from_dotenv"},
            {"ELEVEN_API_KEY": "from_box"},
        )
        self.assertEqual(key, "from_env")

    def test_falls_through_to_dotenv_then_box(self):
        self.assertEqual(
            lv.resolve_api_key({}, {"ELEVEN_API_KEY": "dotenv"}, {}), "dotenv"
        )
        self.assertEqual(lv.resolve_api_key({}, {}, {"ELEVEN_API_KEY": "box"}), "box")

    def test_missing_returns_none(self):
        self.assertIsNone(lv.resolve_api_key({}, {}, {}))

    def test_wrong_key_name_does_not_resolve(self):
        # ELEVENLABS_API_KEY (the wrong guess) must NOT resolve.
        self.assertIsNone(lv.resolve_api_key({"ELEVENLABS_API_KEY": "nope"}, {}, {}))

    def test_parse_env_lines_skips_comments_and_blanks(self):
        parsed = lv.parse_env_lines(
            ["# c", "", "ELEVEN_API_KEY=abc", "GOOGLE_API_KEY = xyz "]
        )
        self.assertEqual(parsed["ELEVEN_API_KEY"], "abc")
        self.assertEqual(parsed["GOOGLE_API_KEY"], "xyz")

    def test_key_fingerprint_never_leaks_full_key(self):
        fp = lv.key_fingerprint("sk_supersecretvalue12345")
        self.assertNotIn("supersecret", fp)
        self.assertIn("len=24", fp)
        self.assertIn("sk_s", fp)


# ----- IPv4 forcing -------------------------------------------------------


class IPv4Filter(unittest.TestCase):
    V4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 443))
    V6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0))

    def test_drops_v6(self):
        self.assertEqual(lv.filter_ipv4([self.V6, self.V4]), [self.V4])

    def test_empty_when_no_v4(self):
        self.assertEqual(lv.filter_ipv4([self.V6]), [])


# ----- pure builders ------------------------------------------------------


class Builders(unittest.TestCase):
    def test_tts_url_appends_voice_id(self):
        self.assertTrue(lv.tts_url("VID").endswith("/text-to-speech/VID"))

    def test_build_tts_payload_shape(self):
        self.assertEqual(
            lv.build_tts_payload("hi", "eleven_flash_v2_5"),
            {"text": "hi", "model_id": "eleven_flash_v2_5"},
        )

    def test_ffmpeg_ogg_cmd_is_mono_opus(self):
        cmd = lv.ffmpeg_ogg_cmd("in.mp3", "out.ogg")
        self.assertIn("libopus", cmd)
        self.assertEqual(cmd[cmd.index("-ac") + 1], "1")
        self.assertEqual(cmd[-1], "out.ogg")

    def test_explain_eleven_error_free_tier_hint(self):
        msg = lv.explain_eleven_error(402, '{"detail":"x"}', "somevoice")
        self.assertIn("PREMADE", msg)
        self.assertIn("402", msg)


# ----- ElevenLabs synth (mocked HTTP) -------------------------------------


class SynthMp3(unittest.TestCase):
    def test_returns_audio_bytes(self):
        audio = b"\xff\xfb" + b"\x00" * 2000
        out = lv.synth_mp3(
            "hello",
            "VID",
            "key",
            opener=_opener(200, audio, {"Content-Type": "audio/mpeg"}),
        )
        self.assertEqual(out, audio)

    def test_raises_on_http_error(self):
        with self.assertRaises(lv.VoiceError) as cm:
            lv.synth_mp3(
                "hi", "VID", "key", opener=_opener(402, b'{"detail":"blocked"}', {})
            )
        self.assertIn("402", str(cm.exception))

    def test_raises_on_json_masquerading_as_200(self):
        body = b'{"detail":"quota"}'
        with self.assertRaises(lv.VoiceError) as cm:
            lv.synth_mp3(
                "hi",
                "VID",
                "key",
                opener=_opener(200, body, {"Content-Type": "application/json"}),
            )
        self.assertIn("Expected audio", str(cm.exception))

    def test_raises_on_tiny_body(self):
        with self.assertRaises(lv.VoiceError) as cm:
            lv.synth_mp3(
                "hi",
                "VID",
                "key",
                opener=_opener(200, b"\xff\xfbxx", {"Content-Type": "audio/mpeg"}),
            )
        self.assertIn("truncated", str(cm.exception))


# ----- ffmpeg conversion (mocked subprocess) ------------------------------


class Mp3ToOgg(unittest.TestCase):
    def test_missing_ffmpeg(self):
        def _run(cmd, **kw):
            raise FileNotFoundError("ffmpeg")

        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(lv.VoiceError) as cm:
                lv.mp3_to_ogg("a.mp3", str(Path(d) / "a.ogg"), run=_run)
            self.assertIn("ffmpeg not found", str(cm.exception))

    def test_success(self):
        def _run(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"O" * 2000)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        with tempfile.TemporaryDirectory() as d:
            ogg = Path(d) / "a.ogg"
            lv.mp3_to_ogg("a.mp3", str(ogg), run=_run)
            self.assertTrue(ogg.exists())
            self.assertGreaterEqual(ogg.stat().st_size, lv.MIN_AUDIO_BYTES)

    def test_ffmpeg_failure_surfaces_stderr(self):
        def _run(cmd, **kw):
            raise subprocess.CalledProcessError(1, cmd, output=b"", stderr=b"boom")

        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(lv.VoiceError) as cm:
                lv.mp3_to_ogg("a.mp3", str(Path(d) / "a.ogg"), run=_run)
            self.assertIn("boom", str(cm.exception))


# ----- judge verdict parsing ----------------------------------------------


class JudgeVerdict(unittest.TestCase):
    def test_plain_json(self):
        ok, reason = lv.parse_judge_verdict(
            _judge_response('{"ok": true, "reason": "clean"}')
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "clean")

    def test_code_fenced(self):
        fenced = '```json\n{"ok": false, "reason": "wrong accent"}\n```'
        ok, reason = lv.parse_judge_verdict(_judge_response(fenced))
        self.assertFalse(ok)
        self.assertEqual(reason, "wrong accent")

    def test_surfaces_api_error(self):
        with self.assertRaises(lv.VoiceError) as cm:
            lv.parse_judge_verdict({"error": {"message": "bad key"}})
        self.assertIn("bad key", str(cm.exception))

    def test_non_json_raises(self):
        with self.assertRaises(lv.VoiceError):
            lv.parse_judge_verdict(_judge_response("I think it sounds fine honestly"))

    def test_build_judge_prompt_includes_expected_text(self):
        prompt = lv.build_judge_prompt("On it, Igor.")
        self.assertIn("On it, Igor.", prompt)
        self.assertIn("JSON", prompt)

    def test_build_judge_payload_has_inline_audio(self):
        parts = lv.build_judge_payload("p", "QUJD", "audio/ogg")["contents"][0]["parts"]
        self.assertEqual(parts[1]["inlineData"]["data"], "QUJD")
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "audio/ogg")


if __name__ == "__main__":
    unittest.main()
