# SPDX-License-Identifier: GPL-3.0-or-later
#
# turing-smart-screen-python - a Python system monitor and library for USB-C displays like Turing Smart Screen or XuanFang
# https://github.com/mathoudebine/turing-smart-screen-python/
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Guard the theme designer's preview against silently drifting from the panel.

The designer's whole value is that its preview goes through the monitor's real
render pipeline, so what you see is what the panel shows. Two pieces of that
pipeline had to be duplicated to make an editor possible, and a duplicate is
exactly the kind of thing that rots quietly:

  * EditorLcd re-implements LcdSimulated.DisplayPILImage without the per-paste
    PNG write.
  * renderer._draw_static_images / _draw_static_text re-implement the loops in
    library/display.py, because those loop internally and a bounding box cannot
    be attributed to each entry from outside.

If either drifts, the preview stops telling the truth and nothing else in the
project would notice. These tests are the only thing that would.
"""

import copy
import unittest
from pathlib import Path

from PIL import Image, ImageChops

REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()
THEMES_DIR = REPO_ROOT / "res" / "themes"


def _bundled_themes():
    return sorted(p.parent for p in THEMES_DIR.glob("*/theme.yaml"))


def _identical(a: Image.Image, b: Image.Image) -> bool:
    if a.size != b.size or a.mode != b.mode:
        return False
    return ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is None


class TestEditorLcdParity(unittest.TestCase):
    """EditorLcd must paste exactly what LcdSimulated pastes."""

    def test_paste_matches_simulated(self):
        from library.editor import renderer
        renderer.render(THEMES_DIR / "LoopTelemetry")  # force bootstrap
        from library.lcd.lcd_comm import LcdComm

        # A stand-in for LcdSimulated carrying its ORIGINAL paste geometry,
        # minus only the webserver and the disk writes (which cannot affect
        # pixels). If EditorLcd's copy of that geometry ever drifts, the two
        # buffers stop matching.
        from library.lcd.lcd_simulated import LcdSimulated

        class ReferenceLcd(LcdSimulated):
            def __init__(self, width, height):
                LcdComm.__init__(self, "AUTO", width, height, None)
                self.screen_image = Image.new("RGB", (self.get_width(), self.get_height()), (255, 255, 255))
                self.webServer = None

            def __del__(self):
                pass

            def closeSerial(self):
                pass

            def DisplayPILImage(self, image, x=0, y=0, image_width=0, image_height=0):
                if not image_height:
                    image_height = image.size[1]
                if not image_width:
                    image_width = image.size[0]
                if image.size[1] > self.get_height():
                    image_height = self.get_height()
                if image.size[0] > self.get_width():
                    image_width = self.get_width()
                if image_width != image.size[0] or image_height != image.size[1]:
                    image = image.crop((0, 0, image_width, image_height))
                self.screen_image.paste(image, (x, y))

        editor = renderer._EditorLcd(320, 480)
        reference = ReferenceLcd(320, 480)

        swatch = Image.new("RGB", (60, 40), (200, 30, 90))
        oversized = Image.new("RGB", (400, 600), (10, 220, 120))
        for lcd in (editor, reference):
            lcd.DisplayPILImage(swatch, 10, 20)
            lcd.DisplayPILImage(swatch, 100, 200, 30, 15)   # explicit crop
            lcd.DisplayPILImage(oversized, 0, 0)            # larger than screen

        self.assertTrue(_identical(editor.screen_image, reference.screen_image),
                        "EditorLcd paste geometry has drifted from LcdSimulated")


class TestStaticLoopParity(unittest.TestCase):
    """The re-implemented static loops must match library/display.py exactly."""

    def test_static_loops_match_upstream(self):
        from library.editor import renderer
        renderer.render(THEMES_DIR / "LoopTelemetry")  # force bootstrap
        from library import config
        from library.display import display

        checked = 0
        mismatches = []
        for theme_dir in _bundled_themes():
            try:
                document = renderer.load_theme_document(theme_dir)
            except Exception:
                continue  # malformed bundled theme is not this test's business

            data = copy.deepcopy(document)
            config.copy_default(config.THEME_DEFAULT, data)
            data["PATH"] = str(theme_dir) + "/"
            if "display" not in data:
                continue

            from library.display import _get_theme_size

            # Ours: the attributed re-implementation.
            config.THEME_DATA = copy.deepcopy(data)
            display.lcd = renderer._EditorLcd(*_get_theme_size())
            display.initialize_display()
            renderer._draw_static_images(config.THEME_DATA)
            renderer._draw_static_text(config.THEME_DATA)
            ours = display.lcd.screen_image.copy()

            # Theirs: the untouched loops in library/display.py.
            config.THEME_DATA = copy.deepcopy(data)
            display.lcd = renderer._EditorLcd(*_get_theme_size())
            display.initialize_display()
            display.display_static_images()
            display.display_static_text()
            theirs = display.lcd.screen_image.copy()

            if not _identical(ours, theirs):
                mismatches.append(theme_dir.name)
            checked += 1

        self.assertGreater(checked, 20, "expected to exercise many bundled themes")
        self.assertEqual(mismatches, [], "static drawing drifted from library/display.py")


class TestRenderContract(unittest.TestCase):
    def setUp(self):
        self.theme = THEMES_DIR / "LoopTelemetry"

    def test_renders_expected_size_with_boxes_and_no_errors(self):
        from library.editor import renderer
        result = renderer.render(self.theme)
        self.assertEqual(result.image.size, (480, 320))
        self.assertEqual(result.errors, [])
        self.assertGreater(len(result.bboxes), 5)
        for element, box in result.bboxes.items():
            self.assertIsInstance(element, tuple)
            self.assertLess(box[0], box[2], "%s has a non-positive width" % (element,))
            self.assertLess(box[1], box[3], "%s has a non-positive height" % (element,))

    def test_does_not_mutate_the_caller_document(self):
        # library/stats.py sets theme_data['SHOW'] = False in 32 places when a
        # sensor reads NaN. If the renderer ever stops deep-copying, a user on
        # hardware lacking a sensor would silently lose SHOW: True from their
        # theme the next time the editor saved it.
        from library.editor import renderer
        document = renderer.load_theme_document(self.theme)
        before = copy.deepcopy(document)
        renderer.render(self.theme, document=document)
        self.assertEqual(document, before, "render() mutated the caller's document")

    def test_one_broken_element_does_not_abort_the_frame(self):
        # lcd_comm asserts that x <= display width. A user dragging an element
        # off the right edge must get a reported error and a still-usable
        # preview, not a half-drawn image.
        from library.editor import renderer
        document = renderer.load_theme_document(self.theme)
        document.setdefault("static_text", {})["__OFF_CANVAS__"] = {
            "TEXT": "off canvas", "X": 99999, "Y": 10,
        }
        result = renderer.render(self.theme, document=document)
        self.assertTrue(any(e.element == ("static_text", "__OFF_CANVAS__") for e in result.errors),
                        "the off-canvas element should have been reported: %s" % (result.errors,))
        self.assertEqual(result.image.size, (480, 320))
        self.assertIn(("static_images", "BACKGROUND"), result.bboxes,
                      "the rest of the frame should still have drawn")


if __name__ == "__main__":
    unittest.main()
