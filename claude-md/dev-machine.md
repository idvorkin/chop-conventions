# Dev-machine CLAUDE.md fragment — Tailscale-served hosts

Rules here apply only on machines served to the user over Tailscale — i.e.
the browser reaching dev servers is on a different device than the host
running `claude`. Machines that do not match this host class skip this
file entirely (the symlink is absent, the `@`-import silently no-ops).

This file is loaded via `@~/.claude/claude-md/dev-machine.md`, where the
symlink is created only on machines whose `diagnose.py` classifies them
as `dev_machine: true` (Tailscale installed **and** hostname matches the
dev-VM pattern).

## CPU-heavy ML / embedding work: nice + thread caps

Wrap CPU-heavy ML commands with BOTH a `nice` prefix AND a thread cap:

```bash
nice -n 19 ionice -c 3 env OMP_NUM_THREADS=2 ORT_NUM_THREADS=2 MKL_NUM_THREADS=2 <command>
```

`nice` alone does NOT cap absolute CPU — it only yields on contention. Thread caps are the real knob. Empirically 2 threads is often *faster* than default on consumer CPUs because the default thrashes. Applies to `uvx ... onnx-asr`, `fastembed`, `sentence-transformers`, local LLM inference, `ffmpeg` transcode loops, batch AI processing. Foreground interactive commands stay plain; anything >30s and CPU-heavy gets the full prefix.

This rule lives under `dev-machine.md` rather than `global.md` because `ionice` is Linux-only — the command errors on macOS. Dev VMs are where the heavy batch work actually runs.
