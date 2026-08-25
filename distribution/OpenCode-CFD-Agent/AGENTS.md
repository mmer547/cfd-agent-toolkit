# CFD case agent rules

This repository is the distributable agent package. Copy its distributed files
once into the calculation workspace root. CFD case directories live anywhere
below that root and contain `constant`, `system`, and one of `0`, `0.orig`, or
`0.org`. Do not copy the agent package into each case.

The primary topology for this package is a Windows-native agent host (Codex or
OpenCode) plus Ubuntu WSL for CFD execution. The agent runs in
PowerShell/Windows, invokes the controller with Windows Python, and only the
controller may cross into WSL.

## Data boundary

- Read, search, create, and edit files under the workspace root without requesting
  permission when the operation is necessary for the user's task. Use the
  built-in read, edit, glob, grep, and list tools for routine file operations;
  do not replace them with arbitrary shell commands.
- Never read or write a path outside the workspace root. Do not copy, upload, paste,
  summarize for transmission, or otherwise send case data to an external
  directory, website, search engine, network service, or unregistered command.
- Do not use `webfetch` or `websearch`. If an external reference or missing case
  file is required, stop and ask the user to place it inside the case root.
- Keep execution controls separate from file access: real CFD runs still
  require explicit user intent and the configured execution approval.

## Required workflow

- Select the progress language in this order: the user's explicit language
  request, the language used by the user in the current conversation, the
  `host_locale` returned by controller `inspect`, then English. Keep that
  language until the user requests a change. Before every controller
  invocation, state what is being checked or executed, why it is needed, and
  whether it is a dry-run or a real CFD run. After it returns, summarize the
  result and state the next action.
- Do not remain silent while deciding among multiple steps. At natural
  boundaries, report the current phase: case inspection, workflow resolution,
  execution approval, utility execution, quality evaluation, setting change,
  or completion. Before a potentially long CFD call, name the workflow or
  utility and say that detailed output is being written to `.cfd-runs/`.
- When blocked, waiting for permission, or stopping after an error, immediately
  explain what completed, what failed or is awaiting approval, and what input
  is needed. Do not claim that work is still running after a controller process
  has returned.
- Load the `cfd-case-agent` skill before inspecting, changing, or executing CFD workflows.
- Load `stl-geometry-analyzer` before using STL dimensions or gaps to plan mesh settings.
- Use `.agents/skills/cfd-case-agent/scripts/cfdctl.py`; do not compose or run OpenFOAM/iconCFD utilities directly.
- Treat `.agents/skills/cfd-case-agent/registry/commands.json` as the command allowlist.
- Use `python` because the agent host runs on Windows. Do not start a second
  Codex or OpenCode process in WSL.
- Do not invoke `wsl.exe` directly; the controller selects the configured distribution and performs path conversion.
- Treat `Allrun` and `Allrun.*` as read-only setup references. Never execute or
  source them. When a required tutorial file is missing, use only a fixed
  `resource_copies` entry selected by a structural case rule and execute it
  through `internal.stageResources`. Preview and report the copy first; copy
  missing destinations only. Do not translate arbitrary Allrun shell logic.
- Do not search for OpenFOAM installations or alternative `bashrc` files during a workflow. Use only the provider values in `.cfd-agent.json`; if provider initialization fails, stop and report the configured distribution, configured `bashrc`, and controller log.
- Run the controller from the workspace root. Always pass
  `--workspace . --case <case-path>` for a single case; never infer the case
  from the current directory when multiple cases exist.
- Dry-run is the default. Add `--execute` only when the user explicitly requests a real CFD run.
- Read parallelism from `system/decomposeParDict`; never choose it from CPU count.
- Before analyzing or optimizing mesh settings, require the user to provide a
  positive maximum cell count. If it is missing, ask and stop before invoking
  the controller. Pass the answer with `--max-cells`; do not store or infer a
  default limit in `.cfd-agent.json`.
- Never make `checkMesh` pass by silently weakening `system/meshQualityDict`.
- Preserve and report optimization history under `.cfd-runs/mesh/`.
- Before recalculation, plan the configured `rerun` workflow. Preserve `0` when
  it is the only initial-condition directory. For field templates inside it,
  such as `0/alpha.water.orig`, preserve the template and remove only an
  existing generated counterpart before the initialization utility runs.
  When exactly one of `0.orig` or
  `0.org` exists, preserve that template, replace `0` from it, and remove only
  prior solution times, decomposition directories, post-processing outputs,
  and old root logs. Never delete `constant/polyMesh`. If both templates exist,
  stop as ambiguous without deleting anything.
- For multiple cases, use controller `batch-plan` before `batch-run`. Batch
  execution defaults to one case-level job at a time in the supplied order and
  must preflight every case before starting any. Start the next job only after
  the previous job succeeds; stop after a failed job. Each job may still use
  the MPI count in its own `decomposeParDict`. Do not start concurrent cases or
  implement job scheduling unless the user explicitly requests a separately
  supported execution policy.

## First checks

For a new session, run `discover-cases`, then run the controller's `inspect`,
`list-commands`, and relevant `plan` command for each selected case before
proposing execution. For mesh work, preview with
`optimize-mesh --max-cells <user-limit>` without `--execute` first. If STL files exist, generate and
review `.cfd-runs/geometry/stl-analysis.json` before that preview.

## Scope of user-editable case inputs

The normal user workflow changes fields such as `0/U`,
`constant/transportProperties`, and `constant/MRFProperties`. Mesh optimization
may modify only the paths and dictionary entries allowlisted in `.cfd-agent.json`.
