#!/usr/bin/env python
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

"""Theme Designer -- visual editor for turing-smart-screen themes.

Usage:  .venv/Scripts/python.exe theme-designer.py [theme-name]

Renders through the monitor's own pipeline (library/editor/renderer.py) so the
preview is pixel-identical to the panel, and flags overlapping elements that
would erase each other on the real device.
"""

import os
import sys
from pathlib import Path

# Run from anywhere: the monitor's modules resolve res/ and config.yaml
# relative to the repo root.
REPO_ROOT = Path(__file__).resolve().parent
os.chdir(REPO_ROOT)
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from library.editor.designer import model, style
    from library.editor.designer.window import DesignerWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Theme Designer")
    style.apply(app)

    themes = model.list_themes()
    if not themes:
        print("No themes found under res/themes/ -- nothing to edit.", file=sys.stderr)
        return 1

    requested = sys.argv[1] if len(sys.argv) > 1 else None
    if requested is not None and requested not in themes:
        print("Theme '%s' not found under res/themes/." % requested, file=sys.stderr)
        return 1
    initial = requested or ("LoopTelemetry" if "LoopTelemetry" in themes else themes[0])

    window = DesignerWindow(initial_theme=initial)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
