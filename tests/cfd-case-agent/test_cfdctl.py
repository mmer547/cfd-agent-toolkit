import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "distribution" / "OpenCode-CFD-Agent" / ".agents" / "skills" / "cfd-case-agent" / "scripts" / "cfdctl.py"
PACKAGE_CONFIG = PROJECT_ROOT / "distribution" / "OpenCode-CFD-Agent" / ".cfd-agent.json"
SPEC = importlib.util.spec_from_file_location("cfdctl", SCRIPT)
cfdctl = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(cfdctl)
sys.path.insert(0, str(SCRIPT.parent))
import mesh_optimizer


class CfdCtlTests(unittest.TestCase):
    def make_controller_case(
        self,
        root,
        *,
        parallel=True,
        block_mesh=True,
        surface_features=True,
        snappy=True,
        mesh_quality=True,
    ):
        case = Path(root)
        for name in ("0", "constant", "system"):
            (case / name).mkdir(parents=True, exist_ok=True)
        if parallel:
            (case / "system" / "decomposeParDict").write_text("numberOfSubdomains 8;\n", encoding="utf-8")
        (case / "system" / "controlDict").write_text("application foamRun;\n", encoding="utf-8")
        if block_mesh:
            (case / "system" / "blockMeshDict").write_text("FoamFile {}\n", encoding="utf-8")
        if surface_features:
            (case / "system" / "surfaceFeaturesDict").write_text("surfaces ();\n", encoding="utf-8")
        if snappy:
            (case / "system" / "snappyHexMeshDict").write_text("castellatedMesh true;\n", encoding="utf-8")
        if mesh_quality:
            (case / "system" / "meshQualityDict").write_text("maxNonOrtho 65;\n", encoding="utf-8")
        config = {
            "version": 1,
            "providers": {"openfoam": {"distribution": "Ubuntu", "bashrc": "/opt/openfoam13/etc/bashrc"}},
            "workflows": {
                "mesh": ["openfoam.blockMesh", "openfoam.surfaceFeatures", "openfoam.decomposePar.mesh", "openfoam.snappyHexMesh", "openfoam.reconstructPar.mesh", "internal.removeProcessors", "openfoam.checkMesh", "openfoam.checkMesh.basic"],
                "solve": ["openfoam.decomposePar.solve", "openfoam.solver", "openfoam.reconstructPar.solve", "internal.removeProcessors"],
                "rerun": ["internal.prepareRerun", "openfoam.decomposePar.solve", "openfoam.solver", "openfoam.reconstructPar.solve", "internal.removeProcessors"],
            },
            "mesh_optimization": {
                "constraints": {"check_mesh_failures": 0},
                "limits": {"max_iterations": 3, "stop_after_no_improvement": 2},
                "mutable_files": ["system/snappyHexMeshDict"],
                "protected_files": ["system/meshQualityDict"],
                "tunable_entries": {},
                "candidate_sets": [],
            },
        }
        (case / ".cfd-agent.json").write_text(json.dumps(config), encoding="utf-8")
        return case

    def make_optimizer_case(self, root, candidate_values=(5, 7)):
        case = Path(root)
        for name in ("0", "constant", "system"):
            (case / name).mkdir(parents=True, exist_ok=True)
        dictionary = """snapControls
{
    nSmoothPatch 3;
    nSolveIter 30;
}
addLayersControls
{
    nSmoothPatch 99;
}
"""
        (case / "system" / "snappyHexMeshDict").write_text(dictionary, encoding="utf-8")
        config = {
            "mesh_optimization": {
                "constraints": {"check_mesh_failures": 0},
                "limits": {"max_iterations": 6, "stop_after_no_improvement": 3},
                "mutable_files": ["system/snappyHexMeshDict"],
                "protected_files": ["system/meshQualityDict"],
                "tunable_entries": {
                    "system/snappyHexMeshDict": {
                        "snapControls.nSmoothPatch": {"min": 1, "max": 10}
                    }
                },
                "candidate_sets": [
                    {
                        "id": f"smooth-{value}",
                        "when": {"metric": "failed_checks", "operator": "gt", "value": 0},
                        "changes": [{"file": "system/snappyHexMeshDict", "entry": "snapControls.nSmoothPatch", "value": value}],
                    }
                    for value in candidate_values
                ],
            }
        }
        return case, config

    def write_mesh_log(self, path, cells=1000, failures=1, mesh_ok=False):
        lines = [f"    cells:            {cells}", "Mesh non-orthogonality Max: 60 average: 8"]
        if mesh_ok:
            lines.append("Mesh OK.")
        else:
            lines.extend(["1 faces with low quality or negative volume decomposition tets.", f"Failed {failures} mesh checks."])
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return Path(path)

    def test_foam_entry_ignores_comments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dict"
            path.write_text("// numberOfSubdomains 99;\nnumberOfSubdomains 8;\n", encoding="utf-8")
            self.assertEqual(cfdctl.foam_entry(path, "numberOfSubdomains"), "8")

    def test_replace_nested_foam_entry_does_not_touch_same_key_in_other_block(self):
        source = "snapControls\n{\n  nRelaxIter 5;\n}\naddLayersControls\n{\n  nRelaxIter 9;\n}\n"
        updated, old = mesh_optimizer.replace_foam_entry(source, "snapControls.nRelaxIter", 8)
        self.assertEqual(old, "5")
        self.assertIn("snapControls\n{\n  nRelaxIter 8;", updated)
        self.assertIn("addLayersControls\n{\n  nRelaxIter 9;", updated)

    def test_replace_deep_refinement_level(self):
        source = "a {\n b {\n  c {\n   level (4 4);\n  }\n }\n}\n"
        updated, old = mesh_optimizer.replace_foam_entry(source, "a.b.c.level", "(3 3)")
        self.assertEqual(old, "(4 4)")
        self.assertIn("level (3 3);", updated)

    def test_parse_existing_check_mesh_log(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "log.checkMesh.quality"
            log.write_text(
                "cells: 2235181\nMesh non-orthogonality Max: 65.1 average: 8\n"
                "faces with non-orthogonality > 65 degrees: 10\n"
                "faces with face twist < 0.02: 4\nFailed 3 mesh checks.\n",
                encoding="utf-8",
            )
            metrics = cfdctl.mesh_metrics(log)
            self.assertEqual(metrics["cells"], 2235181)
            self.assertEqual(metrics["failed_checks"], 3)
            self.assertEqual(metrics["faces_over_non_ortho_limit"], 10)
            self.assertEqual(metrics["faces_below_twist_limit"], 4)

    def test_mesh_ok_implies_zero_failed_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            log = self.write_mesh_log(Path(directory) / "check.log", mesh_ok=True)
            metrics = cfdctl.mesh_metrics(log)
            self.assertEqual(metrics["failed_checks"], 0)

    def test_mesh_optimization_requires_user_cell_limit(self):
        parser = cfdctl.build_parser()
        for action in ("analyze-mesh", "plan-mesh-optimization", "optimize-mesh"):
            with self.subTest(action=action):
                with self.assertRaises(SystemExit):
                    parser.parse_args([action])
                with self.assertRaises(SystemExit):
                    parser.parse_args([action, "--max-cells", "0"])
                args = parser.parse_args([action, "--max-cells", "2500"])
                self.assertEqual(args.max_cells, 2500)
        shared_config = json.loads(PACKAGE_CONFIG.read_text(encoding="utf-8"))
        self.assertNotIn("max_cells", shared_config["mesh_optimization"]["constraints"])

    def test_resume_preserves_original_user_cell_limit(self):
        state = {"source_metrics": {"max_cells": 2500}}
        cfdctl.validate_resume_cell_limit(state, 2500)
        with self.assertRaisesRegex(cfdctl.CfdError, "must remain 2500"):
            cfdctl.validate_resume_cell_limit(state, 3000)
        with self.assertRaisesRegex(cfdctl.CfdError, "does not record"):
            cfdctl.validate_resume_cell_limit({"source_metrics": {}}, 2500)


    def test_optimizer_stops_when_baseline_already_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            case, config = self.make_optimizer_case(directory)
            baseline_log = self.write_mesh_log(case / "baseline.log", mesh_ok=True)
            called = []
            optimizer = mesh_optimizer.MeshOptimizer(case, config, cfdctl.mesh_metrics, lambda: {}, lambda _: called.append(True), max_cells=2500)
            run_dir, _ = optimizer.create(baseline_log, "passing-baseline")
            state = optimizer.run(run_dir)
            self.assertEqual(state["status"], "accepted-baseline")
            self.assertEqual(called, [])

    def test_mesh_plan_uses_decompose_par_dict(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = cfdctl.Controller(self.make_controller_case(directory))
            plan = controller.plan("mesh")
            self.assertEqual(plan["number_of_subdomains"], 8)
            snappy = next(step for step in plan["steps"] if step["command_id"] == "openfoam.snappyHexMesh")
            self.assertIn("mpirun -np 8", snappy["command"])

    def test_block_mesh_only_plan_is_serial_without_decompose_par_dict(self):
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_controller_case(
                directory,
                parallel=False,
                surface_features=False,
                snappy=False,
                mesh_quality=False,
            )
            controller = cfdctl.Controller(case)
            plan = controller.plan("mesh")
        self.assertFalse(plan["parallel"])
        self.assertIsNone(plan["number_of_subdomains"])
        self.assertEqual(
            [step["command_id"] for step in plan["steps"]],
            ["openfoam.blockMesh", "openfoam.checkMesh.basic"],
        )
        self.assertEqual(plan["steps"][1]["command"], "checkMesh -allGeometry -allTopology")

    def test_old_mesh_workflow_gets_basic_check_mesh_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_controller_case(
                directory,
                parallel=False,
                surface_features=False,
                snappy=False,
                mesh_quality=False,
            )
            config_path = case / ".cfd-agent.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["workflows"]["mesh"].remove("openfoam.checkMesh.basic")
            config_path.write_text(json.dumps(config), encoding="utf-8")
            plan = cfdctl.Controller(case).plan("mesh")
        self.assertEqual(
            [step["command_id"] for step in plan["steps"]],
            ["openfoam.blockMesh", "openfoam.checkMesh.basic"],
        )

    def test_snappy_mesh_runs_serial_when_decompose_par_dict_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_controller_case(directory, parallel=False)
            controller = cfdctl.Controller(case)
            plan = controller.plan("mesh")
        ids = [step["command_id"] for step in plan["steps"]]
        self.assertIn("openfoam.snappyHexMesh", ids)
        self.assertNotIn("openfoam.decomposePar.mesh", ids)
        self.assertNotIn("openfoam.reconstructPar.mesh", ids)
        snappy = next(step for step in plan["steps"] if step["command_id"] == "openfoam.snappyHexMesh")
        self.assertEqual(snappy["command"], "snappyHexMesh -overwrite")

    def test_solver_plan_is_serial_without_decompose_par_dict(self):
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_controller_case(directory, parallel=False)
            controller = cfdctl.Controller(case)
            plan = controller.plan("solve")
        self.assertFalse(plan["parallel"])
        self.assertEqual(
            [step["command_id"] for step in plan["steps"]],
            ["openfoam.solver"],
        )
        self.assertEqual(plan["steps"][0]["command"], "foamRun")

    def test_registry_is_json(self):
        data = json.loads(cfdctl.REGISTRY_PATH.read_text(encoding="utf-8"))
        self.assertIn("openfoam.checkMesh", data["commands"])
        self.assertIn("openfoam.checkMesh.basic", data["commands"])
        self.assertIn("openfoam.foamCleanCase", data["commands"])
        self.assertIn("iconcfd.solver", data["commands"])
        self.assertIn("internal.prepareRerun", data["commands"])
        self.assertIn("internal.prepareInitialConditions", data["commands"])
        self.assertIn("openfoam.mapFields.latestTime", data["commands"])
        self.assertIn("openfoam.topoSet", data["commands"])
        self.assertIn("openfoam.potentialFoam", data["commands"])

    def test_case_identity_distinguishes_foundation_and_openfoam_com(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            foundation = root / "foundation"
            commercial = root / "commercial"
            for case, banner in (
                (foundation, "Website: https://openfoam.org\nVersion: 14\nsolver incompressibleFluid;\n"),
                (commercial, "Website: www.openfoam.com\nVersion: v2512\napplication simpleFoam;\n"),
            ):
                for name in ("0", "constant", "system"):
                    (case / name).mkdir(parents=True, exist_ok=True)
                (case / "system" / "controlDict").write_text(banner, encoding="utf-8")
            self.assertEqual(cfdctl.case_identity(foundation), {"vendor": "foundation", "version": "14"})
            self.assertEqual(cfdctl.case_identity(commercial), {"vendor": "openfoam-com", "version": "v2512"})

    def test_recent_release_profiles_resolve_from_shared_config(self):
        shared_config = PROJECT_ROOT / "distribution" / "OpenCode-CFD-Agent" / ".cfd-agent.json"
        releases = [
            ("foundation", "10", "foundation-v10"),
            ("foundation", "14", "foundation-v14"),
            ("openfoam-com", "v2206", "openfoam-com-v2206"),
            ("openfoam-com", "v2606", "openfoam-com-v2606"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            for index, (vendor, version, profile) in enumerate(releases):
                case = workspace / f"case-{index}"
                for name in ("0", "constant", "system"):
                    (case / name).mkdir(parents=True, exist_ok=True)
                website = "openfoam.org" if vendor == "foundation" else "openfoam.com"
                (case / "system" / "controlDict").write_text(
                    f"Website: {website}\nVersion: {version}\napplication simpleFoam;\n",
                    encoding="utf-8",
                )
                controller = cfdctl.Controller(case, config_path=shared_config, workspace_root=workspace)
                self.assertEqual(controller.profile_id, profile)
                self.assertEqual(controller.provider_name, profile)

    def test_structural_rule_reads_alternative_decompose_dictionary_after_rename(self):
        shared_config = PROJECT_ROOT / "distribution" / "OpenCode-CFD-Agent" / ".cfd-agent.json"
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            case = workspace / "temp" / "case-8472"
            for name in ("0.orig", "constant", "system"):
                (case / name).mkdir(parents=True, exist_ok=True)
            (case / "system" / "controlDict").write_text(
                "Website: www.openfoam.com\nVersion: v2512\napplication simpleFoam;\n",
                encoding="utf-8",
            )
            (case / "system" / "decomposeParDict.6").write_text(
                "numberOfSubdomains 6;\n", encoding="utf-8"
            )
            (case / "system" / "snappyHexMeshDict").write_text("castellatedMesh true;\n", encoding="utf-8")
            (case / "system" / "topoSetDict").write_text("actions ();\n", encoding="utf-8")
            (case / "system" / "surfaceFeatureExtractDict").write_text("surfaces ();\n", encoding="utf-8")
            (case / "Allrun").write_text(
                'cp -f "$FOAM_TUTORIALS"/resources/geometry/motorBike.obj.gz constant/triSurface/\n',
                encoding="utf-8",
            )
            controller = cfdctl.Controller(case, config_path=shared_config, workspace_root=workspace)
            self.assertEqual(controller.parallel_settings(), (True, 6))
            self.assertIn("openfoamcom-snappy-toposet-alt-decompose", controller.matched_case_rules)
            command = controller.render("openfoam.decomposePar.solve")
            self.assertIn("-decomposeParDict system/decomposeParDict.6", command)
            mesh_plan = controller.plan("mesh")
            self.assertEqual(mesh_plan["steps"][0]["command_id"], "internal.stageResources")
            self.assertIn(
                "openfoamcom.surfaceFeatureExtract",
                [step["command_id"] for step in mesh_plan["steps"]],
            )
            self.assertEqual(mesh_plan["blocking_issues"], [])
            copy = mesh_plan["steps"][0]["details"]["copies"][0]
            self.assertEqual(copy["reference"], "Allrun")
            self.assertTrue(copy["provider_tutorial_fallback"])
            self.assertTrue(copy["needed"])

    def test_stage_resources_uses_provider_environment_and_copies_only_missing_file(self):
        shared_config = PROJECT_ROOT / "distribution" / "OpenCode-CFD-Agent" / ".cfd-agent.json"
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            case = workspace / "scratch" / "renamed-tutorial"
            for name in ("0.orig", "constant", "system"):
                (case / name).mkdir(parents=True, exist_ok=True)
            (case / "system" / "controlDict").write_text(
                "Website: www.openfoam.com\nVersion: v2512\napplication simpleFoam;\n",
                encoding="utf-8",
            )
            for name in ("snappyHexMeshDict", "topoSetDict", "decomposeParDict.6"):
                (case / "system" / name).write_text(
                    "numberOfSubdomains 6;\n" if name.startswith("decompose") else "FoamFile {}\n",
                    encoding="utf-8",
                )
            (case / "Allrun").write_text("cp tutorial geometry\n", encoding="utf-8")
            controller = cfdctl.Controller(case, config_path=shared_config, workspace_root=workspace)
            destination = case / "constant" / "triSurface" / "motorBike.obj.gz"

            def successful_copy(*_args, **_kwargs):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"geometry")
                return SimpleNamespace(returncode=0)

            with patch.object(
                controller, "_provider_invocation", return_value=(["fake-provider"], None)
            ) as provider_invocation:
                with patch.object(cfdctl.subprocess, "run", side_effect=successful_copy) as run:
                    controller.execute_command("internal.stageResources", case / ".cfd-runs" / "test-stage")

            shell_command = provider_invocation.call_args.args[1]
            self.assertIn('${FOAM_TUTORIALS:-}', shell_command)
            self.assertIn('${WM_PROJECT_DIR:-}', shell_command)
            self.assertIn("/usr/lib/openfoam/openfoam2512/tutorials", shell_command)
            self.assertIn("resources/geometry/motorBike.obj.gz", shell_command)
            self.assertIn("constant/triSurface/motorBike.obj.gz", shell_command)
            self.assertEqual(run.call_count, 1)
            self.assertTrue(destination.is_file())
            applicable, reason = controller.command_applicability("internal.stageResources")
            self.assertFalse(applicable)
            self.assertIn("already exist", reason)

    def test_resource_copy_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_controller_case(directory, parallel=False)
            controller = cfdctl.Controller(case)
            controller.case_override["resource_copies"] = [
                {
                    "source_env": "FOAM_TUTORIALS",
                    "source": "../secret",
                    "destination": "constant/triSurface/file",
                    "reference": "Allrun",
                }
            ]
            with self.assertRaisesRegex(cfdctl.CfdError, "unsafe resource source"):
                controller.resource_copy_plan()
            controller.case_override["resource_copies"][0]["source"] = "resources/file"
            controller.case_override["resource_copies"][0]["reference"] = "Allrun.mesh"
            with self.assertRaisesRegex(cfdctl.CfdError, "Allrun reference is missing"):
                controller.resource_copy_plan()

    def test_structural_rule_discovers_renamed_map_fields_sibling(self):
        shared_config = PROJECT_ROOT / "distribution" / "OpenCode-CFD-Agent" / ".cfd-agent.json"
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "scratch" / "job-001"
            target = workspace / "scratch" / "job-002"
            for case in (source, target):
                for name in ("0", "constant", "system"):
                    (case / name).mkdir(parents=True, exist_ok=True)
                (case / "system" / "controlDict").write_text(
                    "Website: www.openfoam.com\nVersion: v2412\napplication parcelFoam;\n",
                    encoding="utf-8",
                )
            (source / "constant" / "kinematicCloudPositions").write_text("positions ();\n", encoding="utf-8")
            (target / "system" / "mapFieldsDict").write_text("mapMethod mapNearest;\n", encoding="utf-8")
            controller = cfdctl.Controller(target, config_path=shared_config, workspace_root=workspace)
            self.assertEqual(controller.plan("solve")["dependencies"], ["scratch/job-001"])
            self.assertIn("openfoamcom-mapfields-from-sibling-initial-state", controller.matched_case_rules)
            self.assertEqual(
                controller.render("openfoam.mapFields.latestTime"),
                "mapFields ../job-001 -sourceTime latestTime",
            )

    def test_discover_cases_and_reject_outside_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self.make_controller_case(workspace / "cases" / "a", parallel=False)
            (workspace / "not-a-case").mkdir()
            self.assertEqual(cfdctl.discover_cases(workspace, Path(".")), ["cases/a"])
            with self.assertRaisesRegex(cfdctl.CfdError, "inside workspace"):
                cfdctl.resolve_workspace_case(workspace, workspace.parent)

    def test_batch_plan_defaults_to_one_case_job_in_supplied_order(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self.make_controller_case(
                workspace / "case-b", parallel=False, surface_features=False, snappy=False
            )
            self.make_controller_case(
                workspace / "case-a", parallel=False, surface_features=False, snappy=False
            )
            report, controllers = cfdctl.batch_plan_report(
                workspace, None, [Path("case-b"), Path("case-a")], "mesh"
            )
        self.assertTrue(report["ready"])
        self.assertEqual(report["execution"], "sequential")
        self.assertEqual(report["max_concurrent_jobs"], 1)
        self.assertEqual(
            [Path(path).name for path in report["job_order"]], ["case-b", "case-a"]
        )
        self.assertEqual([controller.case.name for controller in controllers], ["case-b", "case-a"])

    def test_execute_batch_finishes_each_job_before_starting_next(self):
        events = []
        active_jobs = 0
        maximum_active_jobs = 0

        def runner(name):
            def execute(_workflow, _log_dir):
                nonlocal active_jobs, maximum_active_jobs
                events.append(("start", name))
                active_jobs += 1
                maximum_active_jobs = max(maximum_active_jobs, active_jobs)
                active_jobs -= 1
                events.append(("done", name))
                return Path(f"{name}.log")

            return execute

        controllers = [
            SimpleNamespace(case=Path("case-b"), execute_workflow=runner("case-b")),
            SimpleNamespace(case=Path("case-a"), execute_workflow=runner("case-a")),
        ]
        report = {"ready": True, "cases": [{}, {}]}
        result = cfdctl.execute_batch(report, controllers, "mesh")
        self.assertEqual(
            events,
            [("start", "case-b"), ("done", "case-b"), ("start", "case-a"), ("done", "case-a")],
        )
        self.assertEqual(maximum_active_jobs, 1)
        self.assertEqual(result["max_concurrent_jobs"], 1)

    def test_execute_batch_does_not_start_later_job_after_failure(self):
        first = Mock(side_effect=RuntimeError("job failed"))
        second = Mock()
        controllers = [
            SimpleNamespace(case=Path("case-1"), execute_workflow=first),
            SimpleNamespace(case=Path("case-2"), execute_workflow=second),
        ]
        with self.assertRaisesRegex(RuntimeError, "job failed"):
            cfdctl.execute_batch({"ready": True, "cases": [{}, {}]}, controllers, "solve")
        first.assert_called_once()
        second.assert_not_called()

    def test_foundation_solver_entry_uses_foam_run(self):
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_controller_case(directory, parallel=False)
            (case / "system" / "controlDict").write_text(
                "solver incompressibleFluid;\n", encoding="utf-8"
            )
            controller = cfdctl.Controller(case)
            self.assertEqual(controller.render("openfoam.solver"), "foamRun")

    def test_foundation_region_solvers_entry_uses_foam_multi_run(self):
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_controller_case(directory, parallel=False)
            (case / "system" / "controlDict").write_text(
                "regionSolvers { fluid incompressibleFluid; }\n", encoding="utf-8"
            )
            controller = cfdctl.Controller(case)
            self.assertEqual(controller.render("openfoam.solver"), "foamMultiRun")

    def test_application_entry_supports_keysight_style_solver_name(self):
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_controller_case(directory, parallel=False)
            (case / "system" / "controlDict").write_text(
                "application iconFoam;\n", encoding="utf-8"
            )
            controller = cfdctl.Controller(case)
            self.assertEqual(controller.render("openfoam.solver"), "iconFoam")

    def test_rerun_preserves_zero_and_mesh_but_removes_results(self):
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_controller_case(directory)
            (case / "0" / "U").write_text("initial\n", encoding="utf-8")
            (case / "constant" / "polyMesh").mkdir()
            (case / "constant" / "polyMesh" / "points").write_text("mesh\n", encoding="utf-8")
            for name in ("0.5", "1", "processor0", "processors8", "postProcessing", "VTK", "logs"):
                (case / name).mkdir()
            (case / "log.solver").write_text("old log\n", encoding="utf-8")
            controller = cfdctl.Controller(case)
            plan = controller.plan("rerun")
            details = plan["steps"][0]["details"]
            self.assertEqual(details["initial_conditions"]["mode"], "preserve-zero")
            self.assertNotIn("constant/polyMesh", details["remove"])
            controller.execute_command("internal.prepareRerun", case / ".cfd-runs" / "test-rerun")
            self.assertEqual((case / "0" / "U").read_text(encoding="utf-8"), "initial\n")
            self.assertTrue((case / "constant" / "polyMesh" / "points").is_file())
            for name in ("0.5", "1", "processor0", "processors8", "postProcessing", "VTK", "logs", "log.solver"):
                self.assertFalse((case / name).exists())
            self.assertTrue((case / ".cfd-runs" / "test-rerun" / "prepare-rerun.json").is_file())

    def test_rerun_preserves_field_orig_and_removes_generated_counterpart(self):
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_controller_case(directory, parallel=False)
            template = case / "0" / "alpha.water.orig"
            generated = case / "0" / "alpha.water"
            template.write_text("template\n", encoding="utf-8")
            generated.write_text("generated\n", encoding="utf-8")
            controller = cfdctl.Controller(case)
            details = controller.plan("rerun")["steps"][0]["details"]
            self.assertIn("0/alpha.water", details["remove"])
            self.assertNotIn("0/alpha.water.orig", details["remove"])
            controller.execute_command("internal.prepareRerun")
            self.assertTrue(template.is_file())
            self.assertFalse(generated.exists())

    def test_prepare_initial_conditions_copies_directory_template_once(self):
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_controller_case(directory, parallel=False)
            (case / "0").rmdir()
            (case / "0.orig").mkdir()
            (case / "0.orig" / "U").write_text("template\n", encoding="utf-8")
            controller = cfdctl.Controller(case)
            plan = controller.plan("solve")
            # Legacy unit-test workflow does not include the step, but the internal command is independently safe.
            self.assertEqual(plan["workflow"], "solve")
            controller.execute_command("internal.prepareInitialConditions")
            self.assertEqual((case / "0" / "U").read_text(encoding="utf-8"), "template\n")
            self.assertTrue((case / "0.orig" / "U").is_file())
            controller.execute_command("internal.prepareInitialConditions")
            self.assertEqual((case / "0" / "U").read_text(encoding="utf-8"), "template\n")

    def test_rerun_restores_zero_from_zero_orig(self):
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_controller_case(directory, parallel=False)
            (case / "0" / "U").write_text("calculated\n", encoding="utf-8")
            (case / "0.orig").mkdir()
            (case / "0.orig" / "U").write_text("template\n", encoding="utf-8")
            controller = cfdctl.Controller(case)
            controller.execute_command("internal.prepareRerun")
            self.assertEqual((case / "0" / "U").read_text(encoding="utf-8"), "template\n")
            self.assertEqual((case / "0.orig" / "U").read_text(encoding="utf-8"), "template\n")

    def test_rerun_creates_zero_from_zero_org(self):
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_controller_case(directory, parallel=False)
            (case / "0").rmdir()
            (case / "0.org").mkdir()
            (case / "0.org" / "p").write_text("template\n", encoding="utf-8")
            controller = cfdctl.Controller(case)
            controller.execute_command("internal.prepareRerun")
            self.assertEqual((case / "0" / "p").read_text(encoding="utf-8"), "template\n")
            self.assertTrue((case / "0.org" / "p").is_file())

    def test_rerun_rejects_ambiguous_initial_templates_without_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_controller_case(directory, parallel=False)
            (case / "0.orig").mkdir()
            (case / "0.org").mkdir()
            (case / "1").mkdir()
            controller = cfdctl.Controller(case)
            with self.assertRaisesRegex(cfdctl.CfdError, "both 0.orig and 0.org"):
                controller.execute_command("internal.prepareRerun")
            self.assertTrue((case / "0").is_dir())
            self.assertTrue((case / "1").is_dir())

    def test_rerun_restores_zero_before_later_cleanup_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_controller_case(directory, parallel=False)
            (case / "0" / "U").write_text("calculated\n", encoding="utf-8")
            (case / "0.orig").mkdir()
            (case / "0.orig" / "U").write_text("template\n", encoding="utf-8")
            (case / "1").mkdir()
            controller = cfdctl.Controller(case)
            real_rmtree = cfdctl.shutil.rmtree

            def fail_on_result(path):
                if Path(path).name == "1":
                    raise OSError("simulated cleanup failure")
                return real_rmtree(path)

            with patch.object(cfdctl.shutil, "rmtree", side_effect=fail_on_result):
                with self.assertRaisesRegex(OSError, "simulated cleanup failure"):
                    controller.execute_command("internal.prepareRerun")
            self.assertEqual((case / "0" / "U").read_text(encoding="utf-8"), "template\n")
            self.assertEqual((case / "0.orig" / "U").read_text(encoding="utf-8"), "template\n")

    def test_host_locale_uses_python_locale_without_subprocess(self):
        with patch.object(cfdctl.locale, "getlocale", return_value=("Japanese_Japan", "932")):
            self.assertEqual(cfdctl.host_locale(), "Japanese_Japan")

    def test_host_locale_has_safe_fallback(self):
        with patch.object(cfdctl.locale, "getlocale", return_value=(None, None)):
            self.assertEqual(cfdctl.host_locale(), "unknown")

    def test_linux_execution_runs_provider_directly_in_case(self):
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_controller_case(Path(directory) / "case")
            controller = cfdctl.Controller(case)
            with patch.object(cfdctl.sys, "platform", "linux"):
                with patch.object(cfdctl.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
                    controller.execute_command("openfoam.checkMesh", Path(directory) / "logs")
        invocation = run.call_args.args[0]
        self.assertEqual(invocation[:2], ["bash", "-lc"])
        self.assertEqual(run.call_args.kwargs["cwd"], case.resolve())
        self.assertIn("source /opt/openfoam13/etc/bashrc", invocation[2])

    def test_windows_execution_routes_provider_through_configured_wsl(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = cfdctl.Controller(self.make_controller_case(directory))
            provider = controller.config["providers"]["openfoam"]
            with patch.object(cfdctl.sys, "platform", "win32"):
                with patch.object(controller, "_linux_case", return_value="/mnt/c/work/cfd_skills"):
                    invocation, cwd = controller._provider_invocation(provider, "checkMesh")
        self.assertIsNone(cwd)
        self.assertEqual(invocation[:4], ["wsl.exe", "-d", "Ubuntu", "--cd"])
        self.assertEqual(invocation[4], "/mnt/c/work/cfd_skills")
        self.assertEqual(invocation[-3:], ["bash", "-lc", "checkMesh"])

    def test_linux_case_reports_wsl_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = cfdctl.Controller(self.make_controller_case(directory))
            result = SimpleNamespace(returncode=1, stdout="", stderr="distribution failed to start\n")
            with patch.object(cfdctl.subprocess, "run", return_value=result):
                with self.assertRaisesRegex(
                    cfdctl.CfdError,
                    "Ubuntu.*distribution failed to start",
                ):
                    controller._linux_case("Ubuntu")

    def test_linux_case_normalizes_windows_path_before_wslpath(self):
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_controller_case(Path(directory) / "case with spaces")
            controller = cfdctl.Controller(case)
            result = SimpleNamespace(returncode=0, stdout="/mnt/c/case with spaces\n", stderr="")
            with patch.object(cfdctl.subprocess, "run", return_value=result) as run:
                converted = controller._linux_case("Ubuntu")
        invocation = run.call_args.args[0]
        self.assertEqual(invocation[:6], ["wsl.exe", "-d", "Ubuntu", "--", "wslpath", "-a"])
        self.assertEqual(invocation[6], str(case.resolve()).replace("\\", "/"))
        self.assertNotIn("\\", invocation[6])
        self.assertEqual(converted, "/mnt/c/case with spaces")

    def test_linux_case_rejects_empty_converted_path(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = cfdctl.Controller(self.make_controller_case(directory))
            result = SimpleNamespace(returncode=0, stdout="\n", stderr="")
            with patch.object(cfdctl.subprocess, "run", return_value=result):
                with self.assertRaisesRegex(cfdctl.CfdError, "empty path.*Ubuntu"):
                    controller._linux_case("Ubuntu")

    def test_quality_failures_produce_bounded_candidates(self):
        recommendations = cfdctl.optimization_recommendations(
            {"faces_over_non_ortho_limit": 10, "faces_below_twist_limit": 4, "low_quality_face_tets": 142, "concave_cells": 94682, "cells": 2235181},
            3_000_000,
        )
        candidate_names = {item["candidate"] for item in recommendations}
        self.assertIn("increase-snap-smoothing", candidate_names)
        self.assertIn("increase-snap-convergence", candidate_names)
        for candidate in recommendations:
            for change in candidate["proposed_changes"]:
                self.assertNotEqual(change["file"], "system/meshQualityDict")

    def test_exported_solver_allrun_initializes_before_reading_processors(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = cfdctl.Controller(self.make_controller_case(Path(directory) / "case"))
            output = Path(directory) / "Allrun"
            controller.export_allrun("solve", output, force=False)
            text = output.read_text(encoding="utf-8")
        self.assertLess(text.index("source /opt/openfoam13/etc/bashrc"), text.index("foamDictionary"))
        self.assertIn('mpirun -np "$N" foamRun -parallel', text)
        self.assertNotIn("snappyHexMesh", text)
        self.assertNotIn("blockMesh", text)

    def test_exported_serial_solver_allrun_does_not_read_decompose_par_dict(self):
        with tempfile.TemporaryDirectory() as directory:
            case = self.make_controller_case(Path(directory) / "case", parallel=False)
            controller = cfdctl.Controller(case)
            output = Path(directory) / "Allrun"
            controller.export_allrun("solve", output, force=False)
            text = output.read_text(encoding="utf-8")
        self.assertIn("foamRun", text)
        self.assertNotIn("foamDictionary", text)
        self.assertNotIn("decomposePar", text)
        self.assertNotIn("reconstructPar", text)
        self.assertNotIn("mpirun", text)

    def test_optimizer_restores_baseline_between_trials_and_keeps_accepted_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            case, config = self.make_optimizer_case(directory)
            baseline_log = self.write_mesh_log(case / "baseline.log", failures=2)
            observed = []

            def runner(log_dir):
                current = (case / "system" / "snappyHexMeshDict").read_text(encoding="utf-8")
                observed.append(current)
                if "nSmoothPatch 5;" in current:
                    return self.write_mesh_log(log_dir / "check.log", failures=1)
                if "nSmoothPatch 7;" in current:
                    return self.write_mesh_log(log_dir / "check.log", failures=0, mesh_ok=True)
                raise AssertionError("unexpected candidate dictionary")

            optimizer = mesh_optimizer.MeshOptimizer(case, config, cfdctl.mesh_metrics, lambda: {"workflow": "mesh"}, runner, max_cells=2500)
            run_dir, _ = optimizer.create(baseline_log, "test-run")
            state = optimizer.run(run_dir)
            final_text = (case / "system" / "snappyHexMeshDict").read_text(encoding="utf-8")
            self.assertEqual(state["status"], "accepted")
            self.assertEqual(state["accepted_iteration"], 2)
            self.assertIn("nSmoothPatch 5;", observed[0])
            self.assertNotIn("nSmoothPatch 5;", observed[1])
            self.assertIn("nSmoothPatch 7;", observed[1])
            self.assertIn("nSmoothPatch 7;", final_text)
            self.assertTrue((run_dir / "iteration-001-smooth-5" / "diffs" / "system" / "snappyHexMeshDict.patch").is_file())

    def test_optimizer_failure_restores_baseline_and_can_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            case, config = self.make_optimizer_case(directory, candidate_values=(5,))
            baseline_log = self.write_mesh_log(case / "baseline.log", failures=2)

            def failing_runner(_):
                raise RuntimeError("simulated solver failure")

            optimizer = mesh_optimizer.MeshOptimizer(case, config, cfdctl.mesh_metrics, lambda: {}, failing_runner, max_cells=2500)
            run_dir, _ = optimizer.create(baseline_log, "resume-run")
            with self.assertRaises(RuntimeError):
                optimizer.run(run_dir)
            restored = (case / "system" / "snappyHexMeshDict").read_text(encoding="utf-8")
            self.assertIn("nSmoothPatch 3;", restored)
            self.assertEqual(optimizer.status("resume-run")[1]["status"], "failed")

            optimizer.workflow_runner = lambda log_dir: self.write_mesh_log(log_dir / "check.log", failures=0, mesh_ok=True)
            state = optimizer.run(run_dir)
            self.assertEqual(state["status"], "accepted")
            self.assertEqual(state["accepted_iteration"], 2)

    def test_optimizer_rejects_protected_or_unlisted_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            case, config = self.make_optimizer_case(directory)
            config["mesh_optimization"]["candidate_sets"][0]["changes"][0]["file"] = "system/meshQualityDict"
            optimizer = mesh_optimizer.MeshOptimizer(case, config, cfdctl.mesh_metrics, lambda: {}, lambda _: Path(), max_cells=2500)
            with self.assertRaises(mesh_optimizer.OptimizationError):
                optimizer.candidates({"failed_checks": 1})


if __name__ == "__main__":
    unittest.main()
