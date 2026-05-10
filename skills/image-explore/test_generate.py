#!/usr/bin/env python3
"""Unit tests for generate.py pure helpers and the post-strip eval path.

Run with: python3 -m unittest test_generate.py

The eval tests shell out to ImageMagick to build tiny synthetic alpha
fixtures on the fly. If magick isn't on PATH the integration tests skip
themselves (keeps the fast-test loop green on dev machines without
ImageMagick installed).

Recraft API itself is NOT exercised here — that path requires a network
round trip and burns credits. The eval functions are general-purpose
alpha-mask checks that operate on any RGBA image, so the fixtures synth
their own pre-stripped images.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from generate import (  # noqa: E402
    HEALTHY_ALPHA_MAX_PCT,
    HEALTHY_ALPHA_MIN_PCT,
    INTERIOR_HOLE_CLOSE_RADIUS,
    _format_eval_card,
    eval_alpha,
    evaluate_strip,
)


MAGICK = shutil.which("magick") or shutil.which("convert")
REQUIRES_MAGICK = unittest.skipIf(MAGICK is None, "ImageMagick not installed")


def _draw_image(magick_bin, out_path, *args):
    """Build a synthetic PNG via magick. Args are extra magick command tokens."""
    assert magick_bin is not None, "magick must be resolved before calling _draw_image"
    cmd = [magick_bin, *args, str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)


@REQUIRES_MAGICK
class TestEvaluateStrip(unittest.TestCase):
    """evaluate_strip reports general-purpose alpha-mean metrics + warnings.

    The function is bg-removal-mechanism-agnostic — it just measures the
    alpha channel of the finished RGBA image, so the same thresholds
    apply whether the upstream stripper was Recraft, the old flood-fill,
    or anything else.
    """

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil as _sh

        _sh.rmtree(self.tmpdir, ignore_errors=True)

    def test_healthy_strip_reports_healthy_status_no_warning(self):
        # Pre-stripped image: 50% transparent (top half), 50% opaque green (bottom half).
        # Alpha mean should land in (HEALTHY_ALPHA_MIN_PCT, HEALTHY_ALPHA_MAX_PCT).
        path = self.tmpdir / "half-transparent.png"
        _draw_image(
            MAGICK,
            path,
            "-size",
            "100x100",
            "xc:none",
            "-fill",
            "#2E8B2E",
            "-draw",
            "rectangle 0,50 99,99",
        )
        metrics, warning = evaluate_strip(str(path))
        self.assertEqual(metrics["status"], "healthy")
        self.assertIsNone(warning)
        self.assertIsNotNone(metrics["alpha_mean_pct"])
        self.assertGreaterEqual(metrics["alpha_mean_pct"], HEALTHY_ALPHA_MIN_PCT)
        self.assertLessEqual(metrics["alpha_mean_pct"], HEALTHY_ALPHA_MAX_PCT)
        self.assertGreater(metrics["file_size_kb"], 0)

    def test_subject_eaten_reports_loud_warning(self):
        # Mostly transparent canvas with a 5x5 dot of opacity — alpha far below
        # the HEALTHY_ALPHA_MIN_PCT threshold. The "subject eaten" failure mode.
        path = self.tmpdir / "mostly-eaten.png"
        _draw_image(
            MAGICK,
            path,
            "-size",
            "100x100",
            "xc:none",
            "-fill",
            "#2E8B2E",
            "-draw",
            "rectangle 47,47 51,51",
        )
        metrics, warning = evaluate_strip(str(path))
        self.assertEqual(metrics["status"], "subject_eaten")
        self.assertIsNotNone(warning)
        self.assertIn("subject", warning.lower())
        self.assertLess(metrics["alpha_mean_pct"], HEALTHY_ALPHA_MIN_PCT)

    def test_nothing_stripped_reports_loud_warning(self):
        # All-opaque canvas written as PNG32 (alpha channel forced). Plain PNG
        # strips an all-opaque alpha channel as a size optimization, which would
        # make the read-back report alpha=0. After remove_background_recraft()
        # the output always has an alpha channel, so this fixture is closer to
        # the real runtime input to evaluate_strip.
        path = self.tmpdir / "all-opaque.png"
        assert MAGICK is not None, "REQUIRES_MAGICK should have skipped this test"
        subprocess.run(
            [MAGICK, "-size", "100x100", "xc:rgba(46,139,46,1.0)", f"PNG32:{path}"],
            check=True,
            capture_output=True,
        )
        metrics, warning = evaluate_strip(str(path))
        self.assertEqual(metrics["status"], "nothing_stripped")
        self.assertIsNotNone(warning)
        self.assertIn("fills the", warning.lower())
        self.assertGreater(metrics["alpha_mean_pct"], HEALTHY_ALPHA_MAX_PCT)

    def test_format_eval_card_includes_filename_and_metrics(self):
        card = _format_eval_card(
            "/tmp/raccoon.webp",
            {"alpha_mean_pct": 51.3, "file_size_kb": 74.0, "status": "healthy"},
            None,
        )
        self.assertIn("raccoon.webp", card)
        self.assertIn("51.3%", card)
        self.assertIn("74.0KB", card)
        self.assertIn("healthy", card)
        self.assertNotIn("WARN", card)

    def test_format_eval_card_surfaces_warning_on_second_line(self):
        card = _format_eval_card(
            "/tmp/raccoon.webp",
            {"alpha_mean_pct": 3.1, "file_size_kb": 13.0, "status": "subject_eaten"},
            "subject eaten — see /hill-climbing for the fix",
        )
        self.assertIn("subject_eaten", card)
        self.assertIn("\n  WARN:", card)
        self.assertIn("hill-climbing", card)


class TestEvalAlphaInteriorHoles(unittest.TestCase):
    """eval_alpha catches interior holes via mask-closing set difference.

    General-purpose alpha-mask check: works on any RGBA output regardless
    of how the bg was removed. The thin-channel test case below was the
    motivating regression for the close_radius/min_component_px tuning,
    and remains a useful guard against any stripper that drills narrow
    transparent paths through a silhouette.
    """

    def _build_rgba(self, opaque_mask):
        """Turn a bool opaque_mask into an RGBA array with flat alpha."""
        import numpy as np  # noqa: PLC0415

        h, w = opaque_mask.shape
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[..., :3][opaque_mask] = (128, 128, 128)  # neutral opaque fill
        rgba[..., 3] = np.where(opaque_mask, 255, 0)
        return rgba

    def _save_and_eval(self, rgba, **eval_kwargs):
        from PIL import Image  # noqa: PLC0415

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        Image.fromarray(rgba, mode="RGBA").save(path)
        try:
            return eval_alpha(path, **eval_kwargs)
        finally:
            Path(path).unlink(missing_ok=True)

    def _try_import_numpy(self):
        try:
            import numpy as np  # noqa: F401, PLC0415
            from PIL import Image  # noqa: F401, PLC0415
            from scipy.ndimage import label  # noqa: F401, PLC0415
        except ImportError:
            self.skipTest("eval_alpha deps (numpy/pillow/scipy) not installed")

    def test_clean_solid_silhouette_reports_zero_holes(self):
        """A solid opaque disc on a transparent field — no damage, zero holes."""
        self._try_import_numpy()
        import numpy as np  # noqa: PLC0415

        h = w = 200
        yy, xx = np.ogrid[:h, :w]
        opaque = (yy - h // 2) ** 2 + (xx - w // 2) ** 2 <= 60**2
        metrics = self._save_and_eval(self._build_rgba(opaque))
        self.assertEqual(metrics["interior_hole_px"], 0)
        self.assertEqual(metrics["interior_hole_largest_px"], 0)

    def test_preexisting_enclosed_hole_without_channel_is_not_flagged(self):
        """Enclosed transparent disc with no bleed channel — interior at
        both r=0 and r=1. Set-difference zeroes it out: could be design-
        intentional (donut hole, glasses rim), so give benefit of doubt.
        The metric targets bleed-through specifically.
        """
        self._try_import_numpy()
        import numpy as np  # noqa: PLC0415

        h = w = 200
        yy, xx = np.ogrid[:h, :w]
        body = (yy - h // 2) ** 2 + (xx - w // 2) ** 2 <= 80**2
        hole = (yy - h // 2) ** 2 + (xx - w // 2) ** 2 <= 20**2
        opaque = body & ~hole
        metrics = self._save_and_eval(self._build_rgba(opaque))
        self.assertEqual(metrics["interior_hole_px"], 0)
        self.assertEqual(metrics["interior_hole_largest_px"], 0)

    def test_design_gap_armpit_is_not_flagged(self):
        """Solid body with a 5-px-wide inlet from the border (like an
        armpit opening between arm and body). Interior_open treats the
        inlet as border-connected; closing at radius 1 narrows it but
        doesn't seal it (wider than 2*radius+1). Set-difference = 0.
        """
        self._try_import_numpy()
        import numpy as np  # noqa: PLC0415

        h = w = 200
        yy, xx = np.ogrid[:h, :w]
        opaque = (yy - h // 2) ** 2 + (xx - w // 2) ** 2 <= 80**2
        # Carve a 5-px-wide inlet from the top edge down into the body.
        inlet = (np.abs(xx - w // 2) <= 2) & (yy <= h // 2)
        opaque = opaque & ~inlet
        metrics = self._save_and_eval(self._build_rgba(opaque))
        self.assertEqual(metrics["interior_hole_px"], 0)

    def test_bleed_channel_hole_still_flagged_after_closing(self):
        """Hole connected to outside via a thin 1-pixel channel. Without
        closing, reports 0. With the default closing radius, the channel
        is sealed and the hole re-emerges. Original motivating case for
        the close_radius tuning (issue-#171); still a useful guard for
        any stripper that drills narrow channels through the silhouette.
        """
        self._try_import_numpy()
        import numpy as np  # noqa: PLC0415

        h = w = 200
        yy, xx = np.ogrid[:h, :w]
        body = (yy - h // 2) ** 2 + (xx - w // 2) ** 2 <= 80**2
        hole = (yy - h // 2) ** 2 + (xx - w // 2) ** 2 <= 20**2
        # 1-pixel-wide channel from the hole straight out through the body
        # to the image border. Emulates a bleed path.
        channel = (np.abs(xx - w // 2) <= 0) & (yy >= h // 2)
        opaque = body & ~hole & ~channel

        # Confirm the naïve (radius=0) detector misses it.
        metrics_naive = self._save_and_eval(self._build_rgba(opaque), close_radius=0)
        self.assertEqual(
            metrics_naive["interior_hole_px"],
            0,
            "test fixture is supposed to hide the hole behind a thin channel",
        )

        # Default radius must catch it.
        metrics = self._save_and_eval(self._build_rgba(opaque))
        hole_area = int(hole.sum())
        self.assertGreater(
            metrics["interior_hole_px"],
            hole_area * 0.5,
            f"closing (r={INTERIOR_HOLE_CLOSE_RADIUS}) should re-expose the hidden hole",
        )

    def test_tiny_speckles_below_min_component_are_filtered(self):
        """Single-pixel transparent dots inside opaque shouldn't register."""
        self._try_import_numpy()
        import numpy as np  # noqa: PLC0415

        h = w = 200
        yy, xx = np.ogrid[:h, :w]
        body = (yy - h // 2) ** 2 + (xx - w // 2) ** 2 <= 80**2
        opaque = body.copy()
        # Scatter 10 isolated 1-pixel interior transparent dots.
        for dy, dx in [
            (-40, 0),
            (-30, 10),
            (-20, -20),
            (-10, 15),
            (0, -30),
            (10, 25),
            (20, -5),
            (30, 20),
            (-5, 5),
            (5, -10),
        ]:
            opaque[h // 2 + dy, w // 2 + dx] = False
        metrics = self._save_and_eval(self._build_rgba(opaque))
        # Each speckle is 1 px (<100), so they should all be filtered.
        self.assertLess(metrics["interior_hole_largest_px"], 100)


if __name__ == "__main__":
    unittest.main()
