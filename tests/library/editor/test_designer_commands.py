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

"""Command objects and the undo stack -- pure Python, no Qt."""

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

from library.editor.designer.commands import (CompoundCommand, MISSING,  # noqa: E402
                                              MoveElement, SetProperty,
                                              UndoStack)


def make_doc():
    return {
        "static_text": {
            "LABEL": {"TEXT": "CPU", "X": 20, "Y": 58},
            "BARE": {"TEXT": "no position"},
        },
    }


class TestSetProperty(unittest.TestCase):

    def test_apply_and_revert(self):
        doc = make_doc()
        command = SetProperty(("static_text", "LABEL"), "X", 20, 33)
        command.apply(doc)
        self.assertEqual(33, doc["static_text"]["LABEL"]["X"])
        command.revert(doc)
        self.assertEqual(20, doc["static_text"]["LABEL"]["X"])

    def test_revert_of_missing_old_deletes_the_key(self):
        doc = make_doc()
        command = SetProperty(("static_text", "BARE"), "X", MISSING, 10)
        command.apply(doc)
        self.assertEqual(10, doc["static_text"]["BARE"]["X"])
        command.revert(doc)
        self.assertNotIn("X", doc["static_text"]["BARE"])


class TestMoveElement(unittest.TestCase):

    def test_move_and_revert_restores_both_axes(self):
        doc = make_doc()
        command = MoveElement(("static_text", "LABEL"), (20, 58), (100, 200))
        command.apply(doc)
        node = doc["static_text"]["LABEL"]
        self.assertEqual((100, 200), (node["X"], node["Y"]))
        command.revert(doc)
        self.assertEqual((20, 58), (node["X"], node["Y"]))

    def test_revert_removes_keys_that_did_not_exist(self):
        doc = make_doc()
        command = MoveElement(("static_text", "BARE"), (MISSING, MISSING), (5, 6))
        command.apply(doc)
        command.revert(doc)
        self.assertNotIn("X", doc["static_text"]["BARE"])
        self.assertNotIn("Y", doc["static_text"]["BARE"])


class TestCompoundCommand(unittest.TestCase):

    def test_reverts_in_reverse_order(self):
        doc = make_doc()
        compound = CompoundCommand("Edit two things", [
            SetProperty(("static_text", "LABEL"), "X", 20, 1),
            SetProperty(("static_text", "LABEL"), "X", 1, 2),
        ])
        compound.apply(doc)
        self.assertEqual(2, doc["static_text"]["LABEL"]["X"])
        compound.revert(doc)
        self.assertEqual(20, doc["static_text"]["LABEL"]["X"])


class TestUndoStack(unittest.TestCase):

    def setUp(self):
        self.doc = make_doc()
        self.events = 0
        self.stack = UndoStack(self.doc, on_change=self._count)

    def _count(self):
        self.events += 1

    def _x(self):
        return self.doc["static_text"]["LABEL"]["X"]

    def test_push_undo_redo(self):
        self.stack.push(SetProperty(("static_text", "LABEL"), "X", 20, 30))
        self.stack.push(SetProperty(("static_text", "LABEL"), "X", 30, 40))
        self.assertEqual(40, self._x())
        self.assertEqual("Set X = 40", self.stack.undo_text())

        self.stack.undo()
        self.assertEqual(30, self._x())
        self.assertEqual("Set X = 40", self.stack.redo_text())
        self.stack.undo()
        self.assertEqual(20, self._x())
        self.assertFalse(self.stack.can_undo())

        self.stack.redo()
        self.stack.redo()
        self.assertEqual(40, self._x())
        self.assertFalse(self.stack.can_redo())

    def test_push_discards_redo_branch(self):
        self.stack.push(SetProperty(("static_text", "LABEL"), "X", 20, 30))
        self.stack.undo()
        self.stack.push(SetProperty(("static_text", "LABEL"), "X", 20, 99))
        self.assertFalse(self.stack.can_redo())
        self.assertEqual(99, self._x())

    def test_clean_tracking_across_save_and_undo(self):
        self.assertTrue(self.stack.is_clean())
        self.stack.push(SetProperty(("static_text", "LABEL"), "X", 20, 30))
        self.assertFalse(self.stack.is_clean())
        self.stack.set_clean()  # "saved"
        self.assertTrue(self.stack.is_clean())
        self.stack.undo()
        self.assertFalse(self.stack.is_clean())  # differs from saved state
        self.stack.redo()
        self.assertTrue(self.stack.is_clean())

    def test_clean_state_lost_when_redo_branch_discarded(self):
        self.stack.push(SetProperty(("static_text", "LABEL"), "X", 20, 30))
        self.stack.set_clean()
        self.stack.undo()
        self.stack.push(SetProperty(("static_text", "LABEL"), "X", 20, 50))
        self.assertFalse(self.stack.is_clean())
        self.stack.undo()
        self.assertFalse(self.stack.is_clean())  # saved state is unreachable

    def test_already_applied_push_does_not_reapply(self):
        # Simulates a drag: the document was mutated live, then one command
        # records the gesture.
        node = self.doc["static_text"]["LABEL"]
        node["X"], node["Y"] = 111, 222
        self.stack.push(MoveElement(("static_text", "LABEL"),
                                    (20, 58), (111, 222)),
                        already_applied=True)
        self.assertEqual((111, 222), (node["X"], node["Y"]))
        self.stack.undo()
        self.assertEqual((20, 58), (node["X"], node["Y"]))

    def test_nudges_coalesce_into_one_undo_step(self):
        for step in range(1, 6):
            self.stack.push(
                MoveElement(("static_text", "LABEL"),
                            (20 + step - 1, 58), (20 + step, 58)),
                coalesce=True)
        self.assertEqual(25, self._x())
        self.stack.undo()
        self.assertEqual(20, self._x())          # one undo, back to the start
        self.assertFalse(self.stack.can_undo())  # it really was one step

    def test_coalescing_never_merges_across_the_clean_point(self):
        self.stack.push(MoveElement(("static_text", "LABEL"), (20, 58), (21, 58)),
                        coalesce=True)
        self.stack.set_clean()
        self.stack.push(MoveElement(("static_text", "LABEL"), (21, 58), (22, 58)),
                        coalesce=True)
        # Had these merged, is_clean() would wrongly report True at (22, 58).
        self.assertFalse(self.stack.is_clean())
        self.stack.undo()
        self.assertEqual(21, self._x())
        self.assertTrue(self.stack.is_clean())

    def test_on_change_fires(self):
        before = self.events
        self.stack.push(SetProperty(("static_text", "LABEL"), "X", 20, 30))
        self.stack.undo()
        self.stack.redo()
        self.stack.set_clean()
        self.assertEqual(before + 4, self.events)


if __name__ == "__main__":
    unittest.main()
