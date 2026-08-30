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

"""Pure-Python model layer for the theme designer: theme discovery, element
enumeration and overlap detection. Deliberately Qt-free so it can be unit
tested without a QApplication.

OVERLAP DETECTION -- why it matters

The physical panel does not composite. Every text/graph element repaints an
opaque rectangle (its own crop of the background image) over its own bounding
box, and each element refreshes independently on its own INTERVAL timer. Two
elements whose boxes intersect therefore erase each other on the real device,
forever -- while looking perfectly fine in a single-pass editor preview. The
renderer gives us every element's true bounding box, so intersections can be
detected exactly rather than guessed from YAML coordinates.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ElementId = Tuple[str, ...]
BBox = Tuple[int, int, int, int]

# The leaf keys under STATS that the render pipeline actually draws.
WIDGET_KEYS = ("TEXT", "GRAPH", "LINE_GRAPH", "RADIAL")

WIDGET_KIND_LABELS = {
    "TEXT": "Text value",
    "GRAPH": "Bar graph",
    "LINE_GRAPH": "Line graph",
    "RADIAL": "Radial gauge",
}

# An element whose box covers at least this fraction of the panel is treated
# as a backdrop (full-screen background image): everything overlaps it by
# design, so pairing it with every other element would drown real problems.
BACKDROP_AREA_FRACTION = 0.90


@dataclass
class ElementInfo:
    """One drawable element of a theme document."""
    element_id: ElementId
    kind: str            # human-readable: "Image", "Text", "Text value", ...
    group: str           # tree group label, e.g. "Static Text" or "CPU"
    label: str           # short name within the group
    node: dict           # LIVE reference into the theme document (not a copy)
    shown: bool          # False when SHOW: False -- element exists but is not drawn
    interval: float      # refresh period in seconds; 0 = drawn once at startup


@dataclass
class Overlap:
    """Two elements whose rendered bounding boxes intersect."""
    a: ElementId
    b: ElementId
    rect: BBox           # the intersection rectangle
    severity: str        # "conflict" | "warning" | "notice"
    message: str         # one-line summary for the problem bar
    detail: str          # full explanation for the tooltip


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def themes_dir() -> Path:
    return repo_root() / "res" / "themes"


def list_themes() -> List[str]:
    """Every directory under res/themes that contains a theme.yaml."""
    root = themes_dir()
    if not root.is_dir():
        return []
    return sorted(
        (p.name for p in root.iterdir() if (p / "theme.yaml").is_file()),
        key=str.casefold,
    )


def id_text(element_id: ElementId) -> str:
    return ".".join(element_id)


def _walk_stats(sensor: str, node: dict, path: ElementId, interval: float,
                out: List[ElementInfo]) -> None:
    """Depth-first walk of one STATS sensor, collecting drawable leaves.

    INTERVAL can sit at any ancestor level (CPU.PERCENTAGE.INTERVAL vs
    GPU.INTERVAL), so the nearest ancestor's value is carried down.
    """
    own = node.get("INTERVAL")
    if isinstance(own, (int, float)) and not isinstance(own, bool):
        interval = float(own)
    for key, value in node.items():
        if not isinstance(value, dict):
            continue
        child_path = path + (str(key),)
        if key in WIDGET_KEYS:
            out.append(ElementInfo(
                element_id=child_path,
                kind=WIDGET_KIND_LABELS.get(key, key),
                group=sensor,
                label=".".join(child_path[2:]),
                node=value,
                shown=bool(value.get("SHOW", False)),
                interval=interval,
            ))
        else:
            _walk_stats(sensor, value, child_path, interval, out)


def enumerate_elements(document: dict) -> List[Tuple[str, List[ElementInfo]]]:
    """Group every drawable element for the tree: Static Images, Static Text,
    then one group per STATS sensor, all in document order."""
    groups: List[Tuple[str, List[ElementInfo]]] = []

    images = document.get("static_images") or {}
    if isinstance(images, dict) and images:
        groups.append(("Static Images", [
            ElementInfo(("static_images", str(name)), "Image", "Static Images",
                        str(name), entry, True, 0.0)
            for name, entry in images.items() if isinstance(entry, dict)
        ]))

    texts = document.get("static_text") or {}
    if isinstance(texts, dict) and texts:
        groups.append(("Static Text", [
            ElementInfo(("static_text", str(name)), "Text", "Static Text",
                        str(name), entry, True, 0.0)
            for name, entry in texts.items() if isinstance(entry, dict)
        ]))

    stats = document.get("STATS") or {}
    if isinstance(stats, dict):
        for sensor, node in stats.items():
            if not isinstance(node, dict):
                continue
            found: List[ElementInfo] = []
            _walk_stats(str(sensor), node, ("STATS", str(sensor)), 0.0, found)
            if found:
                groups.append((str(sensor), found))
    return groups


def flatten(groups: List[Tuple[str, List[ElementInfo]]]) -> Dict[ElementId, ElementInfo]:
    return {info.element_id: info for _, elements in groups for info in elements}


def _intersection(a: BBox, b: BBox) -> Optional[BBox]:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x0 < x1 and y0 < y1:
        return (x0, y0, x1, y1)
    return None


def _fmt_interval(seconds: float) -> str:
    return ("%g" % seconds) + "s"


def compute_overlaps(bboxes: Dict[ElementId, BBox],
                     info_by_id: Dict[ElementId, ElementInfo],
                     panel_width: int, panel_height: int) -> List[Overlap]:
    """Pairwise-intersect the rendered bounding boxes and classify each hit.

    Severity model (matches what the hardware actually does):
      conflict -- both elements repaint on their own timers: each repaint wipes
                  the other's pixels, so the region flickers/corrupts forever.
      warning  -- a refreshing element intersects a draw-once element: after the
                  first refresh the static content is permanently erased there.
      notice   -- two draw-once elements: painted once in file order, the later
                  one covers the earlier. Cosmetic, but usually unintended.
    """
    panel_area = max(1, panel_width * panel_height)
    candidates = []
    for element_id, box in bboxes.items():
        area = max(0, box[2] - box[0]) * max(0, box[3] - box[1])
        if area / panel_area >= BACKDROP_AREA_FRACTION:
            continue  # full-screen backdrop: overlapped by design
        candidates.append((element_id, box))

    overlaps: List[Overlap] = []
    for i in range(len(candidates)):
        id_a, box_a = candidates[i]
        for j in range(i + 1, len(candidates)):
            id_b, box_b = candidates[j]
            rect = _intersection(box_a, box_b)
            if rect is None:
                continue
            info_a = info_by_id.get(id_a)
            info_b = info_by_id.get(id_b)
            int_a = info_a.interval if info_a else 0.0
            int_b = info_b.interval if info_b else 0.0
            name_a, name_b = id_text(id_a), id_text(id_b)
            w, h = rect[2] - rect[0], rect[3] - rect[1]
            where = "%dx%d px at (%d, %d)" % (w, h, rect[0], rect[1])

            if int_a > 0 and int_b > 0:
                severity = "conflict"
                message = "%s and %s repaint over each other (%s)" % (name_a, name_b, where)
                detail = (
                    "Both elements refresh independently (%s every %s, %s every %s). "
                    "The panel does not composite: each refresh repaints an opaque "
                    "rectangle over the element's whole bounding box, erasing the "
                    "other element's pixels inside %s. On the real device this "
                    "region will flicker and corrupt forever. Move one of them."
                    % (name_a, _fmt_interval(int_a), name_b, _fmt_interval(int_b), where))
            elif int_a > 0 or int_b > 0:
                live, static = (name_a, name_b) if int_a > 0 else (name_b, name_a)
                period = _fmt_interval(int_a if int_a > 0 else int_b)
                severity = "warning"
                message = "%s will erase part of %s (%s)" % (live, static, where)
                detail = (
                    "%s refreshes every %s and repaints an opaque rectangle over its "
                    "bounding box. %s is only drawn once at startup, so after the "
                    "first refresh its pixels inside %s are permanently erased on "
                    "the real device -- even though this preview looks fine."
                    % (live, period, static, where))
            else:
                severity = "notice"
                message = "%s and %s overlap (%s)" % (name_a, name_b, where)
                detail = (
                    "Both elements are drawn once at startup, in file order, so the "
                    "later one covers the earlier where they overlap (%s). Stable, "
                    "but usually unintended." % where)
            overlaps.append(Overlap(id_a, id_b, rect, severity, message, detail))

    rank = {"conflict": 0, "warning": 1, "notice": 2}
    overlaps.sort(key=lambda o: (rank[o.severity],
                                 -((o.rect[2] - o.rect[0]) * (o.rect[3] - o.rect[1]))))
    return overlaps
