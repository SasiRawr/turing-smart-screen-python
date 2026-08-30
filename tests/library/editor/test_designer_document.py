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

"""ThemeDocument round-trip guarantees.

The two invariants that make the designer safe to point at hand-authored
themes:

  1. Loading a theme and saving it with no edits reproduces the file
     byte-for-byte -- for EVERY bundled theme, including the ones with
     three-space indents, trailing spaces and CRLF line endings that a plain
     ruamel dump would normalize.
  2. Editing one scalar changes exactly one line; every comment (including
     inline comments on the edited line) survives.
"""

import difflib
import shutil
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

from library.editor.designer import commands  # noqa: E402
from library.editor.designer.document import ThemeDocument, is_bool  # noqa: E402

THEMES_DIR = REPO_ROOT / "res" / "themes"

INT_POSITION_KEYS = ("X", "Y", "WIDTH", "HEIGHT")


def bundled_theme_dirs():
    return sorted(
        (p for p in THEMES_DIR.iterdir() if (p / "theme.yaml").is_file()),
        key=lambda p: p.name.casefold())


def first_int_leaf(node, path=()):
    """Depth-first search for an X/Y/WIDTH/HEIGHT int leaf to edit."""
    for key, value in node.items():
        if isinstance(value, dict):
            found = first_int_leaf(value, path + (str(key),))
            if found is not None:
                return found
        elif key in INT_POSITION_KEYS and isinstance(value, int) \
                and not is_bool(value):
            return path, str(key), value
    return None


def changed_lines(before: str, after: str):
    """(removed, added) line lists between two texts."""
    removed, added = [], []
    matcher = difflib.SequenceMatcher(None, before.split("\n"), after.split("\n"))
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed.extend(before.split("\n")[a0:a1])
        if tag in ("replace", "insert"):
            added.extend(after.split("\n")[b0:b1])
    return removed, added


class TestRoundTripEveryTheme(unittest.TestCase):
    """The headline guarantee: load -> save is byte-identical, no exceptions."""

    def test_unedited_save_is_byte_identical_for_every_bundled_theme(self):
        themes = bundled_theme_dirs()
        self.assertGreater(len(themes), 50, "theme discovery is broken")
        failures = []
        for theme_dir in themes:
            original = (theme_dir / "theme.yaml").read_bytes()
            try:
                document = ThemeDocument(theme_dir)
            except Exception as exc:  # noqa: BLE001 - collect, then report all
                failures.append("%s: load failed: %s" % (theme_dir.name, exc))
                continue
            if document.dump_bytes() != original:
                failures.append("%s: bytes differ after no-op round trip"
                                % theme_dir.name)
        self.assertEqual([], failures,
                         "themes that do not round-trip:\n" + "\n".join(failures))

    def test_single_edit_changes_exactly_one_line_in_every_bundled_theme(self):
        """Stronger than the no-op test: after editing ONE scalar, the diff
        against the original file is exactly one changed line, and the file
        re-parses to the edited value."""
        failures = []
        for theme_dir in bundled_theme_dirs():
            document = ThemeDocument(theme_dir)
            target = first_int_leaf(document.data)
            if target is None:
                failures.append("%s: no int leaf found to edit" % theme_dir.name)
                continue
            path, key, old_value = target
            commands.SetProperty(path, key, old_value, old_value + 7).apply(document.data)

            before = document.yaml_path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n")
            after = document.dump_text()
            removed, added = changed_lines(before, after)
            if len(removed) != 1 or len(added) != 1:
                failures.append("%s: edit of %s.%s changed %d/%d lines"
                                % (theme_dir.name, ".".join(path), key,
                                   len(removed), len(added)))
                continue
            if key not in added[0] or str(old_value + 7) not in added[0]:
                failures.append("%s: changed line %r does not carry the edit"
                                % (theme_dir.name, added[0]))
                continue
            # And the result must actually parse back to the edited document.
            reloaded_value = _dig(_parse(after), path)[key]
            if int(reloaded_value) != old_value + 7:
                failures.append("%s: reloaded value is %r, expected %d"
                                % (theme_dir.name, reloaded_value, old_value + 7))
        self.assertEqual([], failures,
                         "themes where one edit did not mean one line:\n"
                         + "\n".join(failures))


def _parse(text):
    from library.editor.designer.document import make_yaml
    return make_yaml().load(text)


def _dig(node, path):
    for key in path:
        node = node[key]
    return node


class TestCommentSurvival(unittest.TestCase):
    """LoopTelemetry's WIDTH: 148 carries a six-line comment explaining why it
    is 148. If that comment does not survive an edit of that very value, the
    save path is wrong."""

    COMMENT_MARKER = "# 148, not 172: the delta-T radial is centred at X 372"

    def find_commented_width_node(self, data):
        stack = [((), data)]
        while stack:
            path, node = stack.pop()
            for key, value in node.items():
                if isinstance(value, dict):
                    if value.get("WIDTH") == 148 and "LINE_GRAPH" in str(key):
                        return path + (str(key),)
                    stack.append((path + (str(key),), value))
        return None

    def test_editing_the_commented_value_keeps_the_comment(self):
        document = ThemeDocument(THEMES_DIR / "LoopTelemetry")
        original = document.dump_text()
        self.assertIn(self.COMMENT_MARKER, original, "fixture comment moved?")

        path = self.find_commented_width_node(document.data)
        self.assertIsNotNone(path, "no LINE_GRAPH with WIDTH 148 found")
        commands.SetProperty(path, "WIDTH", 148, 150).apply(document.data)

        after = document.dump_text()
        self.assertIn(self.COMMENT_MARKER, after)
        for line in ("# so it occupies x 328-416.",
                     "# opaque box over its own bounds",
                     "# perfect in a single-pass preview."):
            self.assertTrue(any(line in out for out in after.split("\n")),
                            "comment line lost: %s" % line)
        removed, added = changed_lines(original, after)
        self.assertEqual((1, 1), (len(removed), len(added)))
        self.assertIn("WIDTH: 150", added[0])

    def test_inline_comment_survives_edit_of_its_own_line(self):
        """theme_example.yaml has 'ANCHOR: lt   # Check https://...' -- the
        comment shares the edited line and must survive."""
        with tempfile.TemporaryDirectory() as scratch:
            theme_dir = Path(scratch) / "InlineComment"
            theme_dir.mkdir()
            (theme_dir / "theme.yaml").write_text(
                "static_text:\n"
                "  LABEL:\n"
                "    TEXT: hello\n"
                "    ANCHOR: lt   # Check the PIL text-anchor docs\n",
                encoding="utf8")
            document = ThemeDocument(theme_dir)
            commands.SetProperty(("static_text", "LABEL"), "ANCHOR",
                                 document.data["static_text"]["LABEL"]["ANCHOR"],
                                 "mm").apply(document.data)
            after = document.dump_text()
            self.assertIn("ANCHOR: mm   # Check the PIL text-anchor docs", after)


class TestSaveMechanics(unittest.TestCase):

    def _copy_theme(self, scratch, name="LoopTelemetry"):
        target = Path(scratch) / name
        target.mkdir()
        shutil.copyfile(THEMES_DIR / name / "theme.yaml", target / "theme.yaml")
        return target

    def test_save_writes_atomically_and_rebaselines(self):
        with tempfile.TemporaryDirectory() as scratch:
            theme_dir = self._copy_theme(scratch)
            document = ThemeDocument(theme_dir)
            commands.SetProperty(("static_text", "CPU_LABEL"), "X", 20, 26) \
                .apply(document.data)
            self.assertTrue(document.is_modified())
            document.save()
            self.assertFalse(document.is_modified())
            # No temp litter left behind.
            leftovers = [p for p in theme_dir.iterdir() if p.suffix == ".tmp"]
            self.assertEqual([], leftovers)
            # Saved file re-parses to the edited value...
            reloaded = ThemeDocument(theme_dir)
            self.assertEqual(26, reloaded.data["static_text"]["CPU_LABEL"]["X"])
            # ...and a second unedited save is byte-stable.
            before = (theme_dir / "theme.yaml").read_bytes()
            reloaded.save()
            self.assertEqual(before, (theme_dir / "theme.yaml").read_bytes())

    def test_crlf_theme_stays_crlf_after_an_edit(self):
        with tempfile.TemporaryDirectory() as scratch:
            theme_dir = self._copy_theme(scratch, "3.5inchTheme2")
            original = (theme_dir / "theme.yaml").read_bytes()
            self.assertIn(b"\r\n", original, "fixture is no longer CRLF")
            document = ThemeDocument(theme_dir)
            target = first_int_leaf(document.data)
            path, key, old_value = target
            commands.SetProperty(path, key, old_value, old_value + 1) \
                .apply(document.data)
            document.save()
            saved = (theme_dir / "theme.yaml").read_bytes()
            self.assertIn(b"\r\n", saved)
            self.assertEqual(saved.count(b"\n"), saved.count(b"\r\n"),
                             "some lines lost their CR")

    def test_structural_change_falls_back_but_keeps_comments(self):
        """Adding a key cannot be a line splice; the full-dump fallback must
        still keep the hand-written comments."""
        with tempfile.TemporaryDirectory() as scratch:
            theme_dir = self._copy_theme(scratch)
            document = ThemeDocument(theme_dir)
            document.data["static_text"]["CPU_LABEL"]["WIDTH"] = 120  # new key
            after = document.dump_text()
            self.assertIn("# 148, not 172", after)
            self.assertIn("WIDTH: 120", after)
            # The result must re-parse to the live document (verified inside
            # dump_text; this asserts it end-to-end).
            reloaded = _parse(after)
            self.assertEqual(120, reloaded["static_text"]["CPU_LABEL"]["WIDTH"])

    def test_string_edit_keeps_quoting_style(self):
        with tempfile.TemporaryDirectory() as scratch:
            theme_dir = Path(scratch) / "Quoted"
            theme_dir.mkdir()
            (theme_dir / "theme.yaml").write_text(
                'static_text:\n'
                '  LABEL:\n'
                '    TEXT: "CPU"\n'
                '    FONT_COLOR: 90, 160, 185\n',
                encoding="utf8")
            document = ThemeDocument(theme_dir)
            node = document.data["static_text"]["LABEL"]
            commands.SetProperty(("static_text", "LABEL"), "TEXT",
                                 node["TEXT"], "GPU").apply(document.data)
            commands.SetProperty(("static_text", "LABEL"), "FONT_COLOR",
                                 node["FONT_COLOR"], "10, 20, 30").apply(document.data)
            after = document.dump_text()
            self.assertIn('TEXT: "GPU"', after)          # quotes preserved
            self.assertIn("FONT_COLOR: 10, 20, 30", after)  # plain preserved

    def test_bool_edit_uses_repo_capitalization(self):
        with tempfile.TemporaryDirectory() as scratch:
            theme_dir = self._copy_theme(scratch)
            document = ThemeDocument(theme_dir)
            node = _dig(document.data, ("STATS", "CPU", "PERCENTAGE", "TEXT"))
            self.assertTrue(is_bool(node["SHOW"]))
            commands.SetProperty(("STATS", "CPU", "PERCENTAGE", "TEXT"),
                                 "SHOW", node["SHOW"], False).apply(document.data)
            after = document.dump_text()
            self.assertNotIn("SHOW: false", after)
            before = document._pristine_text
            removed, added = changed_lines(before, after)
            self.assertEqual(1, len(added))
            self.assertIn("SHOW: False", added[0])


class TestDuplicate(unittest.TestCase):

    def test_duplicate_copies_assets_and_carries_edits(self):
        with tempfile.TemporaryDirectory() as scratch:
            source = Path(scratch) / "Original"
            source.mkdir()
            (source / "theme.yaml").write_text(
                "static_text:\n  LABEL:\n    TEXT: hi\n    X: 5\n",
                encoding="utf8")
            (source / "background.png").write_bytes(b"not-really-a-png")

            document = ThemeDocument(source)
            commands.SetProperty(("static_text", "LABEL"), "X", 5, 9) \
                .apply(document.data)
            target = document.duplicate("Copy Of Original")

            self.assertEqual(Path(scratch) / "Copy Of Original", target)
            self.assertEqual(b"not-really-a-png",
                             (target / "background.png").read_bytes())
            # The copy holds the edit; the original file does not.
            self.assertIn("X: 9", (target / "theme.yaml").read_text(encoding="utf8"))
            self.assertIn("X: 5", (source / "theme.yaml").read_text(encoding="utf8"))

    def test_duplicate_rejects_bad_and_existing_names(self):
        with tempfile.TemporaryDirectory() as scratch:
            source = Path(scratch) / "Original"
            source.mkdir()
            (source / "theme.yaml").write_text("static_text:\n  A:\n    X: 1\n",
                                               encoding="utf8")
            document = ThemeDocument(source)
            for bad in ("", "  ", "..", "a/b", "a\\b", "a:b", "a?b"):
                with self.assertRaises(ValueError):
                    document.duplicate(bad)
            document.duplicate("Twin")
            with self.assertRaises(ValueError):
                document.duplicate("Twin")


if __name__ == "__main__":
    unittest.main()
