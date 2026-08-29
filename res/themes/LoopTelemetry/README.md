# Loop Telemetry

A 480×320 landscape theme for monitoring a sealed AIO loop, built around two
10k NTC thermistors taped to the coolant tubing.

| Region | Shows |
| --- | --- |
| Top band | Probe status line, clock |
| Left panel | CPU load, load bar, package temperature |
| Right panel | GPU load, load bar, temperature |
| Bottom band | Radiator inlet temp, outlet temp, both as line graphs, and the delta-T across the radiator as a radial |

## Why delta-T

A sealed AIO reports no coolant temperature and has no port for an inline
probe, so both coolant readings here come from surface-mounted thermistors —
one on the hot line leaving the pump, one on the cold line leaving the
radiator. The difference between them is the more useful number: it rises with
heat load and falls as the fans catch up, which neither absolute reading shows
on its own.

## Setup

### 1. Point the sensors at your probes

Edit `LOOP_SENSORS` at the bottom of `library/sensors/sensors_custom.py`:

```python
LOOP_SENSORS = {
    "coolant_hot":  ("Crosshair", "Water Temperature (In)"),
    "coolant_cold": ("Crosshair", "Water Temperature (Out)"),
}
```

The first element is a case-insensitive substring of the hardware node name,
the second is the exact sensor name beneath it.

To discover the real names, run this on the target machine:

```
python -m library.sensors.sensors_custom
```

It probes both backends, prints every temperature and fan reading it can see,
and formats them ready to paste into `LOOP_SENSORS`. No third-party packages
and no elevation are needed for the HWiNFO path.

### Sensor backends

| Backend | Needs | Notes |
| --- | --- | --- |
| HWiNFO shared memory | HWiNFO running with **Shared Memory Support** enabled | Preferred. Stdlib `mmap` only, so it works on any Python version. Free edition disables the setting after ~12 hours; Pro removes the limit. |
| LibreHardwareMonitor | `pythonnet`, Windows, administrator | Only available on Python 3.7–3.13 — pythonnet publishes no 3.14 wheels. |
| Simulated | nothing | Fallback. Status line reads `SIMULATED`. |

Typical entries:

| Source | Looks like |
| --- | --- |
| ROG `W_IN` / `W_OUT` water headers | `("Crosshair", "Water Temperature (In)")` |
| Generic `T_SENSOR` header | `("Nuvoton", "Temperature 4")` |
| Aquacomputer Quadro | `("Quadro", "Sensor 1")` |

Exact labels vary by board and HWiNFO version — always confirm with the
discovery command rather than trusting the table.

### 2. Select the theme

In `config.yaml`:

```yaml
config:
  THEME: LoopTelemetry
  HW_SENSORS: PYTHON   # or LHM, if pythonnet is installable on your Python
display:
  REVISION: A          # whichever revision your panel actually is
```

The coolant sensors pick their own source and do not depend on `HW_SENSORS`.
They try **HWiNFO shared memory** first, fall back to LibreHardwareMonitor, and
otherwise simulate. `HW_SENSORS` only governs the built-in CPU/GPU stats.

## Developing without the panel

```yaml
config:
  THEME: LoopTelemetry
  HW_SENSORS: STUB
display:
  REVISION: SIMU
```

`python main.py` then writes each rendered frame to `screencap.png` instead of
driving hardware, so the layout can be iterated on any machine.

## Reading the status line

| Status | Meaning |
| --- | --- |
| `LOOP OK / HWINFO` | Both probes reporting, via HWiNFO shared memory |
| `LOOP OK / LHM` | Both probes reporting, via LibreHardwareMonitor |
| `PROBE MISSING: coolant_hot` | Named probe is configured but not returning a value |
| `SIMULATED` | No sensor backend readable — the coolant figures on screen are a generated sweep, not real measurements |

`SIMULATED` is deliberately loud. A dashboard that invents plausible
temperatures without saying so is worse than one that shows nothing.

## Known limitation

The layout is fixed at 480×320. A rev 1.x (`TUR_USB`) panel — 4.6", 5.2", 8.0",
8.8", 9.2" — has a different resolution and the coordinates will need
re-flowing once the target panel is known.
