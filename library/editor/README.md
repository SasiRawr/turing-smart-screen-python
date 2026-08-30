# library/editor

Support code for the visual theme designer (`theme-designer.py`). This package
is **not** imported by the system monitor (`main.py`) — it exists purely to
give the designer app a fast, attributed way to render `theme.yaml` files
through the monitor's own display pipeline.

If you're looking for the monitor itself, or for `theme-editor.py` (the
existing read-only, drag-to-measure previewer), you're in the wrong place —
neither of those import this package, and this package doesn't touch them.

## Contents

| File | What it is |
|---|---|
| `renderer.py` | Headless render pipeline + per-element bounding boxes. Stable — the subject of this document. |
| `designer/*.py` | The Qt (PySide6) application built on top of the renderer: model, window, style, inspector. Under active development — see its own docstrings/code for current internals rather than relying on this README. |

## Why `renderer.py` exists

The designer's whole value proposition is that its preview is pixel-identical
to what the panel shows, so it renders through the monitor's real pipeline
(`library/display.py`, `library/lcd/lcd_simulated.py`) rather than
reimplementing drawing logic. Reusing that pipeline as-is is missing two
things an editor needs, and `renderer.py` adds exactly those two things —
nothing else.

### 1. Speed

`LcdSimulated.DisplayPILImage` and `SetOrientation` write a full-screen PNG to
disk on **every single element paste** (`library/lcd/lcd_simulated.py:64-65,
112-113, 144-145` — `screen_image.save("tmp", "PNG")` followed by
`shutil.copyfile`). That's fine for a monitor updating the panel a few times a
second; it's much too slow for an editor that wants to redraw continuously
while you drag an element.

Measured on a 16-element theme:

| | Time |
|---|---|
| Full render, disk writes included | 81.2 ms/frame |
| Same render, disk writes removed | 6.9 ms/frame (11.8x faster) |
| Complete `renderer.render()` call (parse + deep copy + draw) | ~17 ms |
| Steady-state re-render (cached parse, same document) | ~8 ms |

That's the difference between "re-render on mouse release" and "re-render
continuously while dragging" — the renderer's `EditorLcd` class removes the
disk writes and keeps everything else about the paste geometry identical.

### 2. Attribution

A rendered frame is just pixels. The editor also needs to know which pixels
belong to which YAML key, so it can hit-test a click, outline a selection, or
detect overlap. `EditorLcd` records the bounding box of every paste, and
`_install_attribution()` wraps the four `library/stats.py` draw helpers so
each paste gets tagged with the theme path that produced it.

## The renderer API

```python
from library.editor import renderer

# Optional but recommended: run before importing library.config, so a broken
# theme raises instead of silently killing the process (see "Hazard 1" below).
renderer.preflight()

# Parse a theme.yaml the way the monitor does, without load_theme()'s exit path.
document = renderer.load_theme_document("res/themes/LoopTelemetry")

# Render it. render() bootstraps the pipeline on first call, deep-copies the
# document, draws through the real pipeline, and returns image + boxes + errors.
result = renderer.render("res/themes/LoopTelemetry", document=document)

result.image     # PIL.Image.Image, the rendered frame
result.bboxes     # Dict[ElementId, (x0, y0, x1, y1)] -- one entry per element that drew
result.errors     # List[RenderError] -- one entry per element that failed

# After replacing/importing an image file under an existing filename:
renderer.invalidate_asset_cache()
```

`document` is optional on `render()` — omit it and the theme is re-read from
disk. When you do pass a document (e.g. one the designer is actively editing
in memory), it is never mutated; `render()` always works on a deep copy.

### `ElementId`

```python
ElementId = Tuple[str, ...]
```

The tuple of YAML keys leading to an element, e.g. `('static_text',
'DELTA_LABEL')` or `('STATS', 'CPU', 'PERCENTAGE', 'TEXT')`. It's stable
across reloads because the theme schema fixes the `STATS` paths, so the same
tuple works as the selection key, the hit-test key, and the bbox key —
there's no separate ID scheme to keep in sync with the YAML.

### `preflight()` / `ThemePreflightError`

```python
def preflight() -> None: ...

class ThemePreflightError(RuntimeError): ...
```

Validates the theme named in `config.yaml` *before* `library.config` gets
imported anywhere in the process (directly or transitively). Raises
`ThemePreflightError` naming the file at fault if `config.yaml` can't be read,
the selected theme doesn't exist, or its `theme.yaml` doesn't parse to a
mapping. See "Hazard 1" below for why this matters. `render()` and
`load_theme_document()` both call it internally on first use, so you only need
to call it yourself if you want to fail fast before touching anything else
that might import `library.config`.

## The three pipeline hazards this renderer works around

These aren't hypothetical — each one was hit while building the designer, and
each is why a particular piece of `renderer.py` exists.

**Hazard 1 — a malformed theme kills the process, uncatchably.**
`library/config.py:55-67`, `load_theme()`, answers a broken theme with:

```python
except:
    logger.error("Theme not found or contains errors!")
    try:
        sys.exit(0)
    except:
        os._exit(0)
```

`sys.exit(0)` inside a bare `except:` swallows the exit, so it falls through
to `os._exit(0)` — which terminates the process immediately, with no
traceback and no chance for a GUI to catch it or show an error. `config.py:84`
calls `load_theme()` at *import time*, so merely importing `library.config`
with a bad theme selected in `config.yaml` is fatal. `renderer.preflight()`
parses `config.yaml` and the selected theme's `theme.yaml` independently,
first, and raises a normal, catchable `ThemePreflightError` instead.

**Hazard 2 — `library/stats.py` mutates the theme dict it renders.**
`library/stats.py` assigns `theme_data['SHOW'] = False` in 32 places when a
sensor reads NaN (e.g. no CPU fan present). If the renderer rendered straight
from the document a user is actively editing, a machine missing one sensor
would silently strip `SHOW: True` out of that user's theme the next time the
editor saved it. `render()` always deep-copies the document before handing it
to the pipeline (`renderer.py:408`); `test_does_not_mutate_the_caller_document`
in `tests/library/editor/test_renderer.py` guards this directly.

**Hazard 3 — one bad element can abort the whole frame.**
`library/lcd/lcd_comm.py` asserts `x <= display width` (`:282`) and
`len(text) > 0` (`:286`), among others — both reachable mid-edit (drag an
element off the right edge, or clear its text field). An uncaught
`AssertionError` from one element would leave a half-drawn preview with no
indication of what broke. `_install_attribution()`'s wrapper
(`renderer.py:301-317`) catches any exception from an individual element's
draw call, records it as a `RenderError` tagged with that element's
`ElementId`, and lets the rest of the frame keep drawing.

## The no-compositing model of the panel

This is the most important thing to understand before working on overlap
detection or drag behavior.

The panel has **no layers**. It's a single framebuffer, painted destructively,
in a fixed draw order (`static_images`, then `static_text`, then a fixed
sequence of `STATS` entries). Every element — text, progress bar, radial,
graph — repaints an **opaque** rectangle over its own bounding box, and it
does so independently, on its own `INTERVAL`, for as long as the monitor runs.

The consequence: if two elements' bounding boxes intersect, they don't
peacefully coexist — they erase each other, continuously, forever, each time
either one's `INTERVAL` fires. A single-pass editor preview renders every
element exactly once and looks perfect, because it never gets far enough
along in time to show the second element wiping out part of the first. The
bug is invisible in the preview and only shows up live, on the device,
seconds after the theme is loaded.

This is why overlap detection — built on the same `{ElementId: bbox}` map the
renderer produces for hit-testing — is not a nice-to-have. It caught two real
cases:

- **LoopTelemetry**: the coolant line graphs reached x=348 while the delta-T
  radial occupied x=328–416 — a 20 px overlap that would have made the two
  erase each other every 2 seconds on real hardware. Fixed by narrowing the
  graphs from 172 px to 148 px wide.
- **The stock upstream theme `3.5inchTheme2`** has the same class of bug:
  `STATS.DISK.TOTAL.TEXT` erases part of `static_text.DISK_TOTAL_LABEL`. Left
  as-is (it's upstream's issue), but it's a real, pre-existing bug the
  detector found unprompted.

## Running and testing

Install the extra editor dependencies on top of the monitor's own
requirements, then launch the app:

```
pip install -r requirements.txt -r requirements-editor.txt
python theme-designer.py [theme-name]
```

`theme-name` is optional — omit it and the designer opens `LoopTelemetry` if
present, otherwise the first theme found under `res/themes/`.

Tests run with the standard library's `unittest`, not `pytest` — `pytest` is
not a project dependency:

```
python -m unittest tests.library.editor.test_renderer tests.library.editor.test_designer_model
```

`tests/library/editor/test_renderer.py` is specifically a **parity guard**.
Two pieces of the pipeline had to be duplicated to make headless, attributed
rendering possible at all:

- `EditorLcd.DisplayPILImage` re-implements `LcdSimulated.DisplayPILImage`
  minus the per-paste PNG write.
- `renderer._draw_static_images` / `_draw_static_text` re-implement the loops
  in `library/display.py`, because those loop internally and a bounding box
  can't be attributed to an individual entry from outside.

A duplicate is exactly the kind of thing that rots quietly as the upstream
pipeline changes. `TestEditorLcdParity` asserts `EditorLcd`'s paste geometry
matches an unmodified `LcdSimulated` pixel-for-pixel; `TestStaticLoopParity`
renders every bundled theme through both the renderer's loops and the
untouched `library/display.py` loops and asserts they're pixel-identical. If
either drifts, these tests are the only thing that would notice — nothing
else in the project renders through both paths to compare them.

## Known constraints

- **Qt is deliberately not in `requirements.txt`.** PySide6 lives in
  `requirements-editor.txt` only, so the system monitor doesn't gain ~100 MB
  of Qt just to drive a panel. Install both files together only if you need
  the designer.
- **`pythonnet` does not work on Python 3.14.** It's pinned to `~=3.1.0` for
  Python 3.10+ (`requirements.txt:46`), which has no wheel for 3.14 and
  selects a source build that fails. This affects the monitor's own
  dependencies, not the editor package directly, but it blocks setting up a
  working environment on 3.14.
- **`library/stats.py:752` uses `locale.getdefaultlocale()`**, which is
  removed in Python 3.15. Works today, will break on that upgrade — not
  something this package can fix, since it doesn't own that file.
