# snappyHexMeshDict knowledge and tuning guide

Use this reference when inspecting or changing `system/snappyHexMeshDict`.
It is a compact, distributable knowledge layer derived from the user's authored
OpenFOAM book, chapter 10, principally pages 246-268 and 282-290. The examples
shown in the source use OpenFOAM v1912-era syntax, and the leak-detection section
notes functionality available from v1812. Treat provider and release differences
as real: retain the case's existing syntax and verify unsupported or unfamiliar
keywords against the installed OpenFOAM/iconCFD release.

## Mental model and required evidence

`snappyHexMesh` starts from a background mesh, removes/refines cells around
geometry, snaps the surviving mesh to surfaces and features, and optionally
extrudes boundary layers. The top-level switches correspond to those stages:

- `castellatedMesh`: retain the desired region and refine geometry/features.
- `snap`: move the castellated boundary onto the target surface.
- `addLayers`: add boundary-layer cells after snapping.

Diagnose stages independently. First establish a valid background mesh and
castellated region, then validate snapping, and add layers last. Do not compensate
for an early-stage geometry or refinement error by tuning a later stage.

Before proposing changes, collect:

1. the current dictionary and provider/version;
2. `constant/triSurface` file names, named STL solids/regions, units, closure, and
   the geometry-analysis report when available;
3. the background-mesh scale and whether `locationInMesh` is inside the intended
   retained fluid/solid region;
4. stage logs, final cell count, and `checkMesh` metrics;
5. visual evidence when the failure is positional, a feature is missing, layers
   are absent, or a leak path is suspected.

## Dictionary map

### `geometry`

Register every surface and searchable refinement object referenced elsewhere.
Triangulated surfaces normally live under `constant/triSurface`. Preserve the
mapping between an STL file's solid/region names and the patch names used in the
case. One STL may contain multiple named regions; multiple STL files may be
registered independently. Searchable primitives such as boxes, cylinders, and
spheres can define controlled refinement regions without adding surface patches.

Common failure evidence:

- an unknown geometry/region error indicates a name mismatch;
- a missing patch suggests that STL solid/region names and `refinementSurfaces`
  were not mapped consistently;
- unexpected inside/outside removal points first to surface closure and
  `locationInMesh`, not to snap or layer controls.

### `castellatedMeshControls`

This section owns cell removal, feature/surface/region refinement, and refinement
transitions.

| Entry/family | Purpose and tuning interpretation |
| --- | --- |
| `maxLocalCells`, `maxGlobalCells` | Resource guardrails, not target-cell controls. If a run approaches them, reduce the refinement source that creates cells. Keep the project hard cap authoritative. |
| `minRefinementCells` | Stops a refinement pass when too few cells would be refined; avoid using it to hide required local detail. |
| `maxLoadUnbalance` | Controls when parallel load balancing is performed; it is not a mesh-quality tuning knob. |
| `nCellsBetweenLevels` | Widens or narrows the transition between refinement levels. Increase cautiously for gentler transitions when local concavity/non-orthogonality evidence points to abrupt level changes. |
| `features` | Associates extracted feature-edge files such as `.eMesh` with refinement levels. Use only after confirming the feature file and the need for explicit capture. |
| `refinementSurfaces` | Sets per-surface or per-region minimum/maximum refinement. These levels are primary cell-count and surface-resolution controls. |
| `resolveFeatureAngle` | Controls refinement changes across surface angles; distinguish this from snap and layer feature angles. |
| `refinementRegions` | Applies refinement inside, outside, or at a distance from registered searchable geometry, depending on provider syntax. |
| `locationInMesh` | Identifies the region to retain. It must lie clearly inside the intended region, away from a surface or unresolved gap. |
| `allowFreeStandingZoneFaces` | Provider-specific zone/baffle behavior; preserve existing intent and do not toggle without zone evidence. |

For a difficult small feature, establish a surface level that creates the patch.
Then raise the maximum level until additional refinement no longer improves the
shape. Only then raise the minimum level if that resolution is required broadly.
For a large positional mismatch, improve feature-edge refinement first, then
surface maximum refinement, then surface minimum refinement. Change one family
per trial so the effect remains attributable.

### `snapControls`

This section moves the castellated boundary onto geometry.

| Entry | Purpose and evidence for adjustment |
| --- | --- |
| `nSmoothPatch` | Surface smoothing passes before displacement. Adjust for visibly rough snapping or localized non-orthogonality, while checking that small features are not rounded away. |
| `tolerance` | Attraction range relative to local mesh size. A larger value reaches farther but can capture the wrong nearby surface across a narrow gap. |
| `nSolveIter` | Mesh-displacement solution iterations. Increase when the log or geometry suggests incomplete convergence, not as a substitute for insufficient refinement. |
| `nRelaxIter` | Relaxation iterations during snapping. Increase cautiously for unstable or low-quality displacement. |
| `nFeatureSnapIter` | Feature-snapping iterations. It matters only when feature capture is enabled and features exist. |
| `implicitFeatureSnap` | Detects features from surface geometry. Provider/version behavior may differ. |
| `explicitFeatureSnap` | Snaps to explicitly extracted feature edges referenced by `features`. Requires a valid feature-edge file. |
| `multiRegionFeatureSnap` | Extends explicit feature capture across multiple surface regions; enable only for a verified multi-region need. |

If a surface is still stair-stepped, first confirm that castellated refinement is
adequate. If sharp edges are missing, confirm the extracted features and explicit
feature configuration before increasing feature-snap iterations. In a narrow gap,
compare local cell size and clearance before increasing `tolerance`.

### `addLayersControls`

Add layers only after the non-layer mesh is acceptable. Important families are:

- patch selection and `nSurfaceLayers`;
- `relativeSizes`, which determines whether thickness values are relative to the
  local final cell size or absolute (provider syntax must be confirmed);
- `expansionRatio`, terminal/initial/total thickness keywords, and `minThickness`;
- feature and termination controls such as `nGrow`, `featureAngle`,
  `nBufferCellsNoExtrude`, and layer iteration limits;
- surface-normal, mesh-normal, and thickness smoothing;
- collision/medial-axis controls such as face-thickness and medial ratios.

When layers disappear locally, inspect which rejection criterion stopped
extrusion. Reduce ambition locally or adjust layer growth/smoothing within the
existing acceptance policy. Never weaken `meshQualityControls` merely to report
more layers. Recheck cell count because layer addition can materially increase it.

### `meshQualityControls`

These limits constrain cell movement and layer growth during meshing. Typical
families include non-orthogonality, boundary/internal skewness, concavity,
pyramid/tetrahedral quality, minimum volume/area, twist, determinant, face weight,
volume ratio, smoothing, and error-reduction controls. Exact names and semantics
vary by release.

Treat this section and `system/meshQualityDict` as acceptance policy. The agent
may report which limit blocked a mesh, but it must not silently weaken a limit.
Run the configured `checkMesh` workflow after every candidate; use the project's
custom mesh-quality checks when present and use parallelism from
`system/decomposeParDict`.

## Symptom-to-action guide

| Symptom/evidence | Investigate first | Candidate family | Mandatory verification |
| --- | --- | --- | --- |
| Cell count exceeds target/cap | Background divisions and which refinement source dominates | Surface/region levels; background divisions | Cell count plus preservation of small gaps/features |
| Patch or small opening is absent | STL closure/names, base cell size, surface minimum/maximum levels | Castellated refinement | Patch list, visual section, `checkMesh` |
| Large surface mismatch | Feature extraction and feature refinement before broad refinement | Feature level, then surface max, then min | Shape change per stage and cell-count growth |
| Abrupt transition causes local poor cells | Location of failures relative to level boundaries | `nCellsBetweenLevels` | Failure counts, max non-orthogonality, total cells |
| Stair-stepped surface after snap | Castellated resolution before snap convergence | Surface refinement, then snap smoothing/solve | Stage visualization and quality metrics |
| Sharp edge missed | Extracted feature file and explicit/implicit mode | Feature configuration and iterations | Feature-edge overlay and final edge capture |
| Wrong surface captured across a gap | Clearance versus local cell size and attraction range | Refinement and snap `tolerance` | Gap remains open; no cross-gap snapping |
| Layers missing or terminating | Layer rejection messages and local curvature/gaps | Thickness/growth/smoothing family | Layer coverage, quality, and cell count |
| Mesh retained on wrong side or leaks out | Surface closure and `locationInMesh` | Geometry repair/retained-region setup | Boundary/region inspection and leak path |

## Geometry integrity and leak diagnosis

Surface defects and unresolved narrow gaps can destroy the intended inside/outside
classification. Do not try to solve a true STL hole only by refinement. Use STL
analysis to find open/non-manifold geometry and clearance risks. Where the provider
supports leak-path output, use an inside point and an outside point to find a path
through cells; the resulting path can localize a missing face or unresolved gap.
This is provider/version-specific and must be implemented through a registered,
fixed workflow before execution.

## Safe iteration protocol

1. Preserve the baseline dictionaries and record the provider/version.
2. Validate geometry registration, background mesh, and `locationInMesh`.
3. Run/inspect castellated output with snap and layers disabled when stage
   isolation is needed.
4. Add snap and validate geometry fit and quality.
5. Add layers last and validate coverage, quality, and cell count.
6. Change one parameter family per candidate, restore the baseline between
   independent candidates, and keep all values within `.cfd-agent.json`.
7. Accept only when hard cell-count and quality constraints pass. Do not infer
   success solely from a normal snappyHexMesh termination message.

The controller remains the only execution route. This reference supplies
reasoning and review criteria; it does not authorize unregistered commands or
new mutable dictionary paths.
