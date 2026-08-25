#!/usr/bin/env python3
"""Deterministic controller for case-local CFD workflows.

The controller is dry-run by default. CFD processes are started only when an
execution subcommand is given the explicit --execute flag. On Windows, tools
run through the configured WSL distribution. On Linux/WSL, tools run directly.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import locale
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from mesh_optimizer import MeshOptimizer, OptimizationError, metric_constraints


SKILL_DIR = Path(__file__).resolve().parents[1]
REGISTRY_PATH = SKILL_DIR / "registry" / "commands.json"
PACKAGE_ROOT = SCRIPT_DIR.parents[3]


class CfdError(RuntimeError):
    pass


def report_progress(message: str) -> None:
    """Emit progress without corrupting JSON written to stdout."""
    print(f"[cfdctl] {message}", file=sys.stderr, flush=True)


def host_locale() -> str:
    """Return the host user locale without launching another process."""
    try:
        language, _ = locale.getlocale()
    except (TypeError, ValueError):
        language = None
    return language or "unknown"


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CfdError(f"required file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CfdError(f"invalid JSON in {path}: {exc}") from exc


def dump_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def strip_foam_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//.*?$", "", text, flags=re.MULTILINE)


def foam_entry(path: Path, key: str) -> str:
    text = strip_foam_comments(path.read_text(encoding="utf-8", errors="replace"))
    match = re.search(rf"(?m)^\s*{re.escape(key)}\s+([^;]+);", text)
    if not match:
        raise CfdError(f"entry {key!r} not found in {path}")
    return match.group(1).strip()


def optional_foam_entry(path: Path, key: str) -> str | None:
    text = strip_foam_comments(path.read_text(encoding="utf-8", errors="replace"))
    match = re.search(rf"(?m)^\s*{re.escape(key)}\s+([^;]+);", text)
    return match.group(1).strip() if match else None


def processor_count(case: Path) -> int:
    raw = foam_entry(case / "system" / "decomposeParDict", "numberOfSubdomains")
    if not re.fullmatch(r"[1-9][0-9]*", raw):
        raise CfdError(f"numberOfSubdomains must be a positive integer, found {raw!r}")
    return int(raw)


def parallel_settings(case: Path) -> tuple[bool, int | None]:
    dictionary = case / "system" / "decomposeParDict"
    if not dictionary.is_file():
        return False, None
    count = processor_count(case)
    return count > 1, count


def application_name(case: Path) -> str:
    control_dict = case / "system" / "controlDict"
    name = optional_foam_entry(control_dict, "application")
    if name is None and optional_foam_entry(control_dict, "solver") is not None:
        name = "foamRun"
    if name is None and optional_foam_entry(control_dict, "regionSolvers") is not None:
        name = "foamMultiRun"
    if name is None:
        raise CfdError(
            f"controlDict has none of application, solver, or regionSolvers: {control_dict}"
        )
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.+-]*", name):
        raise CfdError(f"unsafe application name in controlDict: {name!r}")
    return name


def validate_case(case: Path) -> None:
    missing = [name for name in ("constant", "system") if not (case / name).is_dir()]
    if not any((case / name).is_dir() for name in ("0", "0.orig", "0.org")):
        missing.append("0 or 0.orig or 0.org")
    if missing:
        raise CfdError(f"not a CFD case root; missing directories: {', '.join(missing)}")


def case_identity(case: Path) -> dict[str, str]:
    control_dict = case / "system" / "controlDict"
    text = control_dict.read_text(encoding="utf-8", errors="replace")
    lowered = text.lower()
    if "iconcfd" in lowered:
        vendor = "iconcfd"
    elif "openfoam.com" in lowered:
        vendor = "openfoam-com"
    elif "openfoam.org" in lowered:
        vendor = "foundation"
    else:
        vendor = "unknown"
    match = re.search(r"(?im)\bVersion:\s*([^\s|]+)", text)
    version = match.group(1) if match else "unknown"
    return {"vendor": vendor, "version": version}


def case_matches_structure(case: Path, match: dict[str, Any]) -> bool:
    identity = case_identity(case)
    if "vendor" in match and identity["vendor"] != str(match["vendor"]):
        return False
    if "version" in match and identity["version"] != str(match["version"]):
        return False
    if "application" in match and application_name(case) != str(match["application"]):
        return False
    if any(not (case / relative).is_file() for relative in match.get("files_all", [])):
        return False
    files_any = match.get("files_any", [])
    if files_any and not any((case / relative).is_file() for relative in files_any):
        return False
    if any((case / relative).exists() for relative in match.get("files_absent", [])):
        return False
    if any(not (case / relative).is_dir() for relative in match.get("dirs_all", [])):
        return False
    return True


def merge_case_override(base: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if key == "command_values":
            base.setdefault(key, {}).update(copy.deepcopy(value))
        elif key == "depends_on":
            base[key] = list(dict.fromkeys([*base.get(key, []), *copy.deepcopy(value)]))
        else:
            base[key] = copy.deepcopy(value)


def path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def is_time_directory(path: Path) -> bool:
    if not path.is_dir() or path.name == "0":
        return False
    try:
        value = Decimal(path.name)
    except InvalidOperation:
        return False
    return value.is_finite() and value != 0


def is_processor_directory(path: Path) -> bool:
    return path.is_dir() and bool(
        re.fullmatch(r"processor[0-9]+|processors[0-9]+(?:_[0-9]+-[0-9]+)?", path.name)
    )


def rerun_cleanup_plan(case: Path) -> dict[str, Any]:
    templates = [name for name in ("0.orig", "0.org") if (case / name).is_dir()]
    if len(templates) > 1:
        raise CfdError("both 0.orig and 0.org exist; keep only the intended initial-condition template")
    zero = case / "0"
    if zero.is_symlink() or (zero.exists() and not zero.is_dir()):
        raise CfdError("0 must be a real directory, not a file or symbolic link")
    if templates:
        template_path = case / templates[0]
        if template_path.is_symlink():
            raise CfdError(f"{templates[0]} must be a real directory, not a symbolic link")
        initial_action = {
            "mode": "restore-template",
            "source": templates[0],
            "destination": "0",
        }
    elif zero.is_dir():
        initial_action = {"mode": "preserve-zero", "source": "0", "destination": "0"}
    else:
        raise CfdError("none of 0, 0.orig, or 0.org is available for initial conditions")

    targets: list[str] = []
    for path in case.iterdir():
        if is_time_directory(path) or is_processor_directory(path):
            targets.append(path.name)
        elif path.is_dir() and path.name in {"postProcessing", "VTK", "logs"}:
            targets.append(path.name)
        elif path.is_file() and (path.name == "log" or path.name.startswith("log.")):
            targets.append(path.name)
    if initial_action["mode"] == "preserve-zero":
        generated_from_templates: dict[str, str] = {}
        for suffix in (".orig", ".org"):
            for template in zero.rglob(f"*{suffix}"):
                if not template.is_file():
                    continue
                generated = template.with_name(template.name[: -len(suffix)])
                relative = generated.relative_to(case).as_posix()
                previous = generated_from_templates.get(relative)
                if previous and previous != suffix:
                    raise CfdError(
                        f"both .orig and .org field templates exist for {relative}; keep only one"
                    )
                generated_from_templates[relative] = suffix
                if generated.is_file() or generated.is_symlink():
                    targets.append(relative)
                compressed = generated.with_name(generated.name + ".gz")
                if compressed.is_file() or compressed.is_symlink():
                    targets.append(compressed.relative_to(case).as_posix())
    if initial_action["mode"] == "restore-template" and zero.is_dir():
        targets.append("0")
    return {
        "initial_conditions": initial_action,
        "remove": sorted(set(targets)),
        "preserve": ["constant/polyMesh", "system", templates[0] if templates else "0", ".cfd-runs"],
    }


def initial_conditions_plan(case: Path) -> dict[str, str]:
    templates = [name for name in ("0.orig", "0.org") if (case / name).is_dir()]
    if len(templates) > 1:
        raise CfdError("both 0.orig and 0.org exist; keep only the intended initial-condition template")
    zero = case / "0"
    if zero.is_symlink() or (zero.exists() and not zero.is_dir()):
        raise CfdError("0 must be a real directory, not a file or symbolic link")
    if zero.is_dir():
        return {"action": "preserve-zero", "source": "0", "destination": "0"}
    if templates:
        source = case / templates[0]
        if source.is_symlink():
            raise CfdError(f"{templates[0]} must be a real directory, not a symbolic link")
        return {"action": "copy-template", "source": templates[0], "destination": "0"}
    raise CfdError("none of 0, 0.orig, or 0.org is available for initial conditions")


def mesh_metrics(log_path: Path) -> dict[str, Any]:
    text = log_path.read_text(encoding="utf-8", errors="replace")

    def integer(pattern: str) -> int | None:
        match = re.search(pattern, text, flags=re.MULTILINE | re.IGNORECASE)
        return int(match.group(1)) if match else None

    def number(pattern: str) -> float | None:
        match = re.search(pattern, text, flags=re.MULTILINE | re.IGNORECASE)
        return float(match.group(1)) if match else None

    mesh_ok = "Mesh OK." in text
    failed_checks = integer(r"Failed\s+([0-9]+)\s+mesh checks")
    if failed_checks is None and mesh_ok:
        failed_checks = 0
    return {
        "log": str(log_path.resolve()),
        "cells": integer(r"^\s*cells:\s*([0-9]+)"),
        "failed_checks": failed_checks,
        "mesh_ok": mesh_ok,
        "max_non_orthogonality": number(r"Mesh non-orthogonality Max:\s*([0-9.eE+-]+)"),
        "low_quality_face_tets": integer(r"([0-9]+)\s+faces with low quality or negative volume decomposition tets"),
        "concave_cells": integer(r"Concave cells .*?number of cells:\s*([0-9]+)"),
        "faces_over_non_ortho_limit": integer(r"non-orthogonality\s*>.*?:\s*([0-9]+)"),
        "faces_below_twist_limit": integer(r"faces with face twist\s*<.*?:\s*([0-9]+)"),
    }


class Controller:
    def __init__(self, case: Path, config_path: Path | None = None, workspace_root: Path | None = None):
        self.case = case.resolve()
        validate_case(self.case)
        self.workspace_root = (workspace_root or Path.cwd()).resolve()
        if config_path is None:
            candidates = [
                self.case / ".cfd-agent.json",
                self.workspace_root / ".cfd-agent.json",
                PACKAGE_ROOT / ".cfd-agent.json",
            ]
            config_path = next((path for path in candidates if path.is_file()), candidates[-1])
        self.config_path = config_path.resolve()
        self.config = copy.deepcopy(load_json(self.config_path))
        self.commands = load_json(REGISTRY_PATH)["commands"]
        self.identity = case_identity(self.case)
        self.profile_id, self.profile, self.case_override = self._select_profile()
        workflow_set = self.profile.get("workflow_set")
        if workflow_set:
            try:
                self.config["workflows"] = copy.deepcopy(
                    self.config["workflow_sets"][workflow_set]
                )
            except KeyError as exc:
                raise CfdError(
                    f"profile {self.profile_id!r} selects unknown workflow_set: {workflow_set}"
                ) from exc
        elif self.profile.get("workflows"):
            self.config["workflows"] = copy.deepcopy(self.profile["workflows"])
        if self.case_override.get("workflows"):
            self.config["workflows"] = copy.deepcopy(self.case_override["workflows"])
        self.provider_name = self.profile.get("provider")
        if self.provider_name is None:
            providers = list(self.config.get("providers", {}))
            if len(providers) != 1:
                raise CfdError(
                    "case provider is ambiguous; configure a matching profile or default_profile"
                )
            self.provider_name = providers[0]
        if self.provider_name not in self.config.get("providers", {}):
            raise CfdError(
                f"profile {self.profile_id!r} selects unconfigured provider: {self.provider_name}"
            )
        self.decompose_dict = str(
            self.case_override.get(
                "decompose_par_dict",
                self.profile.get("decompose_par_dict", "system/decomposeParDict"),
            )
        ).replace("\\", "/")
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", self.decompose_dict) or self.decompose_dict.startswith("/") or ".." in Path(self.decompose_dict).parts:
            raise CfdError(f"unsafe decomposePar dictionary path: {self.decompose_dict!r}")

    def _case_key(self) -> str | None:
        try:
            return self.case.relative_to(self.workspace_root).as_posix()
        except ValueError:
            return None

    def _select_profile(self) -> tuple[str, dict[str, Any], dict[str, Any]]:
        key = self._case_key()
        overrides = self.config.get("case_overrides", {})
        override: dict[str, Any] = {}
        self.matched_case_rules: list[str] = []
        for rule in self.config.get("case_rules", []):
            if not case_matches_structure(self.case, rule.get("match", {})):
                continue
            rule_id = str(rule.get("id", "unnamed-rule"))
            self.matched_case_rules.append(rule_id)
            merge_case_override(override, rule.get("apply", {}))
            sibling = rule.get("discover_sibling")
            if sibling:
                candidates: list[Path] = []
                for candidate in self.case.parent.iterdir():
                    if candidate == self.case or not candidate.is_dir():
                        continue
                    try:
                        validate_case(candidate)
                        matches = case_matches_structure(candidate, sibling.get("match", {}))
                    except (CfdError, OSError):
                        continue
                    if matches:
                        candidates.append(candidate.resolve())
                if len(candidates) != 1:
                    raise CfdError(
                        f"case rule {rule_id!r} expected exactly one matching sibling case; "
                        f"found {len(candidates)}"
                    )
                source = candidates[0]
                value_name = str(sibling["command_value"])
                relative_source = os.path.relpath(source, self.case).replace("\\", "/")
                override.setdefault("command_values", {})[value_name] = relative_source
                dependency = source.relative_to(self.workspace_root).as_posix()
                override["depends_on"] = list(
                    dict.fromkeys([*override.get("depends_on", []), dependency])
                )
        if key:
            merge_case_override(override, overrides.get(key, {}))
        profiles = self.config.get("profiles", {})
        explicit = override.get("profile")
        if explicit:
            if explicit not in profiles:
                raise CfdError(f"case override selects unknown profile: {explicit}")
            return explicit, copy.deepcopy(profiles[explicit]), override
        for profile_id, profile in profiles.items():
            expected = profile.get("match", {})
            if expected and all(str(self.identity.get(name)) == str(value) for name, value in expected.items()):
                return profile_id, copy.deepcopy(profile), override
        default = self.config.get("default_profile")
        if default:
            if default not in profiles:
                raise CfdError(f"default_profile is not configured: {default}")
            return default, copy.deepcopy(profiles[default]), override
        return "legacy", {}, override

    def parallel_settings(self) -> tuple[bool, int | None]:
        dictionary = self.case / self.decompose_dict
        if not dictionary.is_file():
            return False, None
        raw = foam_entry(dictionary, "numberOfSubdomains")
        if not re.fullmatch(r"[1-9][0-9]*", raw):
            raise CfdError(f"numberOfSubdomains must be a positive integer, found {raw!r}")
        count = int(raw)
        return count > 1, count

    def command(self, command_id: str) -> dict[str, Any]:
        try:
            return self.commands[command_id]
        except KeyError as exc:
            raise CfdError(f"command is not registered: {command_id}") from exc

    def resource_copy_plan(self) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        for raw in self.case_override.get("resource_copies", []):
            source_env = str(raw.get("source_env", ""))
            source = str(raw.get("source", "")).replace("\\", "/")
            destination = str(raw.get("destination", "")).replace("\\", "/")
            reference = str(raw.get("reference", ""))
            provider_tutorial_fallback = raw.get("provider_tutorial_fallback", False)
            if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", source_env):
                raise CfdError(f"unsafe resource source environment variable: {source_env!r}")
            for label, value in (("source", source), ("destination", destination)):
                parts = Path(value).parts
                if (
                    not value
                    or value.startswith("/")
                    or re.match(r"^[A-Za-z]:", value)
                    or ".." in parts
                    or any(char in value for char in "\n\r\0")
                ):
                    raise CfdError(f"unsafe resource {label}: {value!r}")
            target = (self.case / destination).resolve()
            if not path_within(target, self.case):
                raise CfdError(f"resource destination resolves outside case: {destination!r}")
            if reference and not re.fullmatch(r"Allrun(?:\.[A-Za-z0-9_-]+)?", reference):
                raise CfdError(f"unsafe Allrun reference name: {reference!r}")
            if reference and not (self.case / reference).is_file():
                raise CfdError(f"configured Allrun reference is missing from case: {reference}")
            if not isinstance(provider_tutorial_fallback, bool):
                raise CfdError("provider_tutorial_fallback must be true or false")
            if provider_tutorial_fallback and source_env != "FOAM_TUTORIALS":
                raise CfdError(
                    "provider_tutorial_fallback is allowed only for FOAM_TUTORIALS"
                )
            entries.append(
                {
                    "source_env": source_env,
                    "source": source,
                    "source_display": f"${source_env}/{source}",
                    "destination": destination,
                    "reference": reference or None,
                    "provider_tutorial_fallback": provider_tutorial_fallback,
                    "needed": not target.is_file(),
                }
            )
        return {
            "policy": "copy-missing-only",
            "allrun_is_reference_only": True,
            "copies": entries,
        }

    def _planned_resource_destinations(self) -> list[Path]:
        return [
            (self.case / entry["destination"]).resolve()
            for entry in self.resource_copy_plan()["copies"]
            if entry["needed"]
        ]

    def resolved_command(self, command_id: str) -> dict[str, Any]:
        spec = dict(self.command(command_id))
        if spec.get("kind") == "internal":
            return spec
        if spec.get("provider") == "{case_provider}":
            spec["provider"] = self.provider_name
        executable = spec["executable"]
        if executable == "{application}":
            executable = application_name(self.case)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.+-]*", executable):
            raise CfdError(f"unsafe executable in registry: {executable!r}")
        args = list(spec.get("args", []))
        values = self.case_override.get("command_values", {})
        for index, arg in enumerate(args):
            match = re.fullmatch(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", arg)
            if not match:
                continue
            name = match.group(1)
            if name not in values:
                raise CfdError(f"case override must configure command_values.{name}")
            value = str(values[name]).replace("\\", "/")
            if not value or value.startswith("/") or any(char in value for char in "\n\r\0"):
                raise CfdError(f"unsafe command value for {name}: {value!r}")
            target = (self.case / value).resolve()
            if not path_within(target, self.workspace_root):
                raise CfdError(f"command value {name} resolves outside workspace: {value!r}")
            args[index] = value
        if spec.get("uses_decompose_dict") and self.decompose_dict != "system/decomposeParDict":
            option = self.profile.get("decompose_option")
            if not option:
                raise CfdError(
                    f"profile {self.profile_id!r} must configure decompose_option for {self.decompose_dict}"
                )
            args.extend([option, self.decompose_dict])
        for arg in args:
            if not isinstance(arg, str) or any(char in arg for char in "\n\r\0"):
                raise CfdError(f"unsafe argument in registry for {command_id}")
        spec["args"] = args
        spec["executable"] = executable
        return spec

    def command_applicability(self, command_id: str) -> tuple[bool, str | None]:
        spec = self.command(command_id)
        parallel, _ = self.parallel_settings()
        if spec.get("operation") == "stage-resources":
            if not any(entry["needed"] for entry in self.resource_copy_plan()["copies"]):
                return False, "all registered tutorial resources already exist in the case"
        if spec.get("when_parallel") and not parallel:
            return False, "parallel execution is not enabled by system/decomposeParDict"
        for relative in spec.get("requires", []):
            if not (self.case / relative).is_file():
                return False, f"required case file is missing: {relative}"
        for relative in spec.get("unless", []):
            if (self.case / relative).is_file():
                return False, f"superseded because case file exists: {relative}"
        for relative in spec.get("requires_dirs", []):
            required_dir = (self.case / relative).resolve()
            will_create = any(
                path_within(destination, required_dir)
                for destination in self._planned_resource_destinations()
            )
            if not required_dir.is_dir() and not will_create:
                return False, f"required case directory is missing: {relative}"
        return True, None

    def workflow_resolution(self, workflow: str) -> tuple[list[str], list[dict[str, str]]]:
        try:
            configured = self.config["workflows"][workflow]
        except KeyError as exc:
            raise CfdError(f"workflow is not configured: {workflow}") from exc
        applicable: list[str] = []
        skipped: list[dict[str, str]] = []
        for command_id in configured:
            self.command(command_id)
            enabled, reason = self.command_applicability(command_id)
            if enabled:
                applicable.append(command_id)
            else:
                skipped.append({"command_id": command_id, "reason": reason or "not applicable"})
        if workflow == "mesh" and not any(self.command(item).get("phase") == "validate" for item in applicable):
            fallback = "openfoam.checkMesh.basic"
            if fallback not in configured and fallback in self.commands:
                enabled, _ = self.command_applicability(fallback)
                if enabled:
                    applicable.append(fallback)
        return applicable, skipped

    def workflow_ids(self, workflow: str) -> list[str]:
        return self.workflow_resolution(workflow)[0]

    def workflow_issues(self, workflow: str) -> list[str]:
        issues: list[str] = []
        if workflow == "mesh" and (self.case / "system" / "snappyHexMeshDict").is_file():
            surfaces = self.case / "constant" / "triSurface"
            staged_geometry = any(
                path_within(destination, surfaces)
                for destination in self._planned_resource_destinations()
            )
            if (
                (not surfaces.is_dir() or not any(path.is_file() for path in surfaces.rglob("*")))
                and not staged_geometry
            ):
                issues.append(
                    "snappyHexMeshDict exists but constant/triSurface contains no geometry; "
                    "place the required case geometry inside the case before execution"
                )
        if workflow in {"solve", "rerun"} and not (self.case / "constant" / "polyMesh").is_dir():
            issues.append(
                "constant/polyMesh is missing; complete the mesh workflow before calculation"
            )
        return issues

    def render(self, command_id: str, runtime_n: bool = False) -> str:
        applicable, reason = self.command_applicability(command_id)
        if not applicable:
            raise CfdError(f"command is not applicable to this case: {command_id}: {reason}")
        spec = self.resolved_command(command_id)
        if spec.get("kind") == "internal":
            return f"internal:{spec['operation']}"
        parts = [spec["executable"], *spec.get("args", [])]
        parallel, count = self.parallel_settings()
        if spec.get("parallel") and parallel:
            n = '"$N"' if runtime_n else str(count)
            parts.append("-parallel")
            return "mpirun -np " + n + " " + " ".join(shlex.quote(part) for part in parts)
        return " ".join(shlex.quote(part) for part in parts)

    def plan(self, workflow: str) -> dict[str, Any]:
        ids, skipped = self.workflow_resolution(workflow)
        parallel, count = self.parallel_settings()
        steps = []
        for command_id in ids:
            step: dict[str, Any] = {
                "command_id": command_id,
                "command": self.render(command_id),
            }
            if self.command(command_id).get("operation") == "prepare-rerun":
                step["details"] = rerun_cleanup_plan(self.case)
            elif self.command(command_id).get("operation") == "prepare-initial-conditions":
                step["details"] = initial_conditions_plan(self.case)
            elif self.command(command_id).get("operation") == "stage-resources":
                step["details"] = self.resource_copy_plan()
            steps.append(step)
        return {
            "case": str(self.case),
            "config": str(self.config_path),
            "identity": self.identity,
            "profile": self.profile_id,
            "provider": self.provider_name,
            "decompose_par_dict": self.decompose_dict,
            "workflow": workflow,
            "mode": "dry-run",
            "parallel": parallel,
            "number_of_subdomains": count,
            "steps": steps,
            "skipped_steps": skipped,
            "blocking_issues": self.workflow_issues(workflow),
            "dependencies": self.case_override.get("depends_on", []),
            "case_rules": self.matched_case_rules,
        }

    def _prepare_initial_conditions(self, log_dir: Path | None) -> Path:
        plan = initial_conditions_plan(self.case)
        log_dir = log_dir or self.case / ".cfd-runs" / "commands"
        log_dir.mkdir(parents=True, exist_ok=True)
        manifest = log_dir / "prepare-initial-conditions.json"
        if plan["action"] == "copy-template":
            shutil.copytree(self.case / plan["source"], self.case / "0")
        manifest.write_text(dump_json(plan) + "\n", encoding="utf-8")
        return manifest

    def _stage_resources(self, log_dir: Path | None) -> Path:
        plan = self.resource_copy_plan()
        pending = [entry for entry in plan["copies"] if entry["needed"]]
        log_dir = log_dir or self.case / ".cfd-runs" / "commands"
        log_dir.mkdir(parents=True, exist_ok=True)
        manifest = log_dir / "stage-resources.json"
        manifest.write_text(dump_json(plan) + "\n", encoding="utf-8")
        if not pending:
            return manifest

        provider = self.config["providers"][self.provider_name]
        bashrc_value = provider["bashrc"]
        commands = [
            "set -o pipefail",
            f"[[ -r {shlex.quote(bashrc_value)} ]] || {{ echo {shlex.quote(f'configured bashrc is not readable: {bashrc_value}')} >&2; exit 20; }}",
            f"source {shlex.quote(bashrc_value)} || {{ echo {shlex.quote(f'configured bashrc failed while sourcing: {bashrc_value}')} >&2; exit 21; }}",
        ]
        for entry in pending:
            env_name = entry["source_env"]
            source = shlex.quote(entry["source"])
            destination = shlex.quote(entry["destination"])
            destination_dir = shlex.quote(str(Path(entry["destination"]).parent).replace("\\", "/"))
            commands.append(f"resource_root=\"${{{env_name}:-}}\"")
            commands.append("resource_source=\"\"")
            commands.append(
                f"[[ -z \"$resource_root\" || ! -f \"$resource_root\"/{source} ]] || "
                f"resource_source=\"$resource_root\"/{source}"
            )
            provider_project = bashrc_value.rsplit("/", 2)[0]
            if entry["provider_tutorial_fallback"]:
                provider_tutorials = shlex.quote(f"{provider_project}/tutorials")
                commands.extend(
                    [
                        f"[[ -n \"$resource_source\" || -z \"${{WM_PROJECT_DIR:-}}\" || "
                        f"! -f \"$WM_PROJECT_DIR/tutorials\"/{source} ]] || "
                        f"resource_source=\"$WM_PROJECT_DIR/tutorials\"/{source}",
                        f"[[ -n \"$resource_source\" || ! -f {provider_tutorials}/{source} ]] || "
                        f"resource_source={provider_tutorials}/{source}",
                    ]
                )
            missing_message = (
                f"tutorial resource was not found: {entry['source_display']}; "
                f"also checked $WM_PROJECT_DIR/tutorials and {provider_project}/tutorials"
                if entry["provider_tutorial_fallback"]
                else f"tutorial resource was not found: {entry['source_display']}"
            )
            commands.extend(
                [
                    f"[[ -n \"$resource_source\" ]] || {{ echo {shlex.quote(missing_message)} >&2; exit 24; }}",
                    f"mkdir -p -- {destination_dir}",
                    f"[[ -f {destination} ]] || cp -f -- \"$resource_source\" {destination}",
                ]
            )
        shell_command = "; ".join(commands)
        invocation, cwd = self._provider_invocation(provider, shell_command)
        log_path = log_dir / "internal-stageResources.log"
        with log_path.open("w", encoding="utf-8", newline="\n") as log_stream:
            result = subprocess.run(
                invocation,
                cwd=cwd,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                check=False,
            )
        if result.returncode != 0:
            raise CfdError(
                f"tutorial resource staging failed with exit code {result.returncode}; log: {log_path}"
            )
        missing = [
            entry["destination"]
            for entry in pending
            if not (self.case / entry["destination"]).is_file()
        ]
        if missing:
            raise CfdError(f"resource staging reported success but files are missing: {', '.join(missing)}")
        return manifest

    def _prepare_rerun(self, log_dir: Path | None) -> Path:
        plan = rerun_cleanup_plan(self.case)
        log_dir = log_dir or self.case / ".cfd-runs" / "commands"
        log_dir.mkdir(parents=True, exist_ok=True)
        manifest = log_dir / "prepare-rerun.json"
        manifest.write_text(dump_json(plan) + "\n", encoding="utf-8")

        initial = plan["initial_conditions"]
        staged_zero: Path | None = None
        if initial["mode"] == "restore-template":
            staged_zero = self.case / f".cfd-zero-restore-{uuid.uuid4().hex}"
            shutil.copytree(self.case / initial["source"], staged_zero)
        try:
            if staged_zero is not None:
                zero = self.case / "0"
                if zero.is_dir():
                    shutil.rmtree(zero)
                staged_zero.rename(zero)
                staged_zero = None
            for relative in plan["remove"]:
                if relative == "0":
                    continue
                target = self.case / relative
                if target.is_symlink() or target.is_file():
                    target.unlink(missing_ok=True)
                elif target.is_dir():
                    shutil.rmtree(target)
        finally:
            if staged_zero is not None and staged_zero.exists():
                shutil.rmtree(staged_zero)
        return manifest

    def _linux_case(self, distribution: str) -> str:
        # wsl.exe forwards backslashes to the Linux command where they can be
        # consumed as escape characters (for example C:\work -> C:work).
        windows_case = str(self.case).replace("\\", "/")
        result = subprocess.run(
            ["wsl.exe", "-d", distribution, "--", "wslpath", "-a", windows_case],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "no diagnostic output").strip()
            raise CfdError(
                "WSL path conversion failed for distribution "
                f"{distribution!r} and case {str(self.case)!r} "
                f"(exit {result.returncode}): {detail}"
            )
        linux_case = result.stdout.strip()
        if not linux_case:
            raise CfdError(
                "WSL path conversion returned an empty path for distribution "
                f"{distribution!r} and case {str(self.case)!r}"
            )
        return linux_case

    def _provider_invocation(self, provider: dict[str, Any], shell_command: str) -> tuple[list[str], Path | None]:
        """Return the host command and cwd for one provider invocation."""
        if sys.platform == "win32":
            distribution = provider["distribution"]
            linux_case = self._linux_case(distribution)
            return (
                ["wsl.exe", "-d", distribution, "--cd", linux_case, "--", "bash", "-lc", shell_command],
                None,
            )
        return ["bash", "-lc", shell_command], self.case

    def execute_command(self, command_id: str, log_dir: Path | None = None) -> Path | None:
        spec = self.resolved_command(command_id)
        if spec.get("kind") == "internal":
            report_progress(f"START {command_id}")
            if spec["operation"] == "prepare-rerun":
                try:
                    manifest = self._prepare_rerun(log_dir)
                except Exception as exc:
                    report_progress(f"FAILED {command_id}; reason={exc}")
                    raise
                report_progress(f"DONE {command_id}; manifest={manifest}")
                return manifest
            if spec["operation"] == "prepare-initial-conditions":
                try:
                    manifest = self._prepare_initial_conditions(log_dir)
                except Exception as exc:
                    report_progress(f"FAILED {command_id}; reason={exc}")
                    raise
                report_progress(f"DONE {command_id}; manifest={manifest}")
                return manifest
            if spec["operation"] == "stage-resources":
                try:
                    manifest = self._stage_resources(log_dir)
                except Exception as exc:
                    report_progress(f"FAILED {command_id}; reason={exc}")
                    raise
                report_progress(f"DONE {command_id}; manifest={manifest}")
                return manifest
            if spec["operation"] == "remove-processors":
                for path in self.case.glob("processor[0-9]*"):
                    if path.is_dir() and re.fullmatch(r"processor[0-9]+", path.name):
                        shutil.rmtree(path)
                report_progress(f"DONE {command_id}")
                return None
            raise CfdError(f"unsupported internal operation: {spec['operation']}")

        provider_name = spec["provider"]
        try:
            provider = self.config["providers"][provider_name]
        except KeyError as exc:
            raise CfdError(f"provider is not configured: {provider_name}") from exc
        bashrc_value = provider["bashrc"]
        bashrc = shlex.quote(bashrc_value)
        executable = shlex.quote(spec["executable"])
        missing_bashrc = shlex.quote(f"configured bashrc is not readable: {bashrc_value}")
        source_failed = shlex.quote(f"configured bashrc failed while sourcing: {bashrc_value}")
        missing_executable = shlex.quote(f"registered executable was not found after sourcing bashrc: {spec['executable']}")
        shell_command = (
            f"set -o pipefail; "
            f"[[ -r {bashrc} ]] || {{ echo {missing_bashrc} >&2; exit 20; }}; "
            f"source {bashrc} || {{ echo {source_failed} >&2; exit 21; }}; "
            f"command -v {executable} >/dev/null 2>&1 || {{ echo {missing_executable} >&2; exit 22; }}; "
            f"exec {self.render(command_id)}"
        )
        invocation, cwd = self._provider_invocation(provider, shell_command)
        log_dir = log_dir or self.case / ".cfd-runs" / "commands"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{command_id.replace('.', '-')}.log"
        report_progress(f"START {command_id}; log={log_path}")
        with log_path.open("w", encoding="utf-8", newline="\n") as log_stream:
            result = subprocess.run(
                invocation,
                cwd=cwd,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                text=True,
            )
        if result.returncode:
            report_progress(f"FAILED {command_id}; exit={result.returncode}; log={log_path}")
            raise CfdError(f"{command_id} failed with exit code {result.returncode}; see {log_path}")
        report_progress(f"DONE {command_id}; log={log_path}")
        return log_path

    def execute_workflow(self, workflow: str, log_dir: Path) -> Path:
        issues = self.workflow_issues(workflow)
        if issues:
            raise CfdError("workflow preflight failed: " + "; ".join(issues))
        check_log = None
        processors_cleaned = False
        ids = self.workflow_ids(workflow)
        if workflow == "mesh" and not any(self.command(item).get("phase") == "mesh" for item in ids):
            raise CfdError("no registered mesh-generation command is applicable to this case")
        parallel, _ = self.parallel_settings()
        try:
            for command_id in ids:
                command_log = self.execute_command(command_id, log_dir)
                if command_id == "internal.removeProcessors":
                    processors_cleaned = True
                if self.command(command_id).get("phase") == "validate" and command_log is not None:
                    check_log = command_log
        finally:
            if workflow == "mesh" and parallel and not processors_cleaned:
                self.execute_command("internal.removeProcessors", log_dir)
        if workflow == "mesh" and check_log is None:
            raise CfdError("mesh workflow did not produce a validation log")
        return check_log or log_dir

    def mesh_optimizer(self, max_cells: int | None = None) -> MeshOptimizer:
        return MeshOptimizer(
            self.case,
            self.config,
            mesh_metrics,
            lambda: self.plan("mesh"),
            lambda log_dir: self.execute_workflow("mesh", log_dir),
            max_cells=max_cells,
        )

    def export_allrun(self, workflow: str, output: Path, force: bool) -> Path:
        ids = self.workflow_ids(workflow)
        if any(self.command(item).get("phase") == "mesh" for item in ids):
            raise CfdError("solver-only Allrun cannot contain mesh-phase commands")
        if output.exists() and not force:
            raise CfdError(f"output exists; use --force to replace it: {output}")

        lines = [
            "#!/usr/bin/env bash",
            "set -Eeuo pipefail",
            "",
            'case_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"',
            'cd "$case_dir"',
            "",
        ]
        first_external = next(
            self.resolved_command(command_id)
            for command_id in ids
            if self.resolved_command(command_id).get("kind") != "internal"
        )
        active_provider = first_external["provider"]
        initial_provider = self.config["providers"][active_provider]
        lines.extend([
            f"# Provider: {active_provider}",
            f"source {shlex.quote(initial_provider['bashrc'])}",
            "",
        ])
        parallel, _ = self.parallel_settings()
        if parallel and any(self.resolved_command(item).get("parallel") for item in ids):
            lines.extend([
                f"N=\"$(foamDictionary {shlex.quote(self.decompose_dict)} -entry numberOfSubdomains -value)\"",
                '[[ "$N" =~ ^([2-9]|[1-9][0-9]+)$ ]] || { echo "invalid parallel numberOfSubdomains: $N" >&2; exit 10; }',
                "",
            ])
        for command_id in ids:
            spec = self.resolved_command(command_id)
            if spec.get("kind") == "internal":
                if spec["operation"] == "remove-processors":
                    lines.append("find . -maxdepth 1 -type d -regextype posix-extended -regex './processor[0-9]+' -exec rm -rf -- {} +")
                continue
            provider_name = spec["provider"]
            if provider_name != active_provider:
                provider = self.config["providers"][provider_name]
                lines.extend(["", f"# Provider: {provider_name}", f"source {shlex.quote(provider['bashrc'])}"])
                active_provider = provider_name
            lines.append(self.render(command_id, runtime_n=True))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        return output


def optimization_recommendations(metrics: dict[str, Any], max_cells: int) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    cells = metrics.get("cells")
    if cells is not None and cells > max_cells:
        recs.append({
            "candidate": "reduce-refinement",
            "family": "refinement",
            "action": "reduce surface/region refinement or base block divisions",
            "reason": "cell limit exceeded",
            "proposed_changes": [],
        })
    if metrics.get("faces_over_non_ortho_limit"):
        recs.append({
            "candidate": "increase-snap-smoothing",
            "family": "snap-controls",
            "action": "test smoothing changes without changing meshQualityDict",
            "reason": "faces exceed non-orthogonality limit",
            "proposed_changes": [
                {"file": "system/snappyHexMeshDict", "entry": "snapControls.nSmoothPatch", "value": 5},
                {"file": "system/snappyHexMeshDict", "entry": "snapControls.nSolveIter", "value": 50},
            ],
        })
    if metrics.get("faces_below_twist_limit") or metrics.get("low_quality_face_tets"):
        recs.append({
            "candidate": "increase-snap-convergence",
            "family": "snap-controls",
            "action": "test snap convergence as a separate candidate",
            "reason": "face twist or face-tet quality failures",
            "proposed_changes": [
                {"file": "system/snappyHexMeshDict", "entry": "snapControls.nRelaxIter", "value": 8},
                {"file": "system/snappyHexMeshDict", "entry": "snapControls.nFeatureSnapIter", "value": 15},
            ],
        })
    if metrics.get("concave_cells"):
        recs.append({
            "candidate": "widen-refinement-transition",
            "family": "transition",
            "action": "test a wider refinement transition after inspecting localized geometry",
            "reason": "concave cells reported",
            "proposed_changes": [
                {"file": "system/snappyHexMeshDict", "entry": "castellatedMeshControls.nCellsBetweenLevels", "value": 4}
            ],
        })
    if not recs and not metrics.get("mesh_ok"):
        recs.append({"candidate": "collect-diagnostics", "family": "diagnostics", "action": "collect a complete checkMesh log before tuning", "reason": "no actionable metric found", "proposed_changes": []})
    return recs


def resolve_workspace_case(workspace: Path, case_arg: Path) -> Path:
    case = case_arg if case_arg.is_absolute() else workspace / case_arg
    case = case.resolve()
    if not path_within(case, workspace):
        raise CfdError(f"case must be inside workspace {workspace}: {case}")
    return case


def discover_cases(workspace: Path, root_arg: Path) -> list[str]:
    root = resolve_workspace_case(workspace, root_arg)
    if not root.is_dir():
        raise CfdError(f"discovery root is not a directory: {root}")
    cases: list[str] = []
    candidates = [root] + [path.parent for path in root.rglob("system") if path.is_dir()]
    for candidate in sorted(set(candidates)):
        try:
            validate_case(candidate)
        except CfdError:
            continue
        cases.append(candidate.relative_to(workspace).as_posix())
    return cases


def batch_plan_report(
    workspace: Path,
    config_path: Path | None,
    case_args: list[Path],
    workflow: str,
) -> tuple[dict[str, Any], list[Controller]]:
    controllers: list[Controller] = []
    entries: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for case_arg in case_args:
        case = resolve_workspace_case(workspace, case_arg)
        if case in seen:
            raise CfdError(f"duplicate case in batch: {case}")
        seen.add(case)
        try:
            controller = Controller(case, config_path=config_path, workspace_root=workspace)
            plan = controller.plan(workflow)
            status = "blocked" if plan["blocking_issues"] else "ready"
            entries.append({"case": str(case), "status": status, "plan": plan})
            controllers.append(controller)
        except CfdError as exc:
            entries.append({"case": str(case), "status": "error", "error": str(exc)})
    report = {
        "mode": "dry-run",
        "workflow": workflow,
        "execution": "sequential",
        "max_concurrent_jobs": 1,
        "job_order": [entry["case"] for entry in entries],
        "cases": entries,
        "ready": all(entry["status"] == "ready" for entry in entries),
    }
    return report, controllers


def execute_batch(
    report: dict[str, Any],
    controllers: list[Controller],
    workflow: str,
) -> dict[str, Any]:
    if not report["ready"] or len(controllers) != len(report["cases"]):
        raise CfdError("batch preflight is not ready; no case was executed")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("batch-%Y%m%dT%H%M%SZ")
    results: list[dict[str, str]] = []
    total = len(controllers)
    for index, controller in enumerate(controllers, start=1):
        log_dir = controller.case / ".cfd-runs" / "batch" / stamp / workflow
        report_progress(
            f"BATCH JOB {index}/{total} START {workflow}; case={controller.case}"
        )
        try:
            output = controller.execute_workflow(workflow, log_dir)
        except Exception as exc:
            report_progress(
                f"BATCH JOB {index}/{total} FAILED {workflow}; "
                f"case={controller.case}; reason={exc}"
            )
            raise
        report_progress(
            f"BATCH JOB {index}/{total} DONE {workflow}; "
            f"case={controller.case}; output={output}"
        )
        results.append({"case": str(controller.case), "status": "completed", "output": str(output)})
    return {
        "mode": "execute",
        "workflow": workflow,
        "execution": "sequential",
        "max_concurrent_jobs": 1,
        "results": results,
    }


def positive_cell_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("maximum cell count must be an integer") from exc
    if limit < 1:
        raise argparse.ArgumentTypeError("maximum cell count must be positive")
    return limit


def validate_resume_cell_limit(state: dict[str, Any], requested: int) -> None:
    original = state.get("source_metrics", {}).get("max_cells")
    if original is None:
        raise CfdError("mesh optimization state does not record a maximum cell count")
    if int(original) != requested:
        raise CfdError(
            f"resume maximum cell count must remain {original}; got {requested}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=Path.cwd(), help="CFD case root")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="allowed calculation workspace root")
    parser.add_argument("--config", type=Path, help="shared or case-specific agent configuration")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("inspect")
    sub.add_parser("list-commands")
    plan = sub.add_parser("plan")
    plan.add_argument("workflow")
    run_command = sub.add_parser("run-command")
    run_command.add_argument("command_id")
    run_command.add_argument("--execute", action="store_true")
    run_workflow = sub.add_parser("run-workflow")
    run_workflow.add_argument("workflow")
    run_workflow.add_argument("--execute", action="store_true")
    analyze = sub.add_parser("analyze-mesh")
    analyze.add_argument("--log", type=Path)
    analyze.add_argument("--max-cells", type=positive_cell_limit, required=True)
    optimize = sub.add_parser("plan-mesh-optimization")
    optimize.add_argument("--log", type=Path)
    optimize.add_argument("--max-cells", type=positive_cell_limit, required=True)
    run_optimize = sub.add_parser("optimize-mesh")
    run_optimize.add_argument("--log", type=Path, help="existing checkMesh log used as the baseline")
    run_optimize.add_argument("--max-cells", type=positive_cell_limit, required=True)
    run_optimize.add_argument("--run-id", help="explicit run id for a new run")
    run_optimize.add_argument("--resume", help="resume an existing run id")
    run_optimize.add_argument("--execute", action="store_true", help="start WSL/CFD processes and modify candidate dictionaries")
    opt_status = sub.add_parser("mesh-optimization-status")
    opt_status.add_argument("--run-id")
    recover = sub.add_parser("recover-mesh-optimization")
    recover.add_argument("run_id")
    export = sub.add_parser("export-allrun")
    export.add_argument("--workflow", default="solve")
    export.add_argument("--output", type=Path, default=Path("Allrun.solve"))
    export.add_argument("--force", action="store_true")
    discover = sub.add_parser("discover-cases")
    discover.add_argument("--root", type=Path, default=Path("."))
    batch_plan = sub.add_parser("batch-plan")
    batch_plan.add_argument("workflow")
    batch_plan.add_argument("cases", nargs="+", type=Path)
    batch_run = sub.add_parser("batch-run")
    batch_run.add_argument("workflow")
    batch_run.add_argument("cases", nargs="+", type=Path)
    batch_run.add_argument("--execute", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = args.workspace.resolve()
    config = args.config
    if config is not None and not config.is_absolute():
        config = workspace / config
    if args.action == "discover-cases":
        print(dump_json({"workspace": str(workspace), "cases": discover_cases(workspace, args.root)}))
        return 0
    if args.action in {"batch-plan", "batch-run"}:
        report, controllers = batch_plan_report(workspace, config, args.cases, args.workflow)
        if args.action == "batch-plan" or not args.execute:
            print(dump_json(report))
        else:
            print(dump_json(execute_batch(report, controllers, args.workflow)))
        return 0
    case_arg = resolve_workspace_case(workspace, args.case)
    controller = Controller(case_arg, config_path=config, workspace_root=workspace)
    case = controller.case

    if args.action == "inspect":
        log = case / "log.checkMesh.quality"
        parallel, count = controller.parallel_settings()
        result = {
            "case": str(case),
            "host_locale": host_locale(),
            "parallel": parallel,
            "number_of_subdomains": count,
            "application": application_name(case),
            "providers": controller.config["providers"],
            "configured_workflows": controller.config["workflows"],
            "resolved_workflows": {
                name: controller.plan(name) for name in controller.config["workflows"]
            },
            "mesh_metrics": mesh_metrics(log) if log.exists() else None,
        }
        print(dump_json(result))
    elif args.action == "list-commands":
        print(dump_json(controller.commands))
    elif args.action == "plan":
        print(dump_json(controller.plan(args.workflow)))
    elif args.action == "run-command":
        if not args.execute:
            print(dump_json({"mode": "dry-run", "command_id": args.command_id, "command": controller.render(args.command_id)}))
        else:
            controller.execute_command(args.command_id)
    elif args.action == "run-workflow":
        if not args.execute:
            print(dump_json(controller.plan(args.workflow)))
        else:
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            controller.execute_workflow(args.workflow, case / ".cfd-runs" / "workflows" / stamp)
    elif args.action in {"analyze-mesh", "plan-mesh-optimization"}:
        log = (args.log or case / "log.checkMesh.quality").resolve()
        metrics = mesh_metrics(log)
        max_cells = args.max_cells
        metrics["max_cells"] = max_cells
        metrics["cell_limit_pass"] = metrics["cells"] is not None and metrics["cells"] <= max_cells
        metrics["accepted"] = metrics["cell_limit_pass"] and metrics["failed_checks"] == 0 and metrics["mesh_ok"]
        if args.action == "analyze-mesh":
            print(dump_json(metrics))
        else:
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            run_dir = case / ".cfd-runs" / "mesh" / stamp
            run_dir.mkdir(parents=True, exist_ok=False)
            plan = {
                "mode": "planning-only",
                "created_at": stamp,
                "source_metrics": metrics,
                "limits": controller.config["mesh_optimization"]["limits"],
                "protected_files": controller.config["mesh_optimization"]["protected_files"],
                "recommendations": optimization_recommendations(metrics, max_cells),
                "workflow": controller.plan("mesh"),
            }
            path = run_dir / "plan.json"
            path.write_text(dump_json(plan) + "\n", encoding="utf-8")
            print(dump_json({"plan": str(path), "accepted": metrics["accepted"], "recommendations": plan["recommendations"]}))
    elif args.action == "optimize-mesh":
        optimizer = controller.mesh_optimizer(args.max_cells)
        if args.resume:
            run_dir, state = optimizer.status(args.resume)
            validate_resume_cell_limit(state, args.max_cells)
            if not args.execute:
                print(dump_json({"mode": "dry-run", "run": str(run_dir), "state": state, "next_action": "add --execute to resume"}))
            else:
                print(dump_json(optimizer.run(run_dir)))
        else:
            log = (args.log or case / "log.checkMesh.quality").resolve()
            if not log.is_file():
                if not args.execute:
                    raise CfdError("baseline checkMesh log not found; run the mesh workflow first or provide --log")
                baseline_id = dt.datetime.now(dt.timezone.utc).strftime("baseline-%Y%m%dT%H%M%SZ")
                baseline_logs = case / ".cfd-runs" / "mesh" / baseline_id / "logs"
                log = controller.execute_workflow("mesh", baseline_logs)
            preview = optimizer.preview(log)
            if not args.execute:
                print(dump_json(preview))
            else:
                run_dir, _ = optimizer.create(log, args.run_id)
                print(dump_json(optimizer.run(run_dir)))
    elif args.action == "mesh-optimization-status":
        optimizer = controller.mesh_optimizer()
        run_dir, state = optimizer.status(args.run_id) if args.run_id else optimizer.latest()
        print(dump_json({"run": str(run_dir), "state": state}))
    elif args.action == "recover-mesh-optimization":
        optimizer = controller.mesh_optimizer()
        print(dump_json(optimizer.recover(args.run_id)))
    elif args.action == "export-allrun":
        output = args.output if args.output.is_absolute() else case / args.output
        print(controller.export_allrun(args.workflow, output, args.force))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CfdError, OptimizationError, OSError, subprocess.SubprocessError) as exc:
        print(f"cfdctl: {exc}", file=sys.stderr)
        raise SystemExit(2)
