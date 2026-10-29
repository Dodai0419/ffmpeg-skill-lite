"""Rules ported from ffmpeg-skill's tests (test_editing.py), plus end-to-end runs on a synthetic clip."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import ffmpeg_lite as f  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


class Rules(unittest.TestCase):
    def test_keep_ranges_is_monotone_with_a_nested_span(self):
        nested = f.keep_ranges(sorted([(1.0, 3.0), (1.5, 1.8)]), 10, 0.05, 0.2)
        self.assertEqual(nested, f.keep_ranges([(1.0, 3.0)], 10, 0.05, 0.2))
        self.assertEqual(f.keep_ranges([(4.0, 5.0), (1.0, 2.0)], 10, 0.05, 0.2),
                         f.keep_ranges([(1.0, 2.0), (4.0, 5.0)], 10, 0.05, 0.2))

    def test_keep_ranges_margins_and_trailing_silence(self):
        self.assertEqual(f.keep_ranges([(2.0, 4.0), (9.0, float("inf"))], 10, 0.15, 0.2),
                         [(0.0, 2.15), (3.85, 9.15)])

    def test_frame_size_matches_fit_py(self):
        self.assertEqual(f.frame_size(Fraction(9, 16), None, None, 1280, 720), (406, 720))  # bounded by height
        self.assertEqual(f.frame_size(Fraction(9, 16), 540, None, 1280, 720), (540, 960))
        self.assertEqual(f.frame_size(Fraction(1, 1), None, None, 1280, 720), (720, 720))
        self.assertEqual(f.frame_size(Fraction(21, 9), None, None, 1280, 720), (1280, 550))
        self.assertEqual(f.frame_size(None, 640, None, 1280, 720), (640, 360))

    def test_atempo_chain_stays_in_range(self):
        self.assertEqual(f.atempo_chain(3.0), "atempo=2.0,atempo=1.500000")
        self.assertEqual(f.atempo_chain(0.25), "atempo=0.5,atempo=0.500000")


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.src = os.path.join(cls.dir, "src.mp4")
        tone = "if(between(t,2.5,4)+between(t,6,7.5),0,0.4*sin(2*PI*440*t))"
        f.ffmpeg("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=10", "-f", "lavfi",
                 "-i", f"aevalsrc='{tone}':s=48000:d=10", "-shortest", *f.ENC, cls.src)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def cli(self, *args):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "ffmpeg_lite.py"), *args], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def test_silence_removes_the_gaps(self):
        plan = self.cli("silence", self.src, "--list")
        self.assertEqual(len(plan["silences"]), 2)
        self.assertAlmostEqual(plan["removed_seconds"], 2 * (1.5 - 0.3), delta=0.15)
        out = os.path.join(self.dir, "tight.mp4")
        r = self.cli("silence", self.src, "-o", out)
        self.assertAlmostEqual(r["output"]["duration"], 10 - plan["removed_seconds"], delta=0.15)
        self.assertIn("audio", r["output"])

    def test_fit_vertical_crop_bounded_by_height(self):
        r = self.cli("fit", self.src, "--aspect", "9:16", "-o", os.path.join(self.dir, "v.mp4"))
        self.assertEqual((r["output"]["video"]["width"], r["output"]["video"]["height"]), (406, 720))

    def test_fit_duration_speed_and_aspect_pad(self):
        r = self.cli("fit", self.src, "--duration", "6", "--aspect", "9:16", "--fit", "pad", "--width", "540",
                     "-o", os.path.join(self.dir, "fit1.mp4"))
        self.assertAlmostEqual(r["output"]["duration"], 6.0, delta=0.15)
        self.assertEqual((r["output"]["video"]["width"], r["output"]["video"]["height"]), (540, 960))

    def test_fit_trim_from_center_square(self):
        r = self.cli("fit", self.src, "--duration", "4", "--method", "trim", "--from-center", "--aspect", "1:1",
                     "--fit", "blur", "-o", os.path.join(self.dir, "sq.mp4"))
        self.assertAlmostEqual(r["output"]["duration"], 4.0, delta=0.15)
        self.assertEqual(r["output"]["video"]["width"], r["output"]["video"]["height"])

    def test_cut_copy_and_accurate(self):
        r = self.cli("cut", self.src, "--from", "2", "--to", "5", "--accurate", "-o", os.path.join(self.dir, "c.mp4"))
        self.assertAlmostEqual(r["output"]["duration"], 3.0, delta=0.1)
        r = self.cli("cut", self.src, "--from", "0", "--to", "4", "-o", os.path.join(self.dir, "c2.mp4"))
        self.assertEqual(r["mode"], "copy")
        self.assertAlmostEqual(r["output"]["duration"], 4.0, delta=0.6)

    def test_look_writes_a_sheet(self):
        out = os.path.join(self.dir, "s.jpg")
        r = self.cli("look", self.src, "-o", out)
        self.assertEqual(r["frames"], 12)
        self.assertGreater(os.path.getsize(out), 5000)

    def test_missing_file_is_a_clean_error(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "ffmpeg_lite.py"), "probe", "nope.mp4"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(json.loads(r.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
