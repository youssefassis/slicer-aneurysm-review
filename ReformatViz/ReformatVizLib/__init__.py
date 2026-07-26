# Copyright the ReformatViz authors. Licensed under the Apache License, Version 2.0.
"""Slicer-free core of the ReformatViz module.

Nothing in this package may import ``slicer``, ``vtk``, ``qt`` or ``ctk`` — the linter
enforces that (see ``pyproject.toml``). Keeping the boundary means the geometry, the
detection file format and the display maths can be tested with plain pytest in seconds,
instead of only by launching Slicer and clicking.

Slicer object handling lives on the other side of the boundary, in ``ReformatViz.py``.
"""

__version__ = "0.1.0"
