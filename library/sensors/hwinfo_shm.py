# SPDX-License-Identifier: GPL-3.0-or-later
#
# turing-smart-screen-python - a Python system monitor and library for USB-C displays
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

"""Read HWiNFO's Shared Memory v2 interface using only the standard library.

Why this exists
---------------
The LibreHardwareMonitor backend depends on pythonnet, which supports Python
3.7-3.13 only. On a 3.14 install there is no wheel and no LHM path at all.
HWiNFO instead publishes its whole sensor tree into a named shared memory
block, which stdlib `ctypes` can map and read directly -- no native extension,
no .NET, no third-party package, and it works on any Python version.

Requirements on the target machine
----------------------------------
* HWiNFO running, with Settings -> Main Settings -> "Shared Memory Support"
  ticked.
* On the free edition that setting switches itself off after roughly 12 hours
  and has to be re-enabled by hand; HWiNFO Pro removes the limit. `read_all()`
  raises HWiNFOUnavailable when the block disappears, so the caller can say so
  rather than silently freezing on stale values.

Layout below matches the HWiNFO SDK's shared memory structures. Every field the
reader depends on is validated against the header before any value is trusted,
because a layout change would otherwise produce plausible-looking garbage.
"""

import ctypes
import sys
from typing import Dict, List, NamedTuple, Optional

HWINFO_SHM_NAME = r"Global\HWiNFO_SENS_SM2"
HWINFO_MUTEX_NAME = r"Global\HWiNFO_SM2_MUTEX"

# Win32 section-mapping API, used instead of mmap so that a missing section is
# reported rather than silently created -- see _open_mapping below.
FILE_MAP_READ = 0x0004
ERROR_FILE_NOT_FOUND = 2

if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenFileMappingW.argtypes = (
        ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p)
    _kernel32.OpenFileMappingW.restype = ctypes.c_void_p
    # restype must be c_void_p: the default c_int truncates a 64-bit address.
    _kernel32.MapViewOfFile.argtypes = (
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_size_t)
    _kernel32.MapViewOfFile.restype = ctypes.c_void_p
    _kernel32.UnmapViewOfFile.argtypes = (ctypes.c_void_p,)
    _kernel32.UnmapViewOfFile.restype = ctypes.c_int
    _kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    _kernel32.CloseHandle.restype = ctypes.c_int
else:
    _kernel32 = None

# "HWiS" little-endian: the active-interface marker. HWiNFO writes "DEAD" here
# when it shuts the interface down.
HWINFO_SIGNATURE_ACTIVE = 0x53695748

# SENSOR_READING_TYPE
READING_TYPE_NONE = 0
READING_TYPE_TEMP = 1
READING_TYPE_VOLT = 2
READING_TYPE_FAN = 3
READING_TYPE_CURRENT = 4
READING_TYPE_POWER = 5
READING_TYPE_CLOCK = 6
READING_TYPE_USAGE = 7
READING_TYPE_OTHER = 8

READING_TYPE_NAMES = {
    READING_TYPE_NONE: "none",
    READING_TYPE_TEMP: "temperature",
    READING_TYPE_VOLT: "voltage",
    READING_TYPE_FAN: "fan",
    READING_TYPE_CURRENT: "current",
    READING_TYPE_POWER: "power",
    READING_TYPE_CLOCK: "clock",
    READING_TYPE_USAGE: "usage",
    READING_TYPE_OTHER: "other",
}


class HWiNFOUnavailable(RuntimeError):
    """HWiNFO is not running, or Shared Memory Support is off or expired."""


class _SharedMemHeader(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("dwSignature", ctypes.c_uint32),
        ("dwVersion", ctypes.c_uint32),
        ("dwRevision", ctypes.c_uint32),
        ("poll_time", ctypes.c_int64),
        ("dwOffsetOfSensorSection", ctypes.c_uint32),
        ("dwSizeOfSensorElement", ctypes.c_uint32),
        ("dwNumSensorElements", ctypes.c_uint32),
        ("dwOffsetOfReadingSection", ctypes.c_uint32),
        ("dwSizeOfReadingElement", ctypes.c_uint32),
        ("dwNumReadingElements", ctypes.c_uint32),
    ]


class _SensorElement(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("dwSensorID", ctypes.c_uint32),
        ("dwSensorInst", ctypes.c_uint32),
        ("szSensorNameOrig", ctypes.c_char * 128),
        ("szSensorNameUser", ctypes.c_char * 128),
    ]


class _ReadingElement(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("tReading", ctypes.c_uint32),
        ("dwSensorIndex", ctypes.c_uint32),
        ("dwReadingID", ctypes.c_uint32),
        ("szLabelOrig", ctypes.c_char * 128),
        ("szLabelUser", ctypes.c_char * 128),
        ("szUnit", ctypes.c_char * 16),
        ("Value", ctypes.c_double),
        ("ValueMin", ctypes.c_double),
        ("ValueMax", ctypes.c_double),
        ("ValueAvg", ctypes.c_double),
    ]


class Reading(NamedTuple):
    sensor: str   # hardware node, e.g. "ASUS ROG CROSSHAIR VIII DARK HERO"
    label: str    # reading name, e.g. "Water Temperature (In)"
    value: float
    unit: str
    kind: str     # one of READING_TYPE_NAMES


def _decode(raw: bytes) -> str:
    # HWiNFO writes ANSI strings. latin-1 never raises, which matters more here
    # than perfect fidelity on the rare non-ASCII label.
    return raw.split(b"\x00", 1)[0].decode("latin-1", errors="replace").strip()


class _MappedView:
    """Read-only view of an existing named section.

    Exposes only the read/close surface the callers below need, so it drops in
    where an mmap object used to be.
    """

    def __init__(self, handle, address, size):
        self._handle = handle
        self._address = address
        self._size = size

    def read(self, count: int) -> bytes:
        return ctypes.string_at(self._address, min(count, self._size))

    def close(self) -> None:
        if self._address:
            _kernel32.UnmapViewOfFile(self._address)
            self._address = None
        if self._handle:
            _kernel32.CloseHandle(self._handle)
            self._handle = None


def _open_mapping(size: int) -> _MappedView:
    # Do NOT use mmap.mmap(-1, size, tagname=...) here. On Windows that maps the
    # page file and CREATES the named section when it does not already exist, so
    # it succeeds even with HWiNFO not running -- handing back a zero-filled
    # block that then fails the signature check as "found but not active". That
    # sends you to the Shared Memory checkbox when the real problem is that
    # HWiNFO is not running at all. It also squats on HWiNFO's well-known name
    # for as long as the handle is held, which risks HWiNFO later opening our
    # empty section instead of publishing its own.
    #
    # OpenFileMappingW has no create semantics, so absence is reported as
    # absence. Verified on this machine: with HWiNFO stopped, the mmap call
    # opened a section for a name that could not possibly exist.
    if _kernel32 is None:
        raise HWiNFOUnavailable("HWiNFO shared memory is Windows-only.")

    handle = _kernel32.OpenFileMappingW(FILE_MAP_READ, False, HWINFO_SHM_NAME)
    if not handle:
        err = ctypes.get_last_error()
        if err == ERROR_FILE_NOT_FOUND:
            raise HWiNFOUnavailable(
                "%s does not exist. HWiNFO is not running, or is running without "
                "Shared Memory Support. Start HWiNFO64, then tick Settings -> Main "
                "Settings -> Shared Memory Support and leave it running. (On the "
                "free edition that setting switches itself off after ~12 hours.)"
                % HWINFO_SHM_NAME
            )
        raise HWiNFOUnavailable(
            "OpenFileMapping(%s) failed with Windows error %d." % (HWINFO_SHM_NAME, err)
        )

    address = _kernel32.MapViewOfFile(handle, FILE_MAP_READ, 0, 0, size)
    if not address:
        err = ctypes.get_last_error()
        _kernel32.CloseHandle(handle)
        raise HWiNFOUnavailable(
            "MapViewOfFile(%s, %d bytes) failed with Windows error %d."
            % (HWINFO_SHM_NAME, size, err)
        )

    return _MappedView(handle, address, size)


def read_all() -> List[Reading]:
    """Return every reading HWiNFO is currently publishing."""
    if sys.platform != "win32":
        raise HWiNFOUnavailable("HWiNFO shared memory is Windows-only.")

    header_size = ctypes.sizeof(_SharedMemHeader)

    # The block's true length is not known until the header is read, so map the
    # header first, then remap at full size.
    probe = _open_mapping(header_size)
    try:
        header = _SharedMemHeader.from_buffer_copy(probe.read(header_size))
    finally:
        probe.close()

    if header.dwSignature != HWINFO_SIGNATURE_ACTIVE:
        raise HWiNFOUnavailable(
            "Shared memory found but not active (signature 0x%08X). HWiNFO writes "
            "'DEAD' here when the interface is disabled or the free-edition 12-hour "
            "limit has expired -- re-enable Shared Memory Support." % header.dwSignature
        )

    # Refuse to guess if the SDK layout ever changes: wrong sizes would decode
    # into believable nonsense, which is worse than an error.
    expected_sensor = ctypes.sizeof(_SensorElement)
    expected_reading = ctypes.sizeof(_ReadingElement)
    if header.dwSizeOfSensorElement != expected_sensor:
        raise HWiNFOUnavailable(
            "Unexpected sensor element size %d (expected %d); HWiNFO's shared memory "
            "layout has changed and this reader needs updating."
            % (header.dwSizeOfSensorElement, expected_sensor)
        )
    if header.dwSizeOfReadingElement != expected_reading:
        raise HWiNFOUnavailable(
            "Unexpected reading element size %d (expected %d); HWiNFO's shared memory "
            "layout has changed and this reader needs updating."
            % (header.dwSizeOfReadingElement, expected_reading)
        )

    total = (header.dwOffsetOfReadingSection
             + header.dwNumReadingElements * header.dwSizeOfReadingElement)

    buf = _open_mapping(total)
    try:
        raw = buf.read(total)
    finally:
        buf.close()

    sensor_names: Dict[int, str] = {}
    for i in range(header.dwNumSensorElements):
        off = header.dwOffsetOfSensorSection + i * header.dwSizeOfSensorElement
        element = _SensorElement.from_buffer_copy(raw[off:off + expected_sensor])
        # The user-edited name wins when set, matching what HWiNFO itself shows.
        sensor_names[i] = _decode(element.szSensorNameUser) or _decode(element.szSensorNameOrig)

    readings: List[Reading] = []
    for i in range(header.dwNumReadingElements):
        off = header.dwOffsetOfReadingSection + i * header.dwSizeOfReadingElement
        element = _ReadingElement.from_buffer_copy(raw[off:off + expected_reading])
        readings.append(Reading(
            sensor=sensor_names.get(element.dwSensorIndex, "?"),
            label=_decode(element.szLabelUser) or _decode(element.szLabelOrig),
            value=float(element.Value),
            unit=_decode(element.szUnit),
            kind=READING_TYPE_NAMES.get(element.tReading, "other"),
        ))
    return readings


def find_reading(sensor_match: str, label: str, kind: Optional[str] = None) -> Optional[float]:
    """Look up one value. `sensor_match` is a case-insensitive substring of the
    hardware node name; `label` must match the reading name exactly."""
    needle = sensor_match.lower()
    for reading in read_all():
        if needle in reading.sensor.lower() and reading.label == label:
            if kind is None or reading.kind == kind:
                return reading.value
    return None


def is_available() -> bool:
    try:
        read_all()
        return True
    except HWiNFOUnavailable:
        return False


if __name__ == "__main__":
    try:
        all_readings = read_all()
    except HWiNFOUnavailable as err:
        print("HWiNFO shared memory unavailable: %s" % err)
        raise SystemExit(1)

    print("%d readings published by HWiNFO\n" % len(all_readings))
    for r in sorted(all_readings, key=lambda x: (x.sensor, x.kind, x.label)):
        print('    ("%s", "%s")  # %s %.2f %s' % (r.sensor, r.label, r.kind, r.value, r.unit))
