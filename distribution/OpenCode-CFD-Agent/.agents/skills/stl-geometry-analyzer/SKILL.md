---
name: stl-geometry-analyzer
description: Analyze ASCII or binary STL geometry without external Python packages and produce mesh-planning JSON containing bounds, overall dimensions, triangle/region/component statistics, edge lengths, area, volume estimate, and minimum opposed non-adjacent surface clearance. Use when an OpenFOAM or iconCFD case needs geometry-derived mesh sizing, narrow-gap detection, refinement planning, or a repeatable report from constant/triSurface before changing mesh dictionaries.
---

# STL Geometry Analyzer

Analyze geometry before proposing mesh-setting changes. Keep geometry
measurement separate from dictionary mutation.

## Run the analyzer

From a Windows case root, invoke:

```powershell
python <skill-dir>/scripts/analyze_stl.py constant/triSurface --units m --output .cfd-runs/geometry/stl-analysis.json
```

Accept files or directories. Parse both ASCII and binary STL. Do not require
OpenFOAM, WSL, NumPy, SciPy, or trimesh.

## Interpret the report

- Treat `bounds.extents` and `bounds.diagonal` as exact for STL vertices.
- Treat `minimum_opposed_clearance` as a triangulation-based estimate. It
  excludes triangles sharing a vertex and requires opposed normals; it is not
  a CAD tolerance or guaranteed global wall-thickness measurement.
- Check `topology.connected_components`, region names, and the reported
  triangle pair before using the clearance.
- Use `mesh_guidance.maximum_cell_size_for_gap` only as a starting constraint.
- Do not infer units from the file name. Pass the coordinate units explicitly.

Read `references/mesh-usage.md` before converting measurements into
OpenFOAM/iconCFD dictionary candidates.

## Connect to CFD mesh optimization

Give `.cfd-runs/geometry/stl-analysis.json` to `cfd-case-agent`. Use geometry
metrics to rank or bound allowlisted candidates. Never edit a non-allowlisted
entry, weaken `meshQualityDict`, or start CFD processes from this skill.
