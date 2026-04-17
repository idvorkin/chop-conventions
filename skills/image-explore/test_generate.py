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
    NEAR_MAGENTA_L1_TOLERANCE,
    _parse_srgb,
    _scan_border_for_magenta_seeds,
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


if __name__ == "__main__":
    unittest.main()
