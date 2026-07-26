# Architecture

The module is a 3D Slicer scripted loadable module. Its layout is dictated by Slicer more
than by preference: the scripted-modules directory is on `sys.path`, and a module file must
be named after the class it defines.

```
ReformatViz/
├── ReformatViz.py          Slicer + Qt glue: widget, MRML nodes, signal wiring
├── ReformatVizLib/         the Slicer-free core
│   ├── detections.py       the detection record: schema, validation, JSON I/O
│   ├── geometry.py         the reformatting maths, in RAS millimetres
│   └── rendering.py        colour conversion and the opacity ramp
├── Resources/UI/           the Qt Designer layout
└── Testing/Python/         build-time test scaffolding
```

## The import boundary

**`ReformatVizLib` may not import `slicer`, `vtk`, `qt` or `ctk`.** `ruff` enforces this via
`flake8-tidy-imports` banned-api rules in `pyproject.toml`, so breaking it fails CI rather
than eroding quietly.

The reason is testability. Everything behind the boundary is a function over numbers, lists
and dicts, so `tests/` runs it in a fraction of a second with no GUI. Before the split, none
of this logic could run without launching Slicer and clicking — which is why the project had
no tests at all. When adding code, ask whether it *needs* an MRML node; if not, it belongs
behind the boundary.

Slicer objects are the glue's business. The pattern is: read plain values out of widgets and
nodes, compute in `ReformatVizLib`, write the result back into nodes.

## The reformatting

This is what the module exists for. The reviewer needs to see an aneurysm without
foreshortening, so the slice must **contain the aneurysm axis** (`P1` ostium → `P2` dome),
and the reviewer spins the view around that axis.

`geometry.rotate2Z(v)` returns a rotation whose **third row is the unit `v`** — it maps `v`
onto +Z, with the first two rows an arbitrary but deterministic orthonormal completion. A
vertical `v` has no horizontal direction to complete the frame from, so it gets an
axis-aligned frame instead (the identity for straight up, a half turn about X for straight
down). Only a zero-length `v` falls back to the identity regardless of direction, so callers
always get a usable frame.

`geometry.aneurysm_frame(P1, P2)` derives the `(axis, origin, Rz)` triple the rest depends
on. Then, per slider movement:

1. `slice_normal(Rz, angle)` = `Rz.T @ [cos θ, sin θ, 0]`, a unit vector **perpendicular to
   the axis** — which is exactly what keeps the axis inside the slice at every angle. This is
   the invariant `tests/test_geometry.py` guards hardest.
2. Slicer settles the slice orientation; the code reads the resulting slice axes back, and
   `in_plane_roll_deg` computes the roll that points the aneurysm axis along slice +Y, so the
   aneurysm stays upright instead of tumbling as the view rotates.
3. All three slice nodes are re-centred on `origin`.

The Red slice also runs slab reconstruction in maximum-intensity-projection mode; the
thickness slider sets the slab thickness in millimetres.

## Volume rendering

`rendering.scalar_opacity_points` builds a fixed 5-point opacity ramp — transparent below
`threshold - margin`, rising to the chosen opacity at `threshold`, then a plateau. The point
count is fixed so the ramp can be updated in place rather than rebuilt, which keeps the
sliders responsive. **Translucent** zeroes the plateau, leaving a thin shell at the
threshold: that is how the vessel surface is shown in 3D without occluding the reformatted
slice behind it.

## State in the widget

### Index-parallel scene nodes

Five collections are indexed in lockstep with `self.detections`:

- `self.spheres[i]`, `self.lines[i]` — a `vtkMRMLModelNode` each, per detection
- `self.confidences`, `self.ostia`, `self.domes` — three single `vtkMRMLMarkupsFiducialNode`s
  whose **Nth control point** belongs to detection *N*

Anything that adds a detection must extend all five (`add_detection_to_scene`), and `reset()`
tears all five down together. There is no per-detection removal path, and nothing in the UI
deletes a single detection — which is what makes the positional scheme survivable. Adding
deletion means threading it through all five collections plus the table rows, which is why
replacing this with an object that owns its own nodes is the obvious next refactor.

### Selection

`self.selected_detection` is the table row index, `-1` for none. Every `set_*_visibility`
method applies the same rule: *show if `i == selected_detection` or `selected_detection < 0`*
— no selection shows everything. `onRowSelected` → `updateObjectsDisplay()` +
`prepare_slices()` is the only path that reacts to selection.

### Scene ownership

`__init__` calls `clearScene()`, so opening the module wipes the scene. This is deliberate:
`load_files()` takes `getNodesByClass("vtkMRMLScalarVolumeNode")[0]` and `onCropChanged()`
takes the first `vtkMRMLMarkupsROINode`, both of which would latch onto foreign nodes
otherwise. Removing the wipe means fixing those lookups first.

The widget observes the scene's `StartCloseEvent`/`EndCloseEvent` (registered at the end of
`setup()`, through the inherited `VTKObservationMixin`), so a scene closed from the File menu
while the module is open does not leave its state pointing at removed nodes. `onSceneEndClose`
recreates the three shared markups nodes and calls `forgetSceneState()`.

`forgetSceneState()` is the half of `reset()` that touches nothing in the scene: it empties
`detections`, `spheres`, `lines` and the table, drops the volume references and unselects. The
split matters because the two callers have opposite obligations — **Clean** and the next patient
load must remove the nodes themselves, while after a scene close there is nothing left to remove
and reaching for those nodes is what would go wrong. `cleanup()` drops the observers before its
own `clearScene()`, so the module's teardown does not run the handler against a widget that is
being destroyed.

## Slicer and Qt conventions

- Widgets come from the `.ui` file through `slicer.util.childWidgetVariables()`, so
  `self.ui.<objectName>` mirrors the Qt Designer object names exactly. Renaming a widget in
  Designer breaks the Python side silently — grep for the old name.
- This is PythonQt, not PyQt: `.checked`, `.value`, `.minimum`/`.maximum` are assignable
  properties, not methods.
- Both signal styles appear (`x.clicked.connect(f)` and `x.connect('clicked(bool)', f)`);
  both work. Match whichever is adjacent.
- Connect table signals in `setup()`, never in `add_detection_to_table()` — that runs once
  per row, so anything connected there accumulates duplicate slots.
- Positions are RAS world coordinates; use `GetNthControlPointPositionWorld` with a
  pre-allocated `np.empty(3)` out-parameter.
