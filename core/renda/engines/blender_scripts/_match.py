"""Name matching for material/texture overrides. Standard library only: imported by renda core
and by the Blender scripts (which run inside Blender's Python without renda's dependencies).

A pattern is one of:
- an exact name ("Wood.001" matches only "Wood.001"),
- a case-insensitive glob ("wood*", "*_screen", "Mat[12]"),
- a regular expression written as /expr/flags ("/^wood(_\\d+)?$/i"); flags: i, m, s, x.
"*" (or None) matches everything.
"""

from __future__ import annotations

import fnmatch
import re
from functools import lru_cache

_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}


@lru_cache(maxsize=512)
def compile_pattern(pattern: str):
    """→ predicate(name) -> bool. Raises ValueError for a malformed /regex/."""
    if pattern == "*":
        return lambda name: True
    m = re.fullmatch(r"/(.*)/([imsx]*)", pattern, re.DOTALL)
    if m:
        flags = 0
        for f in m.group(2):
            flags |= _FLAGS[f]
        try:
            rx = re.compile(m.group(1), flags)
        except re.error as e:
            raise ValueError(f"bad regex {pattern!r}: {e}") from None
        return lambda name: rx.search(name) is not None
    low = pattern.lower()
    return lambda name: name == pattern or fnmatch.fnmatchcase(name.lower(), low)


def matches(pattern: str | None, name: str | None) -> bool:
    if pattern is None:
        return True
    if name is None:
        return pattern == "*"
    return compile_pattern(pattern)(name)


def basename(path: str | None) -> str | None:
    if not path:
        return None
    return re.split(r"[\\/]", str(path))[-1]
