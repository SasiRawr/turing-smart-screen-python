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

"""The editable theme document: ruamel.yaml round-trip load and a save path
that leaves everything the user did not touch byte-for-byte alone.

WHY SAVING IS A PATCH, NOT A DUMP

Themes are hand-authored files with comments that explain *why* values are
what they are, and a dumb re-serialize destroys exactly the details that make
them hand-authored. Measured against all 74 bundled themes, a straight ruamel
round-trip dump reproduces only 24 byte-identically; the other 50 differ in
cosmetic ways ruamel cannot represent: three-space indents (the
NZXT/Cyberpunk family), trailing spaces after values ('FONT_SIZE: 25 '),
doubled spaces after colons ('PATH:  background.png'), whitespace-only lines,
missing final newlines, and comments above the '---' document marker
(CustomDataExample), which ruamel silently drops.

So save() never rewrites the file wholesale when it can avoid it. It diffs
the live document against a pristine parse of the on-disk text, locates each
changed scalar with ruamel's line/column bookkeeping, and splices the new
value into the original line -- preserving indentation, inline comments and
even trailing whitespace on every line it does not own. A document with no
changes serializes to the original bytes, by construction, for all 74 themes.

The full round-trip dump still exists as the fallback for structural edits
(keys added or removed), where a line splice is not well-defined. That path
preserves comments and key order (ruamel 'rt' mode) but may normalize the
cosmetic quirks above -- and every save, patched or dumped, is verified by
re-parsing the result and comparing it to the live document before anything
is written. Writes go to a temp file in the theme directory followed by
os.replace(), so an interrupted save can never leave a half-written theme.
"""

import io
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, List, Optional, Tuple

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedSeq
from ruamel.yaml.scalarbool import ScalarBoolean
from ruamel.yaml.scalarstring import (DoubleQuotedScalarString,
                                      SingleQuotedScalarString)

ElementPath = Tuple[str, ...]

_UTF8_BOM = b"\xef\xbb\xbf"

# A '---' document-start marker, possibly preceded by comment and blank lines.
_EXPLICIT_START_RE = re.compile(r"^(?:[ \t]*(?:#[^\n]*)?\n)*---(?:[ \t\n]|$)")


def is_bool(value: Any) -> bool:
    """True for plain bools AND ruamel ScalarBooleans.

    The loader turns every YAML bool into a ScalarBoolean so 'True' can be
    written back as 'True' (this repo's convention) instead of ruamel's
    lowercase 'true'. ScalarBoolean subclasses int, not bool, so a plain
    isinstance(value, bool) misclassifies every SHOW/AUTOSCALE/CLOCKWISE flag.
    """
    return isinstance(value, (bool, ScalarBoolean))


def make_yaml(explicit_start: bool = False) -> YAML:
    """A round-trip YAML instance tuned for theme files."""
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    # ruamel wraps lines at 80 columns by default, which would split long
    # hand-written scalars and comments. Effectively disable wrapping.
    yaml.width = 2 ** 31
    yaml.explicit_start = explicit_start

    # Round-trip booleans keeping their exact source spelling (True/TRUE/true).
    def construct_bool(constructor, node):
        value = ScalarBoolean(bool(constructor.construct_yaml_bool(node)))
        value.original = node.value
        return value

    yaml.constructor.add_constructor("tag:yaml.org,2002:bool", construct_bool)

    def represent_scalar_bool(representer, data):
        text = getattr(data, "original", None) or ("True" if data else "False")
        return representer.represent_scalar("tag:yaml.org,2002:bool", text)

    yaml.representer.add_representer(ScalarBoolean, represent_scalar_bool)
    # New bools written by the editor are plain Python bools; capitalize them
    # to match the convention used across the bundled themes.
    yaml.representer.add_representer(
        bool, lambda representer, data: representer.represent_scalar(
            "tag:yaml.org,2002:bool", "True" if data else "False"))
    return yaml


class ThemeSaveError(RuntimeError):
    """The document could not be serialized or written safely."""


class ThemeDocument:
    """One theme.yaml, loaded for editing.

    data          -- the live CommentedMap the designer mutates
    is_modified() -- value/structure comparison against the on-disk state
    dump_text()   -- what save() would write, as LF-normalized text
    save()        -- atomic write (temp file + os.replace) preserving CRLF/BOM
    duplicate()   -- copy the whole theme directory, current edits included
    """

    def __init__(self, theme_dir):
        self.theme_dir = Path(theme_dir)
        self.yaml_path = self.theme_dir / "theme.yaml"

        raw = self.yaml_path.read_bytes()
        self._bom = raw.startswith(_UTF8_BOM)
        text = raw.decode("utf-8-sig")
        # Newline style is restored on save; ruamel and the patcher both work
        # in LF internally.
        self._newline = "\r\n" if "\r\n" in text else "\n"
        self._pristine_text = text.replace("\r\n", "\n")
        self._explicit_start = bool(_EXPLICIT_START_RE.match(self._pristine_text))

        self.data = self._load(self._pristine_text)
        # A second, never-mutated parse: its values are the diff baseline and
        # its .lc line/column data locates each scalar in _pristine_text.
        self._pristine_doc = self._load(self._pristine_text)

    def _load(self, text: str):
        data = make_yaml(self._explicit_start).load(text)
        if not isinstance(data, dict):
            raise ValueError("theme.yaml did not parse to a mapping")
        return data

    # ------------------------------------------------------------------ diff

    def is_modified(self) -> bool:
        changes, structural = self._diff()
        return structural or bool(changes)

    def _diff(self) -> Tuple[List[Tuple[ElementPath, Any, Any]], bool]:
        """(changed scalar leaves, structural-change flag) vs on-disk state."""
        changes: List[Tuple[ElementPath, Any, Any]] = []
        structural = not _walk_diff(self._pristine_doc, self.data, (), changes)
        return changes, structural

    # ------------------------------------------------------------------ dump

    def dump_text(self) -> str:
        """The exact text save() would write (LF newlines)."""
        changes, structural = self._diff()
        if not structural and not changes:
            return self._pristine_text
        if not structural:
            patched = self._patch_lines(changes)
            if patched is not None and self._reparses_to_live(patched):
                return patched
        dumped = self._full_dump()
        if not self._reparses_to_live(dumped):
            raise ThemeSaveError(
                "serialized theme did not re-parse to the edited document; "
                "refusing to write it")
        return dumped

    def dump_bytes(self) -> bytes:
        raw = self.dump_text().replace("\n", self._newline).encode("utf8")
        return _UTF8_BOM + raw if self._bom else raw

    def _patch_lines(self, changes) -> Optional[str]:
        """Splice each changed scalar into its original line. None = cannot."""
        lines = self._pristine_text.split("\n")
        used_rows = set()
        for path, old, new in changes:
            try:
                parent = _resolve(self._pristine_doc, path[:-1])
                row, col = parent.lc.value(path[-1])
            except Exception:
                return None
            if row in used_rows or not 0 <= row < len(lines):
                return None
            used_rows.add(row)
            line = lines[row]
            if col > len(line):
                return None
            span = _value_span(line[col:])
            if span is None:
                return None
            _value_text, suffix = span
            rendered = self._render_scalar(new, old)
            if rendered is None:
                return None
            lines[row] = line[:col] + rendered + suffix
        return "\n".join(lines)

    def _render_scalar(self, value, old) -> Optional[str]:
        """Render one scalar the way it should appear in the file."""
        if is_bool(value):
            return "True" if value else "False"
        # Keep the old value's quoting style when replacing a string.
        if isinstance(value, str) and not isinstance(
                value, (DoubleQuotedScalarString, SingleQuotedScalarString)):
            if isinstance(old, DoubleQuotedScalarString):
                value = DoubleQuotedScalarString(value)
            elif isinstance(old, SingleQuotedScalarString):
                value = SingleQuotedScalarString(value)
        if isinstance(value, (list, tuple)):
            sequence = CommentedSeq(list(value))
            sequence.fa.set_flow_style()  # [r, g, b] on one line, not a block
            value = sequence
        # Let ruamel decide plain vs quoted: dump {K: value} and slice the
        # value back out. This handles strings that would otherwise re-parse
        # as numbers or bools, commas in colour strings, etc.
        buffer = io.StringIO()
        make_yaml().dump({"K": value}, buffer)
        dumped = buffer.getvalue()
        if not dumped.startswith("K:") or "\n" in dumped[:-1] or not dumped.endswith("\n"):
            return None  # multi-line rendering cannot be spliced into one line
        return dumped[2:-1].lstrip(" ")

    def _full_dump(self) -> str:
        buffer = io.StringIO()
        make_yaml(self._explicit_start).dump(self.data, buffer)
        return buffer.getvalue()

    def _reparses_to_live(self, text: str) -> bool:
        """A save candidate must re-parse to exactly the live document."""
        try:
            reparsed = self._load(text)
        except Exception:
            return False
        changes: List[Tuple[ElementPath, Any, Any]] = []
        return _walk_diff(reparsed, self.data, (), changes) and not changes

    # ------------------------------------------------------------------ save

    def save(self) -> None:
        """Atomically write the document to theme.yaml."""
        payload = self.dump_bytes()  # serialize (and verify) before touching disk
        written_text = self.dump_text()
        _atomic_write(self.yaml_path, payload)
        # The file now holds written_text; make it the new diff baseline.
        self._pristine_text = written_text
        self._pristine_doc = self._load(self._pristine_text)

    def save_copy_to(self, target_yaml_path) -> None:
        """Write the current document (edits included) to another path,
        without touching this document's own baseline."""
        _atomic_write(Path(target_yaml_path), self.dump_bytes())

    # ------------------------------------------------------------- duplicate

    def duplicate(self, new_name: str) -> Path:
        """Copy the whole theme directory (assets included) to a sibling
        directory named new_name, carrying any unsaved edits into the copy's
        theme.yaml. The original directory is not touched."""
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("theme name is empty")
        if new_name in (".", "..") or re.search(r'[<>:"/\\|?*\x00-\x1f]', new_name):
            raise ValueError("theme name contains characters that cannot be "
                             "used in a directory name")
        target = self.theme_dir.parent / new_name
        if target.exists():
            raise ValueError("a theme named '%s' already exists" % new_name)
        shutil.copytree(self.theme_dir, target)
        try:
            self.save_copy_to(target / "theme.yaml")
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return target


# ------------------------------------------------------------------ helpers

def _atomic_write(path: Path, payload: bytes) -> None:
    """Write via a temp file in the same directory, then os.replace()."""
    fd, temp_path = tempfile.mkstemp(
        dir=path.parent, prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _scalar_equal(a, b) -> bool:
    if is_bool(a) != is_bool(b):
        return False
    if is_bool(a):
        return bool(a) == bool(b)
    return a == b


def _walk_diff(pristine, live, path: ElementPath, changes) -> bool:
    """Record changed scalar leaves; return False on any structural change
    (key set, key order, or container-shape difference)."""
    if list(pristine.keys()) != list(live.keys()):
        return False
    for key in pristine.keys():
        p_value, l_value = pristine[key], live[key]
        p_is_map, l_is_map = isinstance(p_value, dict), isinstance(l_value, dict)
        if p_is_map != l_is_map:
            return False
        if p_is_map:
            if not _walk_diff(p_value, l_value, path + (str(key),), changes):
                return False
            continue
        p_is_seq = isinstance(p_value, (list, tuple))
        l_is_seq = isinstance(l_value, (list, tuple))
        if p_is_seq != l_is_seq:
            return False
        if p_is_seq:
            # Whole-sequence compare; a changed list is one changed leaf.
            if list(p_value) != list(l_value):
                changes.append((path + (str(key),), p_value, l_value))
            continue
        if not _scalar_equal(p_value, l_value):
            changes.append((path + (str(key),), p_value, l_value))
    return True


def _resolve(document, path: ElementPath):
    node = document
    for key in path:
        node = node[key]
        if not isinstance(node, dict):
            raise KeyError("path %r does not lead through mappings" % (path,))
    return node


def _value_span(rest: str) -> Optional[Tuple[str, str]]:
    """Split 'value[  # comment][trailing spaces]' into (value, suffix).

    rest is the line content from the value's start column. The suffix (an
    inline comment and/or trailing whitespace) is preserved verbatim so lines
    like 'ANCHOR: lt   # see PIL docs' keep their comment after an edit.
    """
    if rest.startswith('"'):
        index = 1
        while index < len(rest):
            if rest[index] == "\\":
                index += 2
                continue
            if rest[index] == '"':
                return rest[:index + 1], rest[index + 1:]
            index += 1
        return None
    if rest.startswith("'"):
        index = 1
        while index < len(rest):
            if rest[index] == "'":
                if index + 1 < len(rest) and rest[index + 1] == "'":
                    index += 2  # '' escapes a quote inside single quotes
                    continue
                return rest[:index + 1], rest[index + 1:]
            index += 1
        return None
    comment_index = rest.find(" #")
    body = rest if comment_index < 0 else rest[:comment_index]
    value_length = len(body.rstrip())
    return rest[:value_length], rest[value_length:]
