---
name: cfd-case-agent
description: Inspect, plan, execute, and extend utility-level CFD workflows for OpenFOAM or compatible tools such as iconCFD from Codex or OpenCode on Windows/WSL2. Use for cases containing 0, constant, and system when the user asks to inspect a case, run or dry-run a registered utility, safely stage missing tutorial resources described by an Allrun script, validate mesh quality, automatically change allowlisted mesh settings and iterate toward cell-count and checkMesh goals, resume or recover an interrupted mesh optimization, combine tools from different CFD providers, or generate a solver-only Allrun script. Execution is opt-in and mesh-quality acceptance must not be achieved by silently relaxing quality criteria.
---

# CFD Case Agent

Operate from one calculation workspace containing the shared package and one or
more explicitly identified CFD case roots below it. Each case contains
`constant`, `system`, and one of `0`, `0.orig`, or `0.org`.
Use the bundled deterministic controller instead of composing arbitrary shell commands.

## Report progress

- Before each controller call, tell the user the current phase, the purpose of
  the call, and whether it is a dry-run or real execution.
- After each call, summarize the result and name the next action. Select the
  language from an explicit user request first, then the current conversation,
  then `host_locale` in the controller's `inspect` result, and finally English.
  Keep the selected language until the user requests a change.
- Before a long CFD execution, identify the workflow or utility and the
  `.cfd-runs/` log destination. The controller emits utility start and finish
  markers to standard error; translate these into concise user-facing updates.
- On approval waits or errors, immediately report completed work, the exact
  blocker, and the information or permission needed to continue.

## Safety boundary

- Keep all case data and generated artifacts under the calculation workspace. Never send
  case contents to web tools, network services, external directories, or
  unregistered commands. Permit provider-installed tutorial resources only
  through validated `resource_copies` handled by `internal.stageResources`.
  Ask the user to place any other required external input in the case root.
- Default to dry-run. Never pass `--execute` unless the user explicitly asks to start CFD processes.
- Treat command IDs in `registry/commands.json` as the execution allowlist.
- When `system/decomposeParDict` exists with `numberOfSubdomains > 1`, read that
  value and run parallel-capable workflow steps in parallel. When it is absent or
  specifies one subdomain, use a serial workflow; never infer parallelism from CPU count.
- Preserve original dictionaries and record each optimization candidate before changing mesh settings.
- Never make a mesh pass by weakening `meshQualityDict` unless the user explicitly changes the acceptance policy.
- When no other execution policy is explicitly requested, run multi-case
  batches sequentially with exactly one case-level job active at a time. Start
  the next case only after the previous case returns successfully. A case may
  still use the MPI count from its own `decomposeParDict`. Keep concurrent
  case-level execution and job scheduling outside this skill.

## Inspect and plan

Set the workspace and case paths explicitly and invoke:

```powershell
python <skill-dir>/scripts/cfdctl.py --workspace <workspace> --case <case-dir> inspect
python <skill-dir>/scripts/cfdctl.py --workspace <workspace> --case <case-dir> list-commands
python <skill-dir>/scripts/cfdctl.py --workspace <workspace> --case <case-dir> plan mesh
python <skill-dir>/scripts/cfdctl.py --workspace <workspace> --case <case-dir> analyze-mesh --max-cells 6000000
```

When the agent itself is running inside Linux/WSL, use `python3` instead of
`python`. The controller executes providers directly in Linux/WSL and uses
`wsl.exe` only when its host process is Windows.

The controller loads `.cfd-agent.json` from the case when a case-local override
exists; otherwise it loads the shared workspace configuration. Use detected
`vendor` and `version` plus shared profiles to select the provider. Report the
selected config, profile, provider, and decomposition dictionary from every
plan. Shared `case_rules` select specialized workflows from vendor, version,
application, and required case files rather than directory names. Report the
matched rule IDs and any discovered sibling dependencies. Stop when no exact profile is available instead of trying another
installed version.

Discover and preflight multiple cases with:

```powershell
python <skill-dir>/scripts/cfdctl.py --workspace <workspace> discover-cases --root <folder>
python <skill-dir>/scripts/cfdctl.py --workspace <workspace> batch-plan mesh <case-a> <case-b>
python <skill-dir>/scripts/cfdctl.py --workspace <workspace> batch-run solve <case-a> <case-b> --execute
```

Treat batch execution as sequential orchestration, not scheduling. Unless the
user explicitly requests another policy, preserve the supplied case order,
keep `max_concurrent_jobs` at 1, and start job N+1 only after job N succeeds.
Stop the batch on a failed job. Preflight all cases first and execute none when
any case is blocked or invalid. Never accept duplicate cases or a case outside
the workspace.

Use `optimize-mesh` without `--execute` to preview the applicable candidates and complete mesh workflow without changing the case. Read `references/mesh-optimization.md` before editing mesh dictionaries or evaluating an optimization result.

The controller resolves configured workflows against case files. A block-only
case runs `blockMesh` and the applicable `checkMesh`; a snappy case adds only the
registered feature and snappy steps whose dictionaries exist. Decomposition,
parallel flags, reconstruction, and processor cleanup are included only for a
parallel case. Inspect `skipped_steps` in the plan instead of treating an absent
optional dictionary as an error.

## Stage tutorial resources from Allrun guidance

When required geometry or initialization data is missing and an `Allrun` or
`Allrun.*` file exists, inspect only its setup and copy lines as reference.
Never execute or source the Allrun script. Translate a necessary copy into a
fixed `resource_copies` entry in a structurally matched `case_rule`, then add
`internal.stageResources` before the consuming mesh utility. Read
`references/provider-and-command-registry.md` before adding such an entry.

Preview the resource plan first. Report the Allrun reference, provider
environment variable, fixed source suffix, case-relative destination, and
whether the destination is missing. Copy only a missing destination during an
explicitly approved workflow execution. Do not interpret variables other than
the allowlisted provider environment name, shell operators, loops, command
substitution, downloads, deletion, or arbitrary commands from Allrun. If the
copy cannot be represented as a fixed provider resource, keep the workflow
blocked and ask the user to place the file inside the case.

If a registered `FOAM_TUTORIALS` copy enables
`provider_tutorial_fallback`, try only `$FOAM_TUTORIALS`,
`$WM_PROJECT_DIR/tutorials`, and the tutorials directory derived from the
configured provider bashrc. Report the attempted roots when the resource is
not installed; do not broaden the search.

When inspecting, explaining, or proposing changes to `system/snappyHexMeshDict`,
also read `references/snappyhexmesh-dict.md`. Use it to map symptoms to the
responsible parameter family and to preserve the staged castellated, snap, and
layer workflow. Confirm keywords against the installed CFD provider/version
before adding a setting that is absent from the case.

## Analyze STL geometry first

When `constant/triSurface` contains STL files, load `stl-geometry-analyzer` and
generate `.cfd-runs/geometry/stl-analysis.json` before ranking mesh candidates.
Use bounds, connected components, named regions, and opposed-clearance guidance
only to bound or rank entries already allowlisted in `.cfd-agent.json`. Treat
clearance as an STL-based estimate and report unit or feasibility conflicts.

## Optimize the mesh

Require explicit permission to start the iterative CFD run, then invoke:

```powershell
python <skill-dir>/scripts/cfdctl.py --workspace <workspace> --case <case-dir> optimize-mesh --execute
```

Use the configured candidate sets, bounds, maximum iterations, no-improvement limit, maximum cell count, and allowed check failures. Keep each candidate independent by restoring the baseline dictionaries before applying it. Leave an accepted candidate applied; restore the baseline when candidates are exhausted, execution fails, or the run is interrupted.

Inspect or resume a run with:

```powershell
python <skill-dir>/scripts/cfdctl.py --workspace <workspace> --case <case-dir> mesh-optimization-status --run-id <run-id>
python <skill-dir>/scripts/cfdctl.py --workspace <workspace> --case <case-dir> optimize-mesh --resume <run-id> --execute
```

Use `recover-mesh-optimization <run-id>` only when the user requests recovery of the original dictionaries. Report the accepted iteration, best iteration, stop reason, and `.cfd-runs/mesh/<run-id>` path.

## Run registered operations

Use `run-command <command-id>` for a single utility and `run-workflow <workflow-id>` for a configured sequence. Both only print a plan unless `--execute` is present.

```powershell
python <skill-dir>/scripts/cfdctl.py --workspace <workspace> --case <case-dir> run-command openfoam.checkMesh
python <skill-dir>/scripts/cfdctl.py --workspace <workspace> --case <case-dir> run-workflow mesh
```

Before adding a CFD provider or command, read `references/provider-and-command-registry.md`. Add only fixed executable names and arguments; do not register user-provided shell fragments.

## Recalculate with the existing mesh

Preview the complete cleanup and solve sequence before requesting execution:

```powershell
python <skill-dir>/scripts/cfdctl.py --workspace <workspace> --case <case-dir> plan rerun
python <skill-dir>/scripts/cfdctl.py --workspace <workspace> --case <case-dir> run-workflow rerun --execute
```

For a case with only `0`, preserve `0`. When fields such as
`0/alpha.water.orig` or `0/alpha.water.org` are templates, preserve the
template and remove only its generated counterpart before initialization.
For a case with exactly one of `0.orig`
or `0.org`, preserve the template and replace `0` from it. Reject a case that
contains both templates. Remove previous non-zero numeric time directories,
processor or processors directories, post-processing output, and old root
logs. Preserve `constant/polyMesh`, `system`, the template, and `.cfd-runs`.
Record the cleanup manifest under the workflow log directory.

Resolve the solver from `controlDict` in provider-compatible order:
`application` directly, Foundation `solver` through `foamRun`, and Foundation
`regionSolvers` through `foamMultiRun`. Select OpenFOAM or iconCFD command IDs
through the configured `solve` and `rerun` workflows; do not guess a provider.

## Generate solver-only Allrun

Generate rather than hand-write the solver workflow:

```powershell
python <skill-dir>/scripts/cfdctl.py --workspace <workspace> --case <case-dir> export-allrun --workflow solve --output Allrun.solve
```

Inspect the generated file and validate it statically before handing it to an external scheduler. It must not contain mesh-generation commands.
