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

"""Undo/redo as command objects over the theme document. Deliberately Qt-free
so the whole edit model can be unit tested without a QApplication.

Commands address elements by YAML path (a tuple of keys), not by holding node
references: paths survive a reload and make a command's repr meaningful. A
drag produces ONE MoveElement command on mouse release -- the intermediate
positions re-render live but are never recorded -- and consecutive arrow-key
nudges of the same element coalesce into the command that started them, so
undo returns to where the element was before the burst, not one pixel back.
"""

from typing import Any, Callable, List, Optional, Tuple

ElementPath = Tuple[str, ...]

# Sentinel for "this key did not exist before the command". revert() deletes
# the key instead of writing the sentinel back.
MISSING = object()


def resolve_node(document: dict, path: ElementPath) -> dict:
    node = document
    for key in path:
        node = node[key]
    if not isinstance(node, dict):
        raise KeyError("path %r does not name a mapping" % (path,))
    return node


def _describe(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 24 else text[:21] + "..."


class Command:
    """One reversible document edit."""

    text: str = ""                # human label, e.g. 'Set X = 12'
    coalesce_key: Optional[tuple] = None  # equal keys may merge on the stack

    def apply(self, document: dict) -> None:
        raise NotImplementedError

    def revert(self, document: dict) -> None:
        raise NotImplementedError

    def amend(self, newer: "Command") -> None:
        """Absorb a newer command with the same coalesce_key."""
        raise NotImplementedError


class SetProperty(Command):
    """Set one scalar property of one element."""

    def __init__(self, path: ElementPath, key: str, old: Any, new: Any):
        self.path = tuple(path)
        self.key = key
        self.old = old
        self.new = new
        self.text = "Set %s = %s" % (key, _describe(new))
        self.coalesce_key = ("set", self.path, key)

    def apply(self, document: dict) -> None:
        resolve_node(document, self.path)[self.key] = self.new

    def revert(self, document: dict) -> None:
        node = resolve_node(document, self.path)
        if self.old is MISSING:
            node.pop(self.key, None)
        else:
            node[self.key] = self.old

    def amend(self, newer: "SetProperty") -> None:
        self.new = newer.new
        self.text = newer.text


class MoveElement(Command):
    """Set an element's X and Y in one step (drag release, arrow nudge)."""

    def __init__(self, path: ElementPath, old_xy: Tuple[Any, Any],
                 new_xy: Tuple[int, int], label: str = ""):
        self.path = tuple(path)
        self.old_xy = old_xy      # either value may be MISSING
        self.new_xy = new_xy
        self.text = "Move %s to (%s, %s)" % (
            label or ".".join(self.path), new_xy[0], new_xy[1])
        self.coalesce_key = ("move", self.path)

    def apply(self, document: dict) -> None:
        node = resolve_node(document, self.path)
        node["X"], node["Y"] = self.new_xy

    def revert(self, document: dict) -> None:
        node = resolve_node(document, self.path)
        for key, old in (("X", self.old_xy[0]), ("Y", self.old_xy[1])):
            if old is MISSING:
                node.pop(key, None)
            else:
                node[key] = old

    def amend(self, newer: "MoveElement") -> None:
        self.new_xy = newer.new_xy
        self.text = newer.text


class CompoundCommand(Command):
    """Several commands applied as one undo step, in order."""

    def __init__(self, text: str, commands: List[Command]):
        self.text = text
        self.commands = list(commands)

    def apply(self, document: dict) -> None:
        for command in self.commands:
            command.apply(document)

    def revert(self, document: dict) -> None:
        for command in reversed(self.commands):
            command.revert(document)


class UndoStack:
    """Linear undo/redo over one document, with clean-state tracking.

    The clean index marks the document state that matches the file on disk.
    It starts at 0 (freshly loaded), moves on set_clean() (save), and becomes
    unreachable (None) when the redo branch containing it is discarded or the
    command sitting on it is amended.
    """

    def __init__(self, document: dict,
                 on_change: Optional[Callable[[], None]] = None):
        self._document = document
        self._commands: List[Command] = []
        self._index = 0               # commands[:_index] are applied
        self._clean_index: Optional[int] = 0
        self._on_change = on_change

    # ---- edits ----------------------------------------------------------

    def push(self, command: Command, already_applied: bool = False,
             coalesce: bool = False) -> None:
        """Apply (unless already_applied) and record one command.

        coalesce=True merges into the top command when the coalesce keys
        match -- unless the top command is the clean point, because amending
        it would silently detach the clean state from any reachable index.
        """
        if self._index < len(self._commands):
            if self._clean_index is not None and self._clean_index > self._index:
                self._clean_index = None  # clean state was in the redo branch
            del self._commands[self._index:]

        if not already_applied:
            command.apply(self._document)

        top = self._commands[self._index - 1] if self._index > 0 else None
        if (coalesce and top is not None
                and top.coalesce_key is not None
                and top.coalesce_key == command.coalesce_key
                and self._clean_index != self._index):
            top.amend(command)
        else:
            self._commands.append(command)
            self._index += 1
        self._notify()

    def undo(self) -> Optional[Command]:
        if not self.can_undo():
            return None
        self._index -= 1
        command = self._commands[self._index]
        command.revert(self._document)
        self._notify()
        return command

    def redo(self) -> Optional[Command]:
        if not self.can_redo():
            return None
        command = self._commands[self._index]
        command.apply(self._document)
        self._index += 1
        self._notify()
        return command

    # ---- queries --------------------------------------------------------

    def can_undo(self) -> bool:
        return self._index > 0

    def can_redo(self) -> bool:
        return self._index < len(self._commands)

    def undo_text(self) -> str:
        return self._commands[self._index - 1].text if self.can_undo() else ""

    def redo_text(self) -> str:
        return self._commands[self._index].text if self.can_redo() else ""

    # ---- clean state ----------------------------------------------------

    def is_clean(self) -> bool:
        return self._clean_index == self._index

    def set_clean(self) -> None:
        self._clean_index = self._index
        self._notify()

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change()
