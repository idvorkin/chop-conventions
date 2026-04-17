#!/usr/bin/env python3
"""Unit tests for generate.py pure helpers and the remove_background pipeline.

Run with: python3 -m unittest test_generate.py

The remove_background / _scan_border_for_magenta_seeds tests shell out to
ImageMagick to build tiny synthetic fixtures on the fly. If magick isn't
on PATH the integration tests skip themselves (keeps the fast-test loop
green on dev machines without ImageMagick installed).
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from generate import (  # noqa: E402
    BORDER_SAMPLE_STEP,
    HEALTHY_ALPHA_MAX_PCT,
    HEALTHY_ALPHA_MIN_PCT,
    INTERIOR_HOLE_CLOSE_RADIUS,
    NEAR_MAGENTA_L1_TOLERANCE,
    _format_eval_card,
    _parse_srgb,
    _scan_border_for_magenta_seeds,
    eval_alpha,
    evaluate_strip,
    remove_background,
)


MAGICK = shutil.which("magick") or shutil.which("convert")
REQUIRES_MAGICK = unittest.skipIf(MAGICK is None, "ImageMagick not installed")


def _draw_image(magick_bin, out_path, *args):
    """Build a synthetic PNG via magick. Args are extra magick command tokens."""
    assert magick_bin is not None, "magick must be resolved before calling _draw_image"
    cmd = [magick_bin, *args, str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)


class TestParseSrgb(unittest.TestCase):
    """_parse_srgb handles the string forms magick emits via %[pixel:...]."""

    def test_srgb_triplet(self):
        self.assertEqual(_parse_srgb("srgb(255,0,255)"), (255, 0, 255))

    def test_srgba_quadruplet_returns_rgb_only(self):
        # magick sometimes emits srgba(r,g,b,a); we only need the first 3.
        self.assertEqual(_parse_srgb("srgba(68,72,43,255)"), (68, 72, 43))

    def test_leading_and_trailing_whitespace_tolerated(self):
        self.assertEqual(_parse_srgb("  srgb(10,20,30)  "), (10, 20, 30))

    def test_unrecognized_format_returns_none(self):
        self.assertIsNone(_parse_srgb("#FF00FF"))
        self.assertIsNone(_parse_srgb("rgb(1,2,3)"))
        self.assertIsNone(_parse_srgb(""))

    def test_garbage_inside_parens_returns_none(self):
        self.assertIsNone(_parse_srgb("srgb(not,a,number)"))
        self.assertIsNone(_parse_srgb("srgb(1,2)"))


class TestNearMagentaToleranceInvariants(unittest.TestCase):
    """The tolerance constant is deliberately narrower than the flood fuzz."""

    def test_pure_magenta_within_tolerance(self):
        r, g, b = 255, 0, 255
        self.assertLessEqual(
            abs(r - 255) + abs(g) + abs(b - 255), NEAR_MAGENTA_L1_TOLERANCE
        )

    def test_typical_grass_outside_tolerance(self):
        # Real failing sample from the raccoon-hill regression — grass pixel
        # that ended up in a corner and seeded the flood at 30% fuzz.
        r, g, b = 68, 72, 43
        self.assertGreater(
            abs(r - 255) + abs(g) + abs(b - 255), NEAR_MAGENTA_L1_TOLERANCE
        )


@REQUIRES_MAGICK
class TestScanBorderForMagentaSeeds(unittest.TestCase):
    """Seed scanner must find magenta pixels along the border and reject non-magenta."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # 200x200 keeps the test fast while still yielding many sample points
        # at the default BORDER_SAMPLE_STEP=8.
        self.size = 200

    def tearDown(self):
        import shutil as _sh

        _sh.rmtree(self.tmpdir, ignore_errors=True)

    def _scan(self, image_path):
        seeds, err = _scan_border_for_magenta_seeds(
            MAGICK, str(image_path), self.size, self.size
        )
        self.assertIsNone(err, f"unexpected scan error: {err}")
        return seeds

    def test_all_magenta_border_yields_many_seeds(self):
        # Pure magenta canvas — every sampled border pixel is a seed.
        path = Path(self.tmpdir) / "full-magenta.png"
        _draw_image(MAGICK, path, "-size", f"{self.size}x{self.size}", "xc:#FF00FF")
        seeds = self._scan(path)
        # With step=8 on a 200x200, we sample 4*(200/8) = 100 unique edge points
        # (some corner duplicates cancel because top/bottom/left/right each hit
        # every 8px independently). Seeds should be well over half.
        self.assertGreater(len(seeds), 50)

    def test_grass_in_bottom_corners_still_finds_top_seeds(self):
        # Reproduces the raccoon-hill regression: top half magenta, bottom
        # half grass. Scanner should seed from the top edge + the top portion
        # of left/right edges, and skip the bottom.
        path = Path(self.tmpdir) / "half-grass.png"
        _draw_image(
            MAGICK,
            path,
            "-size",
            f"{self.size}x{self.size}",
            "xc:#FF00FF",
            "-fill",
            "#44482b",  # grass color
            "-draw",
            f"rectangle 0,{self.size // 2} {self.size - 1},{self.size - 1}",
        )
        seeds = self._scan(path)
        self.assertGreater(
            len(seeds), 0, "should find seeds on the magenta portion of the border"
        )
        # No seed should come from the grass half (y > size/2).
        for x, y in seeds:
            self.assertLess(
                y,
                self.size // 2 + BORDER_SAMPLE_STEP,
                f"seed {(x, y)} landed in the grass half — scanner should reject it",
            )

    def test_no_magenta_on_border_returns_empty(self):
        # Solid green canvas — nothing on the border is near magenta.
        path = Path(self.tmpdir) / "no-magenta.png"
        _draw_image(MAGICK, path, "-size", f"{self.size}x{self.size}", "xc:#2E8B2E")
        seeds = self._scan(path)
        self.assertEqual(
            seeds, [], "subject-fills-frame case must return empty seed list"
        )


@REQUIRES_MAGICK
class TestRemoveBackgroundIntegration(unittest.TestCase):
    """End-to-end: remove_background on synthetic fixtures."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil as _sh

        _sh.rmtree(self.tmpdir, ignore_errors=True)

    def _alpha_mean_percent(self, path):
        assert MAGICK is not None, "REQUIRES_MAGICK should have skipped this test"
        out = subprocess.check_output(
            [MAGICK, str(path), "-format", "%[fx:mean.a*100]", "info:"], text=True
        )
        return float(out.strip())

    def test_magenta_border_colored_center_strips_cleanly(self):
        # 200x200 with a 40px solid magenta border on all four sides and a
        # solid green center. Post-strip: alpha mean should be ~ (center area
        # / total) = (120*120)/(200*200) = 36%.
        path = self.tmpdir / "bordered.png"
        _draw_image(
            MAGICK,
            path,
            "-size",
            "200x200",
            "xc:#FF00FF",
            "-fill",
            "#2E8B2E",
            "-draw",
            "rectangle 40,40 159,159",
        )
        ok, err = remove_background(str(path))
        self.assertTrue(ok, f"expected success, got error: {err}")
        alpha = self._alpha_mean_percent(path)
        self.assertGreater(alpha, 25.0, "subject was eaten")
        self.assertLess(alpha, 50.0, "too much background survived")

    def test_subject_fills_frame_returns_guarded_error(self):
        # Solid green canvas — no magenta on the border. Must NOT silently
        # strip (the old regression eats everything). Must return an error
        # mentioning how to fix the prompt.
        path = self.tmpdir / "no-magenta.png"
        _draw_image(MAGICK, path, "-size", "200x200", "xc:#2E8B2E")
        ok, err = remove_background(str(path))
        self.assertFalse(ok, "should refuse to strip when no border magenta")
        self.assertIsNotNone(err)
        self.assertIn("magenta", err.lower())

    def test_grass_in_bottom_corners_now_strips_instead_of_eating_subject(self):
        # The regression fixture: magenta top half, grass bottom half. Old
        # algorithm (4-corner flood) ate the whole canvas because the bottom
        # corners started the flood from grass. New algorithm must strip
        # only the top half.
        path = self.tmpdir / "half-grass.png"
        _draw_image(
            MAGICK,
            path,
            "-size",
            "200x200",
            "xc:#FF00FF",
            "-fill",
            "#44482b",
            "-draw",
            "rectangle 0,100 199,199",
        )
        ok, err = remove_background(str(path))
        self.assertTrue(ok, f"expected success, got error: {err}")
        alpha = self._alpha_mean_percent(path)
        # Top 100 rows were magenta (should become transparent), bottom 100
        # were grass (should remain opaque). Expect alpha ~50%.
        self.assertGreater(alpha, 40.0, "old-style failure mode — scanner ate too much")
        self.assertLess(alpha, 60.0, "scanner missed magenta on the top half")


@REQUIRES_MAGICK
class TestEvaluateStrip(unittest.TestCase):
    """evaluate_strip reports metrics + warnings; same thresholds as the integration tests."""

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
        # the HEALTHY_ALPHA_MIN_PCT threshold. Matches the regression failure mode.
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
        self.assertIn("magenta border", warning.lower())
        self.assertLess(metrics["alpha_mean_pct"], HEALTHY_ALPHA_MIN_PCT)

    def test_nothing_stripped_reports_loud_warning(self):
        # All-opaque canvas written as PNG32 (alpha channel forced). Plain PNG
        # strips an all-opaque alpha channel as a size optimization, which would
        # make the read-back report alpha=0. After remove_background() the
        # output always has an alpha channel, so this fixture is closer to the
        # real runtime input to evaluate_strip.
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
    """eval_alpha must catch flood-fill bleed-through channels.

    The issue-#171 failure: a real interior hole gets connected to the
    image border by a 1–2-pixel-wide transparent channel the flood-fill
    drilled through a narrow part of the character (neck, between legs).
    A naive "transparent pixels not touching border" check then reports
    zero holes. Morphological closing of the opaque mask seals the thin
    channels, and the hole re-emerges as an enclosed component.
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
        The metric targets flood-fill bleed-through specifically.
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
        """The issue-#171 failure mode: hole connected to outside via a
        thin 1-pixel channel. Without closing, reports 0. With the default
        closing radius, the channel is sealed and the hole re-emerges.
        """
        self._try_import_numpy()
        import numpy as np  # noqa: PLC0415

        h = w = 200
        yy, xx = np.ogrid[:h, :w]
        body = (yy - h // 2) ** 2 + (xx - w // 2) ** 2 <= 80**2
        hole = (yy - h // 2) ** 2 + (xx - w // 2) ** 2 <= 20**2
        # 1-pixel-wide channel from the hole straight out through the body
        # to the image border. Emulates a flood-fill bleed path.
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
