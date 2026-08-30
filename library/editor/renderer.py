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

"""Render a theme to a PIL image, fast, with every element's bounding box.

WHY THIS EXISTS

The designer's preview has to be pixel-identical to the panel, so it renders
through the monitor's own pipeline rather than reimplementing it. Two things
have to be added on top of that pipeline:

  1. Speed. LcdSimulated writes a full-screen PNG to disk on *every* element
     paste (lcd_simulated.py, DisplayPILImage and SetOrientation). Measured on
     a 16-element theme that is 81.2 ms per frame, ~90% of it disk I/O. With
     the writes removed the same frame takes 6.9 ms -- an 11.8x speedup, and
     the difference between "re-render on mouse release" and "re-render
     continuously while dragging".

  2. Attribution. A rendered frame is just pixels; the editor needs to know
     which pixels belong to which YAML key in order to hit-test a click,
     outline a selection, or warn about overlap. EditorLcd records the box of
     every paste, and the wrappers installed by _install_attribution() tag each
     paste with the theme path that caused it.

WHAT THIS DELIBERATELY DOES NOT DO

It never calls config.load_theme(). That function reacts to a malformed theme
with sys.exit(0) inside a try whose bare except: then calls os._exit(0) --
uncatchable, and fatal to a GUI process. The editor parses the YAML itself and
assigns config.THEME_DATA directly, which also skips a ~10 ms reparse per frame.

It never renders from the caller's own dict. library/stats.py writes
theme_data['SHOW'] = False in 32 places when a sensor reads NaN, so rendering
straight from the document a user is editing would silently strip SHOW: True
out of their theme on the next save. render() always works on a deep copy.
"""

import contextvars
import copy
import threading
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

# Element identity. A tuple of YAML keys, e.g.
#   ('static_text', 'DELTA_LABEL')
#   ('STATS', 'CPU', 'PERCENTAGE', 'TEXT')
# Stable across reloads because the schema fixes the STATS paths, so it doubles
# as the selection key, the hit-test key and the bbox key.
ElementId = Tuple[str, ...]

BBox = Tuple[int, int, int, int]  # x0, y0, x1, y1


class RenderError(NamedTuple):
    element: Optional[ElementId]
    message: str


class RenderResult(NamedTuple):
    image: Any  # PIL.Image.Image
    bboxes: Dict[ElementId, BBox]
    errors: List[RenderError]


# Everything below is populated by _bootstrap() on first render, so that merely
# importing this module has no side effects.
_lock = threading.Lock()
_ready = False
_config = None
_display = None
_stats = None
_EditorLcd = None

# The path whose draw call is currently in flight, and the map from the identity
# of a theme sub-dict to its path. id() is safe here because the deep copy the
# renderer walks is held alive for the whole render.
_current_element: contextvars.ContextVar = contextvars.ContextVar("current_element", default=None)
_path_by_id: Dict[int, ElementId] = {}
_errors: List[RenderError] = []


# A complete, explicit config for the editor. The render path reads BRIGHTNESS,
# DISPLAY_REVERSE, ETH/WLO, PING, CPU_FAN and HW_SENSORS from the user's real
# config.yaml; left alone, the preview would change depending on what the user
# happened to configure for their own panel. Pinning them keeps the preview
# deterministic on any machine.
EDITOR_CONFIG = {
    "HW_SENSORS": "STATIC",
    "ETH": "",
    "WLO": "",
    "CPU_FAN": "AUTO",
    "PING": "127.0.0.1",
    "WEATHER_API_KEY": "",
    "WEATHER_LATITUDE": 45.75,
    "WEATHER_LONGITUDE": 4.85,
    "WEATHER_UNITS": "metric",
    "WEATHER_LANGUAGE": "en",
}
EDITOR_DISPLAY = {
    "REVISION": "SIMU",
    "BRIGHTNESS": 100,
    "DISPLAY_REVERSE": False,
    "RESET_ON_STARTUP": False,
}


class ThemePreflightError(RuntimeError):
    """The theme named in config.yaml cannot be parsed, so importing the
    monitor's config module would terminate the process."""


def preflight():
    """Validate config.yaml's theme BEFORE library.config is imported.

    config.py runs load_theme() at import, and load_theme() answers a broken
    theme with sys.exit(0) inside a try whose bare except: calls os._exit(0).
    That kills the process outright -- no traceback, no chance for a GUI to show
    an error. Parsing the same files here first turns a silent death into a
    catchable exception naming the file at fault.
    """
    import yaml
    from pathlib import Path
    root = Path(__file__).parent.parent.parent.resolve()
    try:
        with open(root / "config.yaml", "rt", encoding="utf8") as fh:
            cfg = yaml.safe_load(fh)
        theme_name = cfg["config"]["THEME"]
    except Exception as exc:
        raise ThemePreflightError("config.yaml could not be read: %s" % exc) from exc

    theme_file = root / "res" / "themes" / str(theme_name) / "theme.yaml"
    if not theme_file.is_file():
        raise ThemePreflightError(
            "config.yaml selects theme '%s' but %s does not exist. Importing the "
            "monitor's config module would terminate this process; fix THEME in "
            "config.yaml first." % (theme_name, theme_file))
    try:
        with open(theme_file, "rt", encoding="utf8") as fh:
            if not isinstance(yaml.safe_load(fh), dict):
                raise ValueError("theme.yaml did not parse to a mapping")
    except ThemePreflightError:
        raise
    except Exception as exc:
        raise ThemePreflightError(
            "theme '%s' (%s) contains errors: %s. Importing the monitor's config "
            "module would terminate this process; fix the theme or change THEME "
            "in config.yaml first." % (theme_name, theme_file, exc)) from exc


def _bootstrap():
    """Import the render pipeline once, with the editor's config forced first."""
    global _ready, _config, _display, _stats, _EditorLcd
    if _ready:
        return

    # library/config.py calls load_theme() at import (config.py:84), so the
    # theme named in the user's config.yaml is parsed the moment we import it --
    # and a malformed one would os._exit(0) the process before we get control.
    # preflight() turns that into a catchable error with a useful message.
    preflight()

    # The monitor logs a line per bitmap load and per render; at editor frame
    # rates that floods the console and costs measurable time.
    import logging
    import library.log
    library.log.logger.setLevel(logging.ERROR)

    from library import config as config_module
    _config = config_module

    # Must happen before library.display is imported: that module builds a
    # Display() at import time and picks its backend and size from CONFIG_DATA
    # and THEME_DATA.
    _config.CONFIG_DATA["config"].update(EDITOR_CONFIG)
    _config.CONFIG_DATA["display"].update(EDITOR_DISPLAY)

    # config.py's import-time load_theme() has already put a usable theme in
    # THEME_DATA; render() replaces it per frame. Only guarantee the keys
    # Display() reads at construction, since THEME_DEFAULT alone has no
    # 'display' section and _get_theme_size() would KeyError on it.
    if not isinstance(getattr(_config, "THEME_DATA", None), dict):
        _config.THEME_DATA = copy.deepcopy(_config.THEME_DEFAULT)
    _config.THEME_DATA.setdefault("display", {})
    _config.THEME_DATA["display"].setdefault("DISPLAY_ORIENTATION", "portrait")
    _config.THEME_DATA["display"].setdefault("DISPLAY_SIZE", '3.5"')
    _config.THEME_DATA.setdefault("PATH", str(_config.MAIN_DIRECTORY / "res/themes/") + "/")

    from library.display import display as display_module
    _display = display_module
    import library.stats as stats_module
    _stats = stats_module

    from library.lcd.lcd_comm import Orientation
    from library.lcd.lcd_simulated import LcdSimulated
    from PIL import Image

    class EditorLcd(LcdSimulated):
        """LcdSimulated with the disk writes removed and bboxes recorded.

        Deliberately does not call LcdSimulated.__init__: that writes two files
        and starts an HTTPServer on port 5678 in a NON-daemon thread, which
        would keep a GUI process alive after its window closed.
        """

        def __init__(self, width: int, height: int):
            from library.lcd.lcd_comm import LcdComm
            LcdComm.__init__(self, "AUTO", width, height, None)
            self.screen_image = Image.new("RGB", (self.get_width(), self.get_height()), (255, 255, 255))
            self.orientation = Orientation.PORTRAIT
            self.webServer = None
            self.paste_boxes: Dict[ElementId, BBox] = {}

        def __del__(self):
            pass  # nothing to tear down; the base class would close a webserver

        def closeSerial(self):
            pass

        def SetOrientation(self, orientation: Orientation = Orientation.PORTRAIT):
            self.orientation = orientation
            with self.update_queue_mutex:
                self.screen_image = Image.new(
                    "RGB", (self.get_width(), self.get_height()), (255, 255, 255))

        def DisplayPILImage(self, image, x: int = 0, y: int = 0,
                            image_width: int = 0, image_height: int = 0):
            # Same geometry rules as the base class, minus the PNG write.
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

            assert x <= self.get_width(), 'Image X coordinate must be <= display width'
            assert y <= self.get_height(), 'Image Y coordinate must be <= display height'
            assert image_height > 0, 'Image height must be > 0'
            assert image_width > 0, 'Image width must be > 0'

            with self.update_queue_mutex:
                self.screen_image.paste(image, (x, y))

            element = _current_element.get()
            if element is not None:
                box = (x, y, x + image_width, y + image_height)
                prev = self.paste_boxes.get(element)
                # An element can paint more than once (a radial draws its arc and
                # then its centre text); union so the box covers the whole thing.
                self.paste_boxes[element] = box if prev is None else (
                    min(prev[0], box[0]), min(prev[1], box[1]),
                    max(prev[2], box[2]), max(prev[3], box[3]))

    _EditorLcd = EditorLcd

    # Shut down the webserver and buffer that importing library.display created,
    # and swap in a headless LCD. Every call site reaches the LCD through
    # display.lcd at runtime, so this one rebind redirects the whole pipeline.
    try:
        _display.lcd.closeSerial()
    except Exception:
        pass
    _display.lcd = EditorLcd(320, 480)

    _install_attribution()
    _ready = True


def _install_attribution():
    """Tag each element draw with the theme path that caused it."""
    def wrap(fn):
        def wrapper(theme_data, *args, **kwargs):
            token = _current_element.set(_path_by_id.get(id(theme_data)))
            try:
                return fn(theme_data, *args, **kwargs)
            except Exception as exc:
                # One bad element must not abort the frame. lcd_comm asserts on
                # empty text and on x > display width, both of which a user can
                # produce mid-edit, and an uncaught assert would leave a
                # half-drawn preview with no indication of which element broke.
                _errors.append(RenderError(_current_element.get(), "%s: %s" % (type(exc).__name__, exc)))
                return None
            finally:
                _current_element.reset(token)
        wrapper.__name__ = getattr(fn, "__name__", "wrapped")
        wrapper.__wrapped__ = fn
        return wrapper

    # The percent/temperature variants delegate to these four through module
    # globals, so wrapping the four covers every themed element.
    for name in ("display_themed_value", "display_themed_progress_bar",
                 "display_themed_radial_bar", "display_themed_line_graph"):
        fn = getattr(_stats, name)
        if not hasattr(fn, "__wrapped__"):
            setattr(_stats, name, wrap(fn))


def _index_paths(node, prefix: ElementId = ()):  # -> None
    """Map id(sub-dict) -> its YAML path, for every dict in the theme."""
    if not isinstance(node, dict):
        return
    _path_by_id[id(node)] = prefix
    for key, value in node.items():
        if isinstance(value, dict):
            _index_paths(value, prefix + (str(key),))


# Bitmaps and fonts are keyed by path and cost real time to reload -- clearing
# them every frame makes a render ~3x slower because the background PNG is
# re-decoded each time. They are kept across frames and dropped only when the
# editor says an asset changed on disk.
_asset_cache: Dict[str, Any] = {}
_font_cache: Dict[str, Any] = {}


def invalidate_asset_cache():
    """Drop cached bitmaps and fonts.

    Call after importing or replacing an image: the pipeline's caches are keyed
    by path and never expire, so a new file reusing an old filename would
    otherwise keep rendering the old pixels.
    """
    _asset_cache.clear()
    _font_cache.clear()


def _reset_render_state():
    """Clear the pipeline state that would otherwise leak between frames."""
    lcd = _display.lcd
    # Unions each text's box with its previous draw at the same coordinates.
    # Correct on the device (it prevents ghosting); in an editor it just
    # accumulates stale geometry.
    lcd.text_bbox_cache = {}
    # Carried across frames deliberately -- see invalidate_asset_cache().
    lcd.image_cache = _asset_cache
    lcd.font_cache = _font_cache
    lcd.paste_boxes = {}
    _errors.clear()
    # Line-graph history is class-level and gains a point per render. At editor
    # frame rates it fills instantly and looks nothing like the panel.
    for attr in dir(_stats.CPU) + dir(_stats.Gpu) + dir(_stats.Net):
        if attr.startswith("last_values"):
            for cls in (_stats.CPU, _stats.Gpu, _stats.Net):
                if hasattr(cls, attr) and isinstance(getattr(cls, attr), list):
                    getattr(cls, attr).clear()


def load_theme_document(theme_dir) -> dict:
    """Parse a theme.yaml the way the monitor does, without load_theme()'s exit."""
    from pathlib import Path
    if not _ready:
        # Callable before the first render(): the parser lives on the config
        # module, which only exists once the pipeline is bootstrapped.
        with _lock:
            _bootstrap()
    theme_dir = Path(theme_dir)
    data = _config.load_yaml(theme_dir / "theme.yaml")
    if not isinstance(data, dict):
        raise ValueError("theme.yaml did not parse to a mapping")
    data["PATH"] = str(theme_dir) + "/"
    return data


def render(theme_dir, document: Optional[dict] = None) -> RenderResult:
    """Render a theme once and return the image, per-element boxes and errors.

    theme_dir : path to res/themes/<name>
    document  : an already-parsed theme dict; re-read from disk when omitted.
                It is never mutated -- the renderer works on a deep copy.
    """
    with _lock:
        _bootstrap()

        if document is None:
            document = load_theme_document(theme_dir)

        # Deep copy first: stats.py sets SHOW False on the dict it renders.
        render_data = copy.deepcopy(document)
        _config.copy_default(_config.THEME_DEFAULT, render_data)
        render_data["PATH"] = str(theme_dir) + "/"

        _path_by_id.clear()
        _index_paths(render_data)
        _config.THEME_DATA = render_data

        # Size comes from the theme's DISPLAY_SIZE, in portrait-native terms;
        # initialize_display() then applies the theme's orientation. Build a
        # fresh LCD per render rather than a second Display(), which would bind
        # port 5678 again and leak another non-daemon thread.
        from library.display import _get_theme_size
        width, height = _get_theme_size()
        _display.lcd = _EditorLcd(width, height)
        _reset_render_state()

        _display.initialize_display()
        _draw_static_images(render_data)
        _draw_static_text(render_data)
        _draw_stats(render_data)

        return RenderResult(
            image=_display.lcd.screen_image.copy(),
            bboxes=dict(_display.lcd.paste_boxes),
            errors=list(_errors),
        )


def _draw_static_images(theme: dict):
    # Reimplemented rather than calling display.display_static_images() because
    # that loops internally, so a bbox cannot be attributed to each entry from
    # outside. Kept a line-for-line translation of display.py so the output
    # stays identical; test_renderer_parity guards that.
    for name in theme.get("static_images", {}) or {}:
        entry = theme["static_images"][name]
        token = _current_element.set(("static_images", str(name)))
        try:
            _display.lcd.DisplayBitmap(
                bitmap_path=theme["PATH"] + entry.get("PATH"),
                x=entry.get("X", 0),
                y=entry.get("Y", 0),
                width=entry.get("WIDTH", 0),
                height=entry.get("HEIGHT", 0),
            )
        except Exception as exc:
            _errors.append(RenderError(("static_images", str(name)),
                                       "%s: %s" % (type(exc).__name__, exc)))
        finally:
            _current_element.reset(token)


def _draw_static_text(theme: dict):
    from library.display import _get_full_path
    for name in theme.get("static_text", {}) or {}:
        entry = theme["static_text"][name]
        token = _current_element.set(("static_text", str(name)))
        try:
            _display.lcd.DisplayText(
                text=entry.get("TEXT"),
                x=entry.get("X", 0),
                y=entry.get("Y", 0),
                width=entry.get("WIDTH", 0),
                height=entry.get("HEIGHT", 0),
                font=_config.FONTS_DIR + entry.get("FONT", "roboto-mono/RobotoMono-Regular.ttf"),
                font_size=entry.get("FONT_SIZE", 10),
                font_color=entry.get("FONT_COLOR", (0, 0, 0)),
                background_color=entry.get("BACKGROUND_COLOR", (255, 255, 255)),
                background_image=_get_full_path(theme["PATH"], entry.get("BACKGROUND_IMAGE", None)),
                align=entry.get("ALIGN", "left"),
                anchor=entry.get("ANCHOR", "lt"),
            )
        except Exception as exc:
            _errors.append(RenderError(("static_text", str(name)),
                                       "%s: %s" % (type(exc).__name__, exc)))
        finally:
            _current_element.reset(token)


# Mirrors theme-editor.py's refresh_theme(): each stat is drawn only when its
# INTERVAL is set, and a failure in one must not stop the others.
_STAT_CALLS = (
    ("CPU", "PERCENTAGE", lambda s: s.CPU.percentage()),
    ("CPU", "FREQUENCY", lambda s: s.CPU.frequency()),
    ("CPU", "LOAD", lambda s: s.CPU.load()),
    ("CPU", "TEMPERATURE", lambda s: s.CPU.temperature()),
    ("CPU", "FAN_SPEED", lambda s: s.CPU.fan_speed()),
    ("GPU", None, lambda s: s.Gpu.stats()),
    ("MEMORY", None, lambda s: s.Memory.stats()),
    ("DISK", None, lambda s: s.Disk.stats()),
    ("NET", None, lambda s: s.Net.stats()),
    ("DATE", None, lambda s: s.Date.stats()),
    ("UPTIME", None, lambda s: s.SystemUptime.stats()),
    ("CUSTOM", None, lambda s: s.Custom.stats()),
    ("WEATHER", None, lambda s: s.Weather.stats()),
    ("PING", None, lambda s: s.Ping.stats()),
)


def _draw_stats(theme: dict):
    stats_section = theme.get("STATS", {}) or {}
    for section, sub, call in _STAT_CALLS:
        node = stats_section.get(section)
        if not isinstance(node, dict):
            continue
        if sub is not None:
            node = node.get(sub)
            if not isinstance(node, dict):
                continue
        if node.get("INTERVAL", 0) <= 0:
            continue
        try:
            call(_stats)
        except Exception as exc:
            path = ("STATS", section) + ((sub,) if sub else ())
            _errors.append(RenderError(path, "%s: %s" % (type(exc).__name__, exc)))
