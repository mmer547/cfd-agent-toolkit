# Using STL measurements for mesh planning

| Geometry metric | Mesh-planning use |
|---|---|
| Bounding-box extents | Check background-domain coverage and padding |
| Maximum extent / diagonal | Establish scale and detect unit mistakes |
| Minimum opposed clearance | Bound cell size needed to resolve narrow gaps |
| Region bounds and areas | Select named surfaces that may need refinement |
| Connected components | Detect separate bodies and unexpected fragments |
| Edge-length distribution | Diagnose STL tessellation; do not copy it blindly into volume-cell size |

For clearance `g` and `n` requested cells across it, start from
`target_cell <= g / n`. If the local background cell size is `h`, a theoretical
binary refinement level is `ceil(log2(h / target_cell))`, clamped to supported
and allowlisted levels. Validate with the real mesher and `checkMesh`; geometry
analysis alone cannot predict final cell count or quality.

## Guardrails

- Verify coordinate units against the CFD case.
- Inspect the closest triangle regions and indices. Patch seams, duplicate
  facets, intersections, and dirty STL can yield misleading clearance.
- Change only entries explicitly allowlisted in `.cfd-agent.json`.
- Ask the user for the maximum cell count before using the geometry report to
  plan mesh adjustments. Keep that user-supplied constraint and the quality
  criteria independent from geometry-derived resolution targets. Report
  conflicts instead of relaxing quality.
