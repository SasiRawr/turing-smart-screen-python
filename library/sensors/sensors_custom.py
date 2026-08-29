# SPDX-License-Identifier: GPL-3.0-or-later
#
# turing-smart-screen-python - a Python system monitor and library for USB-C displays like Turing Smart Screen or XuanFang
# https://github.com/mathoudebine/turing-smart-screen-python/
#
# Copyright (C) 2021 Matthieu Houdebine (mathoudebine)
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

# This file allows to add custom data source as sensors and display them in System Monitor themes
# There is no limitation on how much custom data source classes can be added to this file
# See CustomDataExample theme for the theme implementation part

import math
import platform
import sys
import time
from abc import ABC, abstractmethod
from typing import List

from library.log import logger


# Custom data classes must be implemented in this file, inherit the CustomDataSource and implement its 2 methods
class CustomDataSource(ABC):
    @abstractmethod
    def as_numeric(self) -> float:
        # Numeric value will be used for graph and radial progress bars
        # If there is no numeric value, keep this function empty
        pass

    @abstractmethod
    def as_string(self) -> str:
        # Text value will be used for text display and radial progress bar inner text
        # Numeric value can be formatted here to be displayed as expected
        # It is also possible to return a text unrelated to the numeric value
        # If this function is empty, the numeric value will be used as string without formatting
        pass

    @abstractmethod
    def last_values(self) -> List[float]:
        # List of last numeric values will be used for plot graph
        # If you do not want to draw a line graph or if your custom data has no numeric values, keep this function empty
        pass


# Example for a custom data class that has numeric and text values
class ExampleCustomNumericData(CustomDataSource):
    # This list is used to store the last 10 values to display a line graph
    last_val = [math.nan] * 10  # By default, it is filed with math.nan values to indicate there is no data stored

    def as_numeric(self) -> float:
        # Numeric value will be used for graph and radial progress bars
        # Here a Python function from another module can be called to get data
        # Example: self.value = my_module.get_rgb_led_brightness() / audio.system_volume() ...
        self.value = 75.845

        # Store the value to the history list that will be used for line graph
        self.last_val.append(self.value)
        # Also remove the oldest value from history list
        self.last_val.pop(0)

        return self.value

    def as_string(self) -> str:
        # Text value will be used for text display and radial progress bar inner text.
        # Numeric value can be formatted here to be displayed as expected
        # It is also possible to return a text unrelated to the numeric value
        # If this function is empty, the numeric value will be used as string without formatting
        # Example here: format numeric value: add unit as a suffix, and keep 1 digit decimal precision
        return f'{self.value:>5.1f}%'
        # Important note! If your numeric value can vary in size, be sure to display it with a default size.
        # E.g. if your value can range from 0 to 9999, you need to display it with at least 4 characters every time.
        # --> return f'{self.as_numeric():>4}%'
        # Otherwise, part of the previous value can stay displayed ("ghosting") after a refresh

    def last_values(self) -> List[float]:
        # List of last numeric values will be used for plot graph
        return self.last_val


# Example for a custom data class that only has text values
class ExampleCustomTextOnlyData(CustomDataSource):
    def as_numeric(self) -> float:
        # If there is no numeric value, keep this function empty
        pass

    def as_string(self) -> str:
        # If a custom data class only has text values, it won't be possible to display graph or radial bars
        return "Python: " + platform.python_version()

    def last_values(self) -> List[float]:
        # If a custom data class only has text values, it won't be possible to display line graph
        pass


# ---------------------------------------------------------------------------
# Loop telemetry: thermistor-backed coolant sensors and radiator delta-T
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS
#   A sealed AIO (e.g. Arctic Liquid Freezer III Pro) reports no coolant
#   temperature over USB and has no port for an inline probe. The workaround is
#   two 10k NTC thermistors taped to the tubing -- one on the hot line leaving
#   the pump, one on the cold line leaving the radiator -- read either by a
#   motherboard T_SENSOR header or by a controller such as an Aquacomputer
#   Quadro. This module surfaces those probes as first-class sensors, and
#   derives the radiator delta-T, which is more informative than either
#   absolute reading on its own.
#
# CONFIGURE ME
#   Edit LOOP_SENSORS below to match the names your machine actually exposes.
#   Open LibreHardwareMonitor and read the tree: the first element is a
#   case-insensitive substring of the *hardware* node name, the second is the
#   exact *sensor* name beneath it.
#
#     Motherboard T_SENSOR header : ("Nuvoton", "Temperature #2")
#     Aquacomputer Quadro         : ("Quadro",  "Sensor 1")
#
#   Run `python -m library.sensors.sensors_custom` on the target machine to
#   print every temperature sensor LibreHardwareMonitor can see, then copy the
#   names you want from that listing.

LOOP_SENSORS = {
    # logical name -> (hardware node substring, exact sensor name)
    #
    # Defaults target an ASUS ROG Crosshair VIII Dark Hero, which has dedicated
    # W_IN / W_OUT water-temperature headers feeding the ASUS EC. Confirm the
    # exact labels with the discovery command below -- ROG boards have shipped
    # these as "Water Temperature (In)", "Water_In", and plain "Temperature 4"
    # depending on board and HWiNFO version.
    "coolant_hot": ("Crosshair", "Water Temperature (In)"),
    "coolant_cold": ("Crosshair", "Water Temperature (Out)"),
}

# Number of samples retained for line graphs.
HISTORY_DEPTH = 10


def _lhm_module():
    # Piggyback on the LibreHardwareMonitor backend only if the application
    # already loaded it (HW_SENSORS: LHM). Importing it directly is unsafe:
    # that module hard-exits the process when not running as administrator,
    # and it is Windows-only, which would break SIMU/STUB development on any
    # other platform.
    return sys.modules.get("library.sensors.sensors_librehardwaremonitor")


def _iter_hardware(nodes):
    for node in nodes:
        yield node
        yield from _iter_hardware(node.SubHardware)


def _hwinfo():
    # Imported lazily and defensively: the module is Windows-only and its import
    # must never take down a SIMU/STUB session on another platform.
    try:
        from library.sensors import hwinfo_shm
        return hwinfo_shm
    except Exception:
        return None


def active_backend() -> str:
    # Which source is actually supplying values right now.
    hwinfo = _hwinfo()
    if hwinfo is not None and hwinfo.is_available():
        return "hwinfo"
    if _lhm_module() is not None:
        return "lhm"
    return "none"


def read_loop_temperature(key: str):
    # Returns degrees Celsius, or None when the sensor is unavailable.
    try:
        hw_match, sensor_name = LOOP_SENSORS[key]
    except KeyError:
        logger.warning("No LOOP_SENSORS entry named '%s'", key)
        return None

    # HWiNFO first: it needs no native extension, so it works where the
    # LibreHardwareMonitor backend cannot be installed at all (pythonnet has no
    # wheels beyond Python 3.13).
    hwinfo = _hwinfo()
    if hwinfo is not None:
        try:
            value = hwinfo.find_reading(hw_match, sensor_name, kind="temperature")
            if value is not None:
                return value
        except hwinfo.HWiNFOUnavailable:
            pass  # fall through to LHM

    mod = _lhm_module()
    if mod is None:
        return None

    needle = hw_match.lower()
    temperature_type = mod.Hardware.SensorType.Temperature
    for hardware in _iter_hardware(mod.handle.Hardware):
        if needle not in str(hardware.Name).lower():
            continue
        hardware.Update()
        for sensor in hardware.Sensors:
            if sensor.SensorType == temperature_type and str(sensor.Name) == sensor_name:
                return sensor.Value
    return None


def list_temperature_sensors() -> List[str]:
    # Diagnostic helper: every temperature sensor LHM can see, formatted so the
    # lines can be pasted straight into LOOP_SENSORS.
    mod = _lhm_module()
    if mod is None:
        return []

    found = []
    temperature_type = mod.Hardware.SensorType.Temperature
    for hardware in _iter_hardware(mod.handle.Hardware):
        hardware.Update()
        for sensor in hardware.Sensors:
            if sensor.SensorType == temperature_type:
                found.append(f'("{hardware.Name}", "{sensor.Name}")  # currently {sensor.Value}')
    return found


class _LoopTemperature(CustomDataSource):
    # Base class for a single thermistor. Subclasses set SENSOR_KEY.
    SENSOR_KEY = None
    # Fallback sweep used when no live sensor is available, so themes can be
    # developed against REVISION: SIMU without the hardware present.
    SIMULATED_BASE = 30.0
    SIMULATED_SWING = 4.0

    def __init__(self):
        self.last_val = [math.nan] * HISTORY_DEPTH

    def as_numeric(self) -> float:
        value = read_loop_temperature(self.SENSOR_KEY)
        if value is None:
            # No probe wired up (or not running with HW_SENSORS: LHM): sweep a
            # plausible value so the layout can still be reviewed.
            phase = time.time() / 20.0
            value = self.SIMULATED_BASE + self.SIMULATED_SWING * math.sin(phase)

        value = float(value)
        self.last_val.append(value)
        self.last_val.pop(0)
        return value

    def as_string(self) -> str:
        return f"{self.as_numeric():.1f}°C"

    def last_values(self) -> List[float]:
        return self.last_val


class CoolantHot(_LoopTemperature):
    # Coolant leaving the pump, entering the radiator.
    SENSOR_KEY = "coolant_hot"
    SIMULATED_BASE = 34.0


class CoolantCold(_LoopTemperature):
    # Coolant leaving the radiator, returning to the pump.
    SENSOR_KEY = "coolant_cold"
    SIMULATED_BASE = 30.5


class RadiatorDeltaT(CustomDataSource):
    # Temperature drop across the radiator. Rises with heat load and falls as
    # the fans catch up, so it reads as "how hard the loop is working" in a way
    # neither absolute probe does.
    def __init__(self):
        self.last_val = [math.nan] * HISTORY_DEPTH
        self._hot = CoolantHot()
        self._cold = CoolantCold()

    def as_numeric(self) -> float:
        delta = self._hot.as_numeric() - self._cold.as_numeric()
        self.last_val.append(delta)
        self.last_val.pop(0)
        return delta

    def as_string(self) -> str:
        return f"{self.as_numeric():.1f}"

    def last_values(self) -> List[float]:
        return self.last_val


class LoopStatus(CustomDataSource):
    # Text-only summary line. Reports plainly when probes are not wired up yet,
    # rather than showing a fabricated temperature as if it were real.
    def as_numeric(self) -> float:
        pass

    def as_string(self) -> str:
        backend = active_backend()
        if backend == "none":
            return "SIMULATED"
        missing = [k for k in LOOP_SENSORS if read_loop_temperature(k) is None]
        if missing:
            return "PROBE MISSING: " + ", ".join(missing)
        return "LOOP OK / " + backend.upper()

    def last_values(self) -> List[float]:
        pass


if __name__ == "__main__":
    # Sensor discovery. Run on the target machine:
    #     python -m library.sensors.sensors_custom
    #
    # Tries HWiNFO shared memory first -- it needs no third-party packages and
    # no elevation, so it works on a bare Python install. Falls back to
    # LibreHardwareMonitor only if pythonnet is actually present.
    print("Probing available sensor backends...\n")

    printed_any = False

    hwinfo = _hwinfo()
    if hwinfo is not None:
        try:
            readings = hwinfo.read_all()
            temps = [r for r in readings if r.kind == "temperature"]
            print("HWiNFO shared memory: %d readings, %d of them temperatures.\n"
                  % (len(readings), len(temps)))
            print("Temperature readings -- paste the ones you want into LOOP_SENSORS:\n")
            for r in sorted(temps, key=lambda x: (x.sensor, x.label)):
                print('    ("%s", "%s")  # currently %.1f %s' % (r.sensor, r.label, r.value, r.unit))
            print("\nFan readings (for reference):\n")
            for r in sorted((r for r in readings if r.kind == "fan"),
                            key=lambda x: (x.sensor, x.label)):
                print('    ("%s", "%s")  # currently %.0f %s' % (r.sensor, r.label, r.value, r.unit))
            printed_any = True
        except hwinfo.HWiNFOUnavailable as err:
            print("HWiNFO shared memory unavailable: %s\n" % err)
    else:
        print("HWiNFO reader could not be imported.\n")

    try:
        import library.sensors.sensors_librehardwaremonitor  # noqa: F401  (Windows + pythonnet)
    except ImportError as err:
        print("LibreHardwareMonitor backend unavailable: %s" % err)
        print("(Expected on Python 3.14 -- pythonnet ships wheels for 3.7-3.13 only.)")
    else:
        lhm_found = list_temperature_sensors()
        print("\nLibreHardwareMonitor: %d temperature sensor(s):\n" % len(lhm_found))
        for line in lhm_found:
            print("    " + line)
        printed_any = printed_any or bool(lhm_found)

    if not printed_any:
        print("\nNo sensor source is currently readable. To use HWiNFO:")
        print("  1. Start HWiNFO64.")
        print("  2. Settings -> Main Settings -> tick 'Shared Memory Support'.")
        print("  3. Leave it running, then re-run this command.")
        print("  Note: on the free edition that setting switches itself off after ~12 hours.")
