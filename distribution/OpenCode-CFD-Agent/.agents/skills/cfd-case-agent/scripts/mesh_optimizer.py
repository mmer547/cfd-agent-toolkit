"""Transactional mesh-dictionary optimization for cfdctl.

This module contains no WSL or CFD-specific process code.  A caller supplies a
workflow runner, which makes the state machine testable with recorded logs.
"""

from __future__ import annotations

import datetime as dt
import difflib
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Callable


class OptimizationError(RuntimeError):
    pass


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8", newline="\n")
    temporary.replace(path)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _masked_foam(text: str) -> str:
    """Mask comments and quoted strings while preserving character offsets."""
    result = list(text)
    index = 0
    while index < len(text):
        if text.startswith("//", index):
            end = text.find("\n", index)
            end = len(text) if end < 0 else end
            for pos in range(index, end):
                result[pos] = " "
            index = end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise OptimizationError("unterminated block comment in OpenFOAM dictionary")
            end += 2
            for pos in range(index, end):
                if result[pos] not in "\r\n":
                    result[pos] = " "
            index = end
        elif text[index] == '"':
            end = index + 1
            while end < len(text):
                if text[end] == '"' and text[end - 1] != "\\":
                    end += 1
                    break
                end += 1
            if end > len(text) or text[end - 1] != '"':
                raise OptimizationError("unterminated quoted string in OpenFOAM dictionary")
            for pos in range(index, end):
                if result[pos] not in "\r\n":
                    result[pos] = " "
            index = end
        else:
            index += 1
    return "".join(result)


def _matching_brace(masked: str, opening: int, limit: int) -> int:
    depth = 0
    for index in range(opening, limit):
        if masked[index] == "{":
            depth += 1
        elif masked[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    raise OptimizationError("unbalanced braces in OpenFOAM dictionary")


def _named_block(masked: str, name: str, start: int, end: int) -> tuple[int, int]:
    pattern = re.compile(rf"(?m)^\s*{re.escape(name)}\s*\{{")
    matches = list(pattern.finditer(masked, start, end))
    direct: list[tuple[int, int]] = []
    for match in matches:
        opening = masked.find("{", match.start(), match.end())
        depth = 0
        for char in masked[start:match.start()]:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
        if depth == 0:
            direct.append((opening + 1, _matching_brace(masked, opening, end)))
    if len(direct) != 1:
        raise OptimizationError(f"expected one direct block named {name!r}, found {len(direct)}")
    return direct[0]


def replace_foam_entry(text: str, dotted_entry: str, new_value: Any) -> tuple[str, str]:
    """Replace one scalar/list entry within a dotted chain of named blocks."""
    parts = dotted_entry.split(".")
    if not parts or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part) for part in parts):
        raise OptimizationError(f"unsafe OpenFOAM entry path: {dotted_entry!r}")
    rendered = str(new_value).lower() if isinstance(new_value, bool) else str(new_value)
    if not rendered or any(token in rendered for token in (";", "{", "}", "\n", "\r", "\0")):
        raise OptimizationError(f"unsafe OpenFOAM value for {dotted_entry}: {rendered!r}")

    masked = _masked_foam(text)
    start, end = 0, len(text)
    for block in parts[:-1]:
        start, end = _named_block(masked, block, start, end)

    key = parts[-1]
    pattern = re.compile(rf"(?m)^(?P<indent>[ \t]*){re.escape(key)}(?P<space>[ \t]+)(?P<value>[^;\r\n]+)(?P<semi>;)")
    candidates = []
    for match in pattern.finditer(masked, start, end):
        depth = 0
        for char in masked[start:match.start()]:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
        if depth == 0:
            candidates.append(match)
    if len(candidates) != 1:
        raise OptimizationError(f"expected one direct entry {dotted_entry!r}, found {len(candidates)}")
    match = candidates[0]
    old_value = text[match.start("value"):match.end("value")].strip()
    updated = text[:match.start("value")] + rendered + text[match.end("value"):]
    return updated, old_value


def metric_constraints(metrics: dict[str, Any], constraints: dict[str, Any]) -> dict[str, Any]:
    max_cells = int(constraints["max_cells"])
    max_failed = int(constraints.get("check_mesh_failures", 0))
    cells = metrics.get("cells")
    failed = metrics.get("failed_checks")
    cell_pass = cells is not None and cells <= max_cells
    quality_pass = failed is not None and failed <= max_failed and bool(metrics.get("mesh_ok"))
    result = dict(metrics)
    result.update({
        "max_cells": max_cells,
        "max_failed_checks": max_failed,
        "cell_limit_pass": cell_pass,
        "quality_pass": quality_pass,
        "accepted": cell_pass and quality_pass,
    })
    return result


def metric_rank(metrics: dict[str, Any]) -> list[float]:
    """Return a lexicographic quality rank; lower is better."""
    missing = 10**15
    cells = metrics.get("cells")
    max_cells = metrics.get("max_cells")
    cell_excess = missing if cells is None else max(0, cells - int(max_cells))
    return [
        0 if metrics.get("accepted") else 1,
        cell_excess,
        metrics.get("failed_checks") if metrics.get("failed_checks") is not None else missing,
        metrics.get("low_quality_face_tets") if metrics.get("low_quality_face_tets") is not None else missing,
        metrics.get("faces_over_non_ortho_limit") if metrics.get("faces_over_non_ortho_limit") is not None else missing,
        metrics.get("faces_below_twist_limit") if metrics.get("faces_below_twist_limit") is not None else missing,
        metrics.get("concave_cells") if metrics.get("concave_cells") is not None else missing,
        metrics.get("max_non_orthogonality") if metrics.get("max_non_orthogonality") is not None else missing,
        cells if cells is not None else missing,
    ]


def candidate_matches(candidate: dict[str, Any], metrics: dict[str, Any], constraints: dict[str, Any]) -> bool:
    condition = candidate.get("when")
    if not condition:
        return True
    metric = condition["metric"]
    actual = metrics.get(metric)
    expected = constraints[condition["constraint"]] if "constraint" in condition else condition.get("value", 0)
    if actual is None:
        return False
    operator = condition.get("operator", "gt")
    comparisons = {
        "gt": actual > expected,
        "ge": actual >= expected,
        "eq": actual == expected,
        "lt": actual < expected,
        "le": actual <= expected,
    }
    if operator not in comparisons:
        raise OptimizationError(f"unsupported candidate condition operator: {operator}")
    return comparisons[operator]


class MeshOptimizer:
    def __init__(
        self,
        case: Path,
        config: dict[str, Any],
        parse_metrics: Callable[[Path], dict[str, Any]],
        workflow_plan: Callable[[], dict[str, Any]],
        workflow_runner: Callable[[Path], Path],
        *,
        max_cells: int | None = None,
    ):
        self.case = case.resolve()
        raw_settings = config["mesh_optimization"]
        self.settings = dict(raw_settings)
        self.settings["constraints"] = dict(raw_settings.get("constraints", {}))
        if max_cells is not None:
            self.settings["constraints"]["max_cells"] = max_cells
        self.parse_metrics = parse_metrics
        self.workflow_plan = workflow_plan
        self.workflow_runner = workflow_runner
        self.root = self.case / ".cfd-runs" / "mesh"
        constraints = self.settings["constraints"]
        limits = self.settings["limits"]
        if "max_cells" in constraints and int(constraints["max_cells"]) < 1:
            raise OptimizationError("mesh max_cells must be positive")
        if int(constraints.get("check_mesh_failures", 0)) < 0:
            raise OptimizationError("check_mesh_failures cannot be negative")
        if int(limits["max_iterations"]) < 1 or int(limits["stop_after_no_improvement"]) < 1:
            raise OptimizationError("mesh optimization limits must be positive")
        overlap = set(self.settings.get("mutable_files", [])) & set(self.settings.get("protected_files", []))
        if overlap:
            raise OptimizationError(f"mesh files cannot be both mutable and protected: {sorted(overlap)}")

    def _relative_file(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise OptimizationError(f"mesh tuning file must be case-relative: {value}")
        resolved = (self.case / path).resolve()
        try:
            resolved.relative_to(self.case)
        except ValueError as exc:
            raise OptimizationError(f"mesh tuning file escapes the case: {value}") from exc
        return path

    def _validate_change(self, change: dict[str, Any]) -> None:
        relative = self._relative_file(change["file"])
        relative_text = relative.as_posix()
        if relative_text in self.settings.get("protected_files", []):
            raise OptimizationError(f"protected mesh file cannot be tuned: {relative_text}")
        if relative_text not in self.settings.get("mutable_files", []):
            raise OptimizationError(f"mesh file is not mutable: {relative_text}")
        try:
            rule = self.settings["tunable_entries"][relative_text][change["entry"]]
        except KeyError as exc:
            raise OptimizationError(f"entry is not allowlisted: {relative_text}:{change['entry']}") from exc
        value = change["value"]
        if "allowed" in rule and value not in rule["allowed"]:
            raise OptimizationError(f"value is not allowed for {change['entry']}: {value!r}")
        if "min" in rule:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise OptimizationError(f"numeric value required for {change['entry']}")
            if value < rule["min"] or value > rule["max"]:
                raise OptimizationError(f"value outside bounds for {change['entry']}: {value}")

    def candidates(self, source_metrics: dict[str, Any]) -> list[dict[str, Any]]:
        constraints = self.settings["constraints"]
        result = []
        seen = set()
        for candidate in self.settings.get("candidate_sets", []):
            if candidate["id"] in seen:
                raise OptimizationError(f"duplicate mesh candidate id: {candidate['id']}")
            seen.add(candidate["id"])
            change_keys = set()
            for change in candidate.get("changes", []):
                self._validate_change(change)
                key = (change["file"], change["entry"])
                if key in change_keys:
                    raise OptimizationError(f"candidate {candidate['id']} changes one entry more than once: {key}")
                change_keys.add(key)
            if candidate_matches(candidate, source_metrics, constraints):
                result.append(candidate)
        return result

    def preview(self, source_log: Path) -> dict[str, Any]:
        metrics = metric_constraints(self.parse_metrics(source_log), self.settings["constraints"])
        return {
            "mode": "dry-run",
            "case": str(self.case),
            "source_metrics": metrics,
            "limits": self.settings["limits"],
            "candidates": self.candidates(metrics),
            "workflow": self.workflow_plan(),
        }

    def _run_dir(self, run_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id):
            raise OptimizationError(f"invalid run id: {run_id!r}")
        return self.root / run_id

    def _state(self, run_dir: Path) -> dict[str, Any]:
        try:
            return json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise OptimizationError(f"mesh optimization run not found: {run_dir.name}") from exc

    def _save_state(self, run_dir: Path, state: dict[str, Any]) -> None:
        state["updated_at"] = utc_stamp()
        atomic_json(run_dir / "state.json", state)

    def _backup_baseline(self, run_dir: Path) -> dict[str, str]:
        hashes = {}
        for value in self.settings["mutable_files"]:
            relative = self._relative_file(value)
            source = self.case / relative
            if not source.is_file():
                raise OptimizationError(f"mutable mesh dictionary not found: {relative.as_posix()}")
            destination = run_dir / "baseline" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            hashes[relative.as_posix()] = file_hash(source)
        return hashes

    def restore_baseline(self, run_dir: Path) -> None:
        state = self._state(run_dir)
        for relative_text, expected_hash in state["baseline_hashes"].items():
            relative = self._relative_file(relative_text)
            source = run_dir / "baseline" / relative
            if not source.is_file() or file_hash(source) != expected_hash:
                raise OptimizationError(f"baseline backup is missing or corrupted: {relative_text}")
            destination = self.case / relative
            shutil.copy2(source, destination)

    def create(self, source_log: Path, run_id: str | None = None) -> tuple[Path, dict[str, Any]]:
        source_log = source_log.resolve()
        metrics = metric_constraints(self.parse_metrics(source_log), self.settings["constraints"])
        run_id = run_id or utc_stamp()
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        baseline_hashes = self._backup_baseline(run_dir)
        shutil.copy2(source_log, run_dir / "baseline-checkMesh.log")
        state = {
            "version": 1,
            "run_id": run_id,
            "case": str(self.case),
            "status": "accepted-baseline" if metrics["accepted"] else "created",
            "created_at": utc_stamp(),
            "current_index": 0,
            "attempt_count": 0,
            "no_improvement_count": 0,
            "best_iteration": None,
            "accepted_iteration": None,
            "baseline_hashes": baseline_hashes,
            "source_metrics": metrics,
            "candidates": self.candidates(metrics),
            "iterations": [],
        }
        self._save_state(run_dir, state)
        atomic_json(run_dir / "workflow-plan.json", self.workflow_plan())
        return run_dir, state

    def _apply_candidate(self, run_dir: Path, iteration_dir: Path, candidate: dict[str, Any]) -> list[dict[str, Any]]:
        self.restore_baseline(run_dir)
        applied = []
        touched: dict[Path, str] = {}
        for change in candidate.get("changes", []):
            self._validate_change(change)
            relative = self._relative_file(change["file"])
            path = self.case / relative
            original = touched.get(relative, path.read_text(encoding="utf-8"))
            updated, old_value = replace_foam_entry(original, change["entry"], change["value"])
            touched[relative] = updated
            applied.append({**change, "old_value": old_value, "changed": old_value != str(change["value"])})
        if not any(item["changed"] for item in applied):
            return applied
        for relative, updated in touched.items():
            path = self.case / relative
            before = path.read_text(encoding="utf-8")
            atomic_text(path, updated)
            after_path = iteration_dir / "dictionaries" / relative
            after_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_text(after_path, updated)
            diff = difflib.unified_diff(
                before.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"baseline/{relative.as_posix()}",
                tofile=f"candidate/{relative.as_posix()}",
            )
            diff_path = iteration_dir / "diffs" / Path(relative.as_posix() + ".patch")
            diff_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_text(diff_path, "".join(diff))
        return applied

    def run(self, run_dir: Path) -> dict[str, Any]:
        state = self._state(run_dir)
        if state["status"] in {"accepted", "accepted-baseline"}:
            return state
        if state["status"] in {"running", "failed", "interrupted"}:
            self.restore_baseline(run_dir)
        state.pop("error", None)
        limits = self.settings["limits"]
        max_iterations = int(limits["max_iterations"])
        stop_no_improvement = int(limits["stop_after_no_improvement"])
        candidates = state["candidates"]
        baseline_rank = metric_rank(state["source_metrics"])
        best_rank = baseline_rank
        if state["best_iteration"] is not None:
            prior = next(item for item in state["iterations"] if item["number"] == state["best_iteration"])
            best_rank = prior["rank"]

        try:
            while state["current_index"] < len(candidates) and len(state["iterations"]) < max_iterations:
                candidate = candidates[state["current_index"]]
                number = int(state.get("attempt_count", 0)) + 1
                state["attempt_count"] = number
                iteration_dir = run_dir / f"iteration-{number:03d}-{candidate['id']}"
                iteration_dir.mkdir(parents=True, exist_ok=False)
                atomic_json(iteration_dir / "candidate.json", candidate)
                state.update({"status": "running", "active_iteration": number, "active_candidate": candidate["id"]})
                self._save_state(run_dir, state)
                applied = self._apply_candidate(run_dir, iteration_dir, candidate)
                atomic_json(iteration_dir / "applied-changes.json", applied)
                if not any(item["changed"] for item in applied):
                    record = {"number": number, "candidate": candidate["id"], "status": "skipped-no-change", "applied_changes": applied}
                    state["iterations"].append(record)
                    state["current_index"] += 1
                    self._save_state(run_dir, state)
                    continue

                check_log = self.workflow_runner(iteration_dir / "logs")
                shutil.copy2(check_log, iteration_dir / "checkMesh.log")
                metrics = metric_constraints(self.parse_metrics(check_log), self.settings["constraints"])
                rank = metric_rank(metrics)
                improved = rank < best_rank
                record = {
                    "number": number,
                    "candidate": candidate["id"],
                    "status": "accepted" if metrics["accepted"] else "evaluated",
                    "applied_changes": applied,
                    "metrics": metrics,
                    "rank": rank,
                    "improved": improved,
                    "directory": str(iteration_dir),
                }
                atomic_json(iteration_dir / "metrics.json", metrics)
                state["iterations"].append(record)
                state["current_index"] += 1
                if improved:
                    best_rank = rank
                    state["best_iteration"] = number
                    state["no_improvement_count"] = 0
                else:
                    state["no_improvement_count"] += 1
                if metrics["accepted"]:
                    state["status"] = "accepted"
                    state["accepted_iteration"] = number
                    state.pop("active_iteration", None)
                    state.pop("active_candidate", None)
                    self._save_state(run_dir, state)
                    return state
                self.restore_baseline(run_dir)
                state.pop("active_iteration", None)
                state.pop("active_candidate", None)
                self._save_state(run_dir, state)
                if state["no_improvement_count"] >= stop_no_improvement:
                    state["status"] = "stopped-no-improvement"
                    self._save_state(run_dir, state)
                    return state

            self.restore_baseline(run_dir)
            state["status"] = "stopped-limit" if len(state["iterations"]) >= max_iterations else "exhausted-candidates"
            state.pop("active_iteration", None)
            state.pop("active_candidate", None)
            self._save_state(run_dir, state)
            return state
        except BaseException as exc:
            self.restore_baseline(run_dir)
            state["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
            state["error"] = str(exc)
            active_number = state.get("active_iteration")
            if active_number is not None and not any(item["number"] == active_number for item in state["iterations"]):
                state["iterations"].append({
                    "number": active_number,
                    "candidate": state.get("active_candidate"),
                    "status": state["status"],
                    "error": str(exc),
                })
            self._save_state(run_dir, state)
            raise

    def recover(self, run_id: str) -> dict[str, Any]:
        run_dir = self._run_dir(run_id)
        self.restore_baseline(run_dir)
        state = self._state(run_dir)
        state["status"] = "recovered"
        state.pop("active_iteration", None)
        state.pop("active_candidate", None)
        self._save_state(run_dir, state)
        return state

    def status(self, run_id: str) -> tuple[Path, dict[str, Any]]:
        run_dir = self._run_dir(run_id)
        return run_dir, self._state(run_dir)

    def latest(self) -> tuple[Path, dict[str, Any]]:
        runs = sorted((path for path in self.root.glob("*") if (path / "state.json").is_file()), reverse=True)
        if not runs:
            raise OptimizationError("no mesh optimization run exists")
        return runs[0], self._state(runs[0])
