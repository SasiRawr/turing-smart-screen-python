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
    "coolant_hot": ("Quadro", "Sensor 1"),   # pump outlet -> radiator inlet
    "coolant_cold": ("Quadro", "Sensor 2"),  # radiator outlet -> pump inlet
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


def read_loop_temperature(key: str):
    # Returns degrees Celsius, or None when the sensor is unavailable.
    mod = _lhm_module()
    if mod is None:
        return None

    try:
        hw_match, sensor_name = LOOP_SENSORS[key]
    except KeyError:
        logger.warning("No LOOP_SENSORS entry named '%s'", key)
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
        if _lhm_module() is None:
            return "SIMULATED"
        missing = [k for k in LOOP_SENSORS if read_loop_temperature(k) is None]
        if missing:
            return "PROBE MISSING: " + ", ".join(missing)
        return "LOOP OK"

    def last_values(self) -> List[float]:
        pass


if __name__ == "__main__":
    # Run this on the target machine, as administrator, to discover sensor
    # names:  python -m library.sensors.sensors_custom
    import library.sensors.sensors_librehardwaremonitor  # noqa: F401  (Windows only)

    sensors_found = list_temperature_sensors()
    if not sensors_found:
        print("No temperature sensors found. Are you running as administrator on Windows?")
    else:
        print(f"{len(sensors_found)} temperature sensor(s) visible to LibreHardwareMonitor:\n")
        for line in sensors_found:
            print("    " + line)
