"""Unit tests for the pure parts of parakeet-stt.py.

No ffmpeg, onnx-asr or model download: every subprocess-touching helper is
patched.
"""

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "parakeet_stt", Path(__file__).resolve().parent / "parakeet-stt.py"
)
stt = importlib.util.module_from_spec(_SPEC)
sys.modules["parakeet_stt"] = stt
_SPEC.loader.exec_module(stt)


def test_nice_prefix_omits_ionice_when_absent():
    prefix = stt.build_nice_prefix(which=lambda _name: None)
    assert "ionice" not in prefix
    assert prefix[:3] == ["nice", "-n", "19"]
    assert prefix[3] == "env"
    assert "OMP_NUM_THREADS=2" in prefix


def test_nice_prefix_keeps_ionice_when_present():
    prefix = stt.build_nice_prefix(which=lambda name: f"/usr/bin/{name}")
    assert prefix[:6] == ["nice", "-n", "19", "ionice", "-c", "3"]


def test_default_output_path_replaces_suffix():
    assert stt.default_output_path(Path("/d/clip.wav"), False, None) == Path(
        "/d/clip.txt"
    )
    assert stt.default_output_path(Path("/d/clip.wav"), True, Path("/o")) == Path(
        "/o/clip.json"
    )


def test_assign_output_paths_same_stem_do_not_collide():
    inputs = [Path("/d/a.wav"), Path("/d/a.mp3"), Path("/d/b.ogg")]
    outs = stt.assign_output_paths(inputs, False, None)
    assert outs == [Path("/d/a.wav.txt"), Path("/d/a.mp3.txt"), Path("/d/b.txt")]
    assert len(set(outs)) == len(inputs)


def test_assign_output_paths_same_name_from_two_dirs():
    inputs = [Path("/x/a.wav"), Path("/y/a.wav"), Path("/z/a.wav.flac")]
    outs = stt.assign_output_paths(inputs, True, Path("/o"))
    assert len(set(outs)) == 3
    assert all(o.parent == Path("/o") and o.suffix == ".json" for o in outs)


def _job(tmp_path, name="a.mp3"):
    src = tmp_path / name
    src.write_bytes(b"not audio")
    return stt.STTJob(
        input_path=src,
        output_path=tmp_path / "out" / "a.txt",
        json_mode=False,
        keep_wav=False,
        model="m",
    )


def test_transcribe_one_reports_oserror_instead_of_raising(tmp_path, monkeypatch):
    def boom(_input, _wav):
        raise FileNotFoundError(2, "No such file or directory", "nice")

    monkeypatch.setattr(stt, "_transcode_to_16k_mono", boom)
    work_root = tmp_path / "work"
    work_root.mkdir()
    result = stt.transcribe_one(_job(tmp_path), work_root)
    assert result.success is False
    assert "FileNotFoundError" in result.error
    assert list(work_root.iterdir()) == []  # per-job dir cleaned up


def test_transcribe_one_same_stem_same_timestamp_jobs_both_succeed(
    tmp_path, monkeypatch
):
    seen = []

    def fake_transcode(_input, wav_path):
        seen.append(wav_path.parent)
        wav_path.write_bytes(b"")

    monkeypatch.setattr(stt, "_transcode_to_16k_mono", fake_transcode)
    monkeypatch.setattr(stt, "_probe_duration_s", lambda _p: 1.0)
    monkeypatch.setattr(stt, "_run_onnx_asr", lambda _w, _m: ("hello", ""))
    monkeypatch.setattr(stt.time, "monotonic", lambda: 5.0)  # identical timestamps
    work_root = tmp_path / "work"
    work_root.mkdir()
    r1 = stt.transcribe_one(_job(tmp_path, "a.mp3"), work_root)
    r2 = stt.transcribe_one(_job(tmp_path, "a.ogg"), work_root)
    assert r1.success and r2.success
    assert r1.output_path.read_text() == "hello\n"
    assert len(seen) == 2
    assert list(work_root.iterdir()) == []
