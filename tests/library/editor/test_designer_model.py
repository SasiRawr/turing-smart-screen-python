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

"""Unit tests for the designer's Qt-free model layer: element enumeration
(including INTERVAL inheritance) and overlap detection with its severity
classification. These run without a QApplication and without rendering."""

import unittest

from library.editor.designer import model


def _doc():
    return {
        "static_images": {
            "BACKGROUND": {"PATH": "background.png", "X": 0, "Y": 0,
                           "WIDTH": 480, "HEIGHT": 320},
        },
        "static_text": {
            "CPU_LABEL": {"TEXT": "CPU", "X": 20, "Y": 58},
        },
        "STATS": {
            "CPU": {
                "PERCENTAGE": {
                    "INTERVAL": 1,
                    "TEXT": {"SHOW": True, "X": 20, "Y": 80},
                    "GRAPH": {"SHOW": False, "X": 20, "Y": 124},
                },
            },
            "GPU": {
                "INTERVAL": 2,  # inherited by everything below
                "TEMPERATURE": {
                    "TEXT": {"SHOW": True, "X": 258, "Y": 146},
                },
            },
        },
    }


class TestEnumerateElements(unittest.TestCase):

    def test_groups_in_document_order(self):
        groups = model.enumerate_elements(_doc())
        self.assertEqual([g for g, _ in groups],
                         ["Static Images", "Static Text", "CPU", "GPU"])

    def test_element_ids_match_renderer_bbox_keys(self):
        info = model.flatten(model.enumerate_elements(_doc()))
        self.assertIn(("static_images", "BACKGROUND"), info)
        self.assertIn(("static_text", "CPU_LABEL"), info)
        self.assertIn(("STATS", "CPU", "PERCENTAGE", "TEXT"), info)
        self.assertIn(("STATS", "CPU", "PERCENTAGE", "GRAPH"), info)
        self.assertIn(("STATS", "GPU", "TEMPERATURE", "TEXT"), info)

    def test_interval_is_inherited_from_nearest_ancestor(self):
        info = model.flatten(model.enumerate_elements(_doc()))
        self.assertEqual(info[("STATS", "CPU", "PERCENTAGE", "TEXT")].interval, 1.0)
        self.assertEqual(info[("STATS", "GPU", "TEMPERATURE", "TEXT")].interval, 2.0)
        self.assertEqual(info[("static_text", "CPU_LABEL")].interval, 0.0)

    def test_show_false_is_reported_not_dropped(self):
        info = model.flatten(model.enumerate_elements(_doc()))
        self.assertFalse(info[("STATS", "CPU", "PERCENTAGE", "GRAPH")].shown)
        self.assertTrue(info[("STATS", "CPU", "PERCENTAGE", "TEXT")].shown)

    def test_nodes_are_live_references_not_copies(self):
        doc = _doc()
        info = model.flatten(model.enumerate_elements(doc))
        info[("static_text", "CPU_LABEL")].node["X"] = 99
        self.assertEqual(doc["static_text"]["CPU_LABEL"]["X"], 99)


class TestComputeOverlaps(unittest.TestCase):

    PANEL = (480, 320)

    def _info(self, mapping):
        """mapping: id -> interval"""
        return {
            element_id: model.ElementInfo(element_id, "Text", "g", "l", {},
                                          True, interval)
            for element_id, interval in mapping.items()
        }

    def test_disjoint_boxes_produce_nothing(self):
        bboxes = {("a",): (0, 0, 10, 10), ("b",): (20, 20, 30, 30)}
        info = self._info({("a",): 1.0, ("b",): 1.0})
        self.assertEqual(model.compute_overlaps(bboxes, info, *self.PANEL), [])

    def test_two_refreshing_elements_are_a_conflict(self):
        bboxes = {("a",): (0, 0, 20, 20), ("b",): (10, 10, 30, 30)}
        info = self._info({("a",): 1.0, ("b",): 2.0})
        overlaps = model.compute_overlaps(bboxes, info, *self.PANEL)
        self.assertEqual(len(overlaps), 1)
        self.assertEqual(overlaps[0].severity, "conflict")
        self.assertEqual(overlaps[0].rect, (10, 10, 20, 20))

    def test_refreshing_over_static_is_a_warning(self):
        bboxes = {("stat",): (0, 0, 20, 20), ("label",): (10, 10, 30, 30)}
        info = self._info({("stat",): 1.0, ("label",): 0.0})
        overlaps = model.compute_overlaps(bboxes, info, *self.PANEL)
        self.assertEqual(overlaps[0].severity, "warning")
        self.assertIn("stat", overlaps[0].message.split()[0])

    def test_two_static_elements_are_a_notice(self):
        bboxes = {("a",): (0, 0, 20, 20), ("b",): (10, 10, 30, 30)}
        info = self._info({("a",): 0.0, ("b",): 0.0})
        overlaps = model.compute_overlaps(bboxes, info, *self.PANEL)
        self.assertEqual(overlaps[0].severity, "notice")

    def test_fullscreen_backdrop_is_excluded(self):
        bboxes = {
            ("static_images", "BACKGROUND"): (0, 0, 480, 320),
            ("a",): (0, 0, 20, 20),
        }
        info = self._info({("static_images", "BACKGROUND"): 0.0, ("a",): 1.0})
        self.assertEqual(model.compute_overlaps(bboxes, info, *self.PANEL), [])

    def test_touching_edges_do_not_count_as_overlap(self):
        bboxes = {("a",): (0, 0, 10, 10), ("b",): (10, 0, 20, 10)}
        info = self._info({("a",): 1.0, ("b",): 1.0})
        self.assertEqual(model.compute_overlaps(bboxes, info, *self.PANEL), [])

    def test_conflicts_sort_before_warnings(self):
        bboxes = {
            ("s1",): (0, 0, 10, 10), ("s2",): (5, 5, 15, 15),   # conflict
            ("s3",): (100, 100, 110, 110),                       # warning vs t1
            ("t1",): (105, 105, 115, 115),
        }
        info = self._info({("s1",): 1.0, ("s2",): 1.0, ("s3",): 1.0, ("t1",): 0.0})
        overlaps = model.compute_overlaps(bboxes, info, *self.PANEL)
        self.assertEqual([o.severity for o in overlaps], ["conflict", "warning"])


if __name__ == "__main__":
    unittest.main()
