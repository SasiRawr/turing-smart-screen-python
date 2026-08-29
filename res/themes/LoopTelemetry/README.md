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
    "coolant_hot":  ("Quadro", "Sensor 1"),   # pump outlet -> radiator inlet
    "coolant_cold": ("Quadro", "Sensor 2"),   # radiator outlet -> pump inlet
}
```

The first element is a case-insensitive substring of the hardware node name,
the second is the exact sensor name beneath it.

To discover the real names, run this on the target machine **as
administrator**:

```
python -m library.sensors.sensors_custom
```

It prints every temperature sensor LibreHardwareMonitor can see, already
formatted so the lines can be pasted straight into `LOOP_SENSORS`.

Typical entries:

| Source | Looks like |
| --- | --- |
| Motherboard `T_SENSOR` header | `("Nuvoton", "Temperature #2")` |
| Aquacomputer Quadro | `("Quadro", "Sensor 1")` |

### 2. Select the theme

In `config.yaml`:

```yaml
config:
  THEME: LoopTelemetry
  HW_SENSORS: LHM      # Windows, must run as administrator
display:
  REVISION: A          # whichever revision your panel actually is
```

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
| `LOOP OK` | Both probes reporting |
| `PROBE MISSING: coolant_hot` | Named probe is configured but not returning a value |
| `SIMULATED` | Not running under `HW_SENSORS: LHM` — the coolant figures on screen are a generated sweep, not real measurements |

`SIMULATED` is deliberately loud. A dashboard that invents plausible
temperatures without saying so is worse than one that shows nothing.

## Known limitation

The layout is fixed at 480×320. A rev 1.x (`TUR_USB`) panel — 4.6", 5.2", 8.0",
8.8", 9.2" — has a different resolution and the coordinates will need
re-flowing once the target panel is known.
