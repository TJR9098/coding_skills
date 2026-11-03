"""Behavior tests use disposable Git repositories, not the user's current checkout."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner, validator = load("review_run"), load("validate_review")
P = "def allowed(same, owner, admin):\n    return same and (owner or admin)\n"
H = "def allowed(same, owner, admin):\n    return same and owner or admin\n"
M = "def allowed(same, owner, admin):\n    return owner or admin\n"
EXISTING = """import unittest
from access import allowed
class Existing(unittest.TestCase):
    def test_owner(self):
        self.assertTrue(allowed(True, True, False))
    def test_admin(self):
        self.assertTrue(allowed(True, False, True))
    def test_outsider(self):
        self.assertFalse(allowed(False, False, False))
"""


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="review-fixture-")
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Review Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "core.autocrlf", "false")
        (self.repo / "access.py").write_text(P, encoding="utf-8")
        (self.repo / "test_existing.py").write_text(EXISTING, encoding="utf-8")
        (self.repo / ".gitignore").write_text("ignored.txt\n__pycache__/\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "Initial permissions")
        (self.repo / "access.py").write_text(H, encoding="utf-8")
        self.run = self.base / "run"

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    def prepare(self, scope="worktree", **kw):
        return runner.prepare(self.repo, scope=scope, output=self.run, **kw)

    def patch(self, target=M):
        patch = self.base / "mutant.patch"
        patch.write_text("diff --git a/access.py b/access.py\n--- a/access.py\n+++ b/access.py\n@@ -1,2 +1,2 @@\n def allowed(same, owner, admin):\n-    return same and (owner or admin)\n+" + target.splitlines()[1] + "\n", encoding="utf-8")
        return patch

    def screen(self, experiment="m1", target=M):
        return runner.screen_mutant(self.run, {"mutant_patch": str(self.patch(target)),
            "baseline_command": [sys.executable, "-m", "unittest", "test_existing"], "timeout_seconds": 20}, experiment)

    def case(self, expression="allowed(False, False, True)", expected="False", intent=False, body=None):
        overlay = self.base / ("overlay-" + str(len(list(self.base.glob("overlay-*")))))
        overlay.mkdir()
        body = body or f"self.assertEqual({expression}, {expected})"
        (overlay / "test_generated.py").write_text(
            f"import unittest\nfrom access import allowed\nclass Generated(unittest.TestCase):\n    def test_case(self):\n        {body}\n", encoding="utf-8")
        result = {"strategy": "intent-aware" if intent else "dodgy-diff", "test_overlay": str(overlay),
                  "test_command": [sys.executable, "-m", "unittest", "test_generated"], "timeout_seconds": 20}
        if intent:
            result["screen_id"] = "m1"
        return result

    def test_worktree_contains_staged_unstaged_untracked_and_deletions(self):
        (self.repo / "staged.txt").write_text("staged", encoding="utf-8")
        self.git("add", "staged.txt")
        (self.repo / "new.txt").write_text("new", encoding="utf-8")
        (self.repo / "ignored.txt").write_text("secret", encoding="utf-8")
        (self.repo / "test_existing.py").unlink()
        self.prepare()
        hp = self.run / "snapshots/H"
        self.assertEqual((hp / "access.py").read_text(), H)
        self.assertTrue((hp / "staged.txt").exists())
        self.assertTrue((hp / "new.txt").exists())
        self.assertFalse((hp / "ignored.txt").exists())
        self.assertFalse((hp / "test_existing.py").exists())

    def test_staged_uses_index_not_workspace(self):
        self.git("add", "access.py")
        (self.repo / "access.py").write_text(M, encoding="utf-8")
        self.prepare("staged")
        self.assertEqual((self.run / "snapshots/H/access.py").read_text(), H)

    def test_refs_ignores_later_workspace_changes(self):
        parent = self.git("rev-parse", "HEAD").decode().strip()
        self.git("add", "access.py")
        self.git("commit", "-qm", "Refactor")
        head = self.git("rev-parse", "HEAD").decode().strip()
        (self.repo / "access.py").write_text(M, encoding="utf-8")
        self.prepare("refs", base=parent, head=head)
        self.assertEqual((self.run / "snapshots/P/access.py").read_text(), P)
        self.assertEqual((self.run / "snapshots/H/access.py").read_text(), H)

    def test_dodgy_captures_difference_without_mutating_source(self):
        status = self.git("status", "--porcelain=v1")
        self.prepare()
        result = runner.compare(self.run, self.case(), "c1")
        self.assertEqual(result["outcome"], "differential_failure")
        self.assertEqual(result["commands"]["P-test"]["status"], "passed")
        self.assertEqual(result["commands"]["H-test"]["status"], "nonzero_exit")
        self.assertEqual(self.git("status", "--porcelain=v1"), status)
        self.assertEqual((self.repo / "access.py").read_text(), H)
        self.assertFalse((self.repo / "test_generated.py").exists())

    def test_reverse_direction_is_not_catch(self):
        self.prepare()
        result = runner.compare(self.run, self.case(expected="True"), "reverse")
        self.assertEqual(result["outcome"], "invalid_parent_test")
        self.assertNotIn("H-test", result["commands"])

    def test_intent_screen_then_kill_then_head(self):
        self.prepare()
        self.assertEqual(self.screen()["outcome"], "survived_existing_tests")
        result = runner.compare(self.run, self.case(intent=True), "intent")
        self.assertEqual(result["outcome"], "differential_failure")
        self.assertEqual(list(result["commands"])[:3], ["P-test", "M-test", "H-test"])
        self.assertTrue(result["mutant_killed"])

    def test_killing_mutant_does_not_imply_head_failure(self):
        self.prepare()
        self.screen()
        case = self.case("allowed(False, True, False)", intent=True)
        result = runner.compare(self.run, case, "not-head")
        self.assertEqual(result["outcome"], "no_differential_failure")

    def test_rejected_mutant_cannot_enter_compare(self):
        self.prepare()
        result = self.screen(target="def allowed(same, owner, admin):\n    return False\n")
        self.assertEqual(result["outcome"], "mutant_rejected")
        with self.assertRaises(runner.ReviewError):
            runner.compare(self.run, self.case(intent=True), "invalid")

    def test_unkilled_mutant_still_checks_head(self):
        self.prepare()
        # Equivalent mutant: survives S and cannot be killed, yet t may still catch H.
        self.screen(target="def allowed(same, owner, admin):\n    return (same and (owner or admin)) if same is not None else False\n")
        result = runner.compare(self.run, self.case(intent=True), "unkilled")
        self.assertFalse(result["mutant_killed"])
        self.assertEqual(result["outcome"], "differential_failure")

    def test_hypothesis_head_only_for_new_behavior(self):
        self.prepare()
        case = self.case()
        case.update(strategy="hypothesis", parent_applicable=False)
        result = runner.compare(self.run, case, "head-only")
        self.assertEqual(result["outcome"], "head_only_failure")
        self.assertNotIn("P-test", result["commands"])
        case["strategy"] = "dodgy-diff"
        with self.assertRaises(runner.ReviewError):
            runner.compare(self.run, case, "head-only-dodgy")

    def test_flaky_difference_is_not_a_catch(self):
        self.prepare()
        body = ("import os; first = not os.path.exists('marker'); open('marker', 'w').close(); "
                "self.assertFalse(first and os.path.basename(os.getcwd()) == 'H')")
        result = runner.compare(self.run, self.case(body=body), "flaky")
        self.assertEqual(result["outcome"], "flaky")
        self.assertIn("H-test-rerun1", result["commands"])

    def test_log_signals_are_hints_from_execution_logs(self):
        self.prepare()
        result = runner.compare(self.run, self.case(), "signals")
        self.assertIn("changed_bool", result["log_signals"]["H-test"]["true_positive"])
        log = self.base / "empty.log"
        log.write_text("Ran 0 tests in 0.000s\nModuleNotFoundError: No module named 'x'\n")
        self.assertEqual(runner.scan_log(log)["false_positive"], ["no_tests_executed", "module_missing"])

    def dependency_case(self, mode):
        case = self.case(body="import sys; sys.path.insert(0, 'deps'); import helper; "
                              "self.assertEqual(allowed(False, False, True), helper.EXPECTED)")
        case["dependencies"] = {"mode": mode, "paths": ["deps"]}
        return case

    def test_ignored_dependency_dirs_are_attached(self):
        with open(self.repo / ".gitignore", "a", encoding="utf-8") as f:
            f.write("deps/\n")
        (self.repo / "deps").mkdir()
        (self.repo / "deps/helper.py").write_text("EXPECTED = False\n", encoding="utf-8")
        (self.repo / "package.json").write_text("{}\n", encoding="utf-8")
        self.prepare()
        self.assertFalse((self.run / "snapshots/H/deps").exists())
        bare = self.case(body="import sys; sys.path.insert(0, 'deps'); import helper")
        self.assertEqual(runner.compare(self.run, bare, "no-deps")["outcome"], "invalid_parent_test")
        for mode in ("copy", "link"):
            result = runner.compare(self.run, self.dependency_case(mode), "deps-" + mode)
            self.assertEqual(result["outcome"], "differential_failure")
            self.assertEqual(result["dependencies"]["manifest_changed"], ["package.json"])
        linked = self.run / "experiments/deps-link/workspaces/H/deps"
        self.assertEqual(os.path.realpath(linked), os.path.realpath(self.repo / "deps"))
        self.assertEqual(sorted(p.name for p in (self.repo / "deps").iterdir()), ["helper.py"])

    def test_repository_placeholder_expands_in_command_and_environment(self):
        self.prepare()
        case = self.case(body="import os; self.assertEqual(os.environ['DEPS'], os.environ['EXPECTED'])")
        case["environment"] = {"DEPS": "{repository}/deps"}
        case["test_command"] = [sys.executable, "-c",
            "import os, subprocess, sys; os.environ['EXPECTED'] = sys.argv[1] + '/deps'; "
            "sys.exit(subprocess.call([sys.executable, '-m', 'unittest', 'test_generated']))", "{repository}"]
        result = runner.compare(self.run, case, "placeholder")
        self.assertEqual(result["outcome"], "no_differential_failure")
        self.assertEqual(result["commands"]["P-test"]["environment_overrides"]["DEPS"], str(self.repo.resolve()) + "/deps")

    @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
    def test_node_path_reads_installed_dependencies_without_copying(self):
        with open(self.repo / ".gitignore", "a", encoding="utf-8") as f:
            f.write("node_modules/\n")
        package = self.repo / "node_modules/leftpad"
        package.mkdir(parents=True)
        (package / "index.js").write_text("module.exports = (s, n) => s.padStart(n, ' ');\n", encoding="utf-8")
        self.prepare()
        overlay = self.base / "node-overlay"
        overlay.mkdir()
        (overlay / "generated.test.js").write_text(
            "const pad = require('leftpad');\nprocess.exit(pad('x', 3) === '  x' ? 0 : 1);\n", encoding="utf-8")
        case = {"strategy": "dodgy-diff", "test_overlay": str(overlay),
                "test_command": ["node", "generated.test.js"], "timeout_seconds": 20}
        self.assertEqual(runner.compare(self.run, case, "no-node-path")["outcome"], "invalid_parent_test")
        case["environment"] = {"NODE_PATH": "{repository}/node_modules"}
        with patch.dict(os.environ, {"NODE_PATH": str(self.base / "elsewhere")}):
            result = runner.compare(self.run, case, "node-path")
        self.assertEqual(result["outcome"], "no_differential_failure")
        self.assertFalse((self.run / "experiments/node-path/workspaces/P/node_modules").exists())
        self.assertEqual([p.name for p in package.iterdir()], ["index.js"])

    def test_tracked_or_unignored_dirs_cannot_be_attached(self):
        (self.repo / "plain").mkdir()
        (self.repo / "plain/x.py").write_text("", encoding="utf-8")
        self.prepare()
        case = self.case()
        case["dependencies"] = {"paths": ["plain"]}
        with self.assertRaises(runner.ReviewError):
            runner.compare(self.run, case, "plain")

    def test_validator_cross_checks_cited_experiments(self):
        manifest = self.prepare()
        runner.compare(self.run, self.case(), "c1")
        record = ReviewStateTests.record()
        finding = record["findings"][0]
        record["current_snapshot"] = finding["verified_snapshot"] = finding["history"][0]["snapshot"] = manifest["run_id"]
        finding.update(strategy="dodgy-diff", evidence_level="reproduced",
                       evidence=["experiments/c1/result.json", "experiments/c1/logs/H-test.log", "调用方无额外过滤"])
        self.assertEqual(validator.validate(record, base=self.run), [])
        finding["strategy"] = "intent-aware"
        self.assertTrue(any("reproduced" in e for e in validator.validate(record, base=self.run)))
        finding["strategy"] = "dodgy-diff"
        finding["evidence"].append("experiments/missing/result.json")
        self.assertTrue(any("does not exist" in e for e in validator.validate(record, base=self.run)))
        finding["evidence"].pop()
        record["current_snapshot"] = "other"
        self.assertTrue(any("run_id" in e for e in validator.validate(record, base=self.run)))

    def test_probe_reports_capabilities_without_running_project_code(self):
        (self.repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
        (self.repo / "package.json").write_text('{"scripts": {"test": "jest"}}', encoding="utf-8")
        (self.repo / "node_modules").mkdir()
        (self.repo / "vendor").mkdir()
        report = runner.probe(self.repo)
        self.assertIn("package.json", report["manifests"])
        self.assertEqual(report["dependency_dirs"], {"node_modules": {"git_ignored": True},
                                                     "vendor": {"git_ignored": False}})
        self.assertEqual(report["package_scripts"], {"test": "jest"})
        self.assertIsNotNone(report["runtimes_on_path"]["git"])
        self.assertEqual(report["python"]["executable"], sys.executable)

    def test_overlay_cannot_overwrite_production_code(self):
        self.prepare()
        case = self.case()
        (Path(case["test_overlay"]) / "access.py").write_text(P)
        with self.assertRaises(runner.ReviewError):
            runner.compare(self.run, case, "overwrite")

    def test_timeout_and_launch_error_are_not_test_failures(self):
        a = runner.run_command([sys.executable, "-c", "import time; time.sleep(5)"], self.base, .1, self.base / "timeout.log")
        b = runner.run_command([str(self.base / "missing-executable")], self.base, 1, self.base / "missing.log")
        self.assertEqual(a["status"], "timeout")
        self.assertEqual(b["status"], "launch_error")

    def test_output_cannot_be_inside_source(self):
        with self.assertRaises(runner.ReviewError):
            runner.prepare(self.repo, output=self.repo / "run")

    def test_git_environment_cannot_redirect_requested_repo(self):
        other = self.base / "other"
        other.mkdir()
        subprocess.run(["git", "-C", str(other), "init", "-q"], check=True)
        with patch.dict(os.environ, {"GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other)}):
            self.prepare()
        self.assertEqual((self.run / "snapshots/P/access.py").read_text(), P)

    def test_src_layout_uses_each_snapshot_not_inherited_pythonpath(self):
        src = self.repo / "src"
        src.mkdir()
        (src / "value.py").write_text("VALUE = 1\n")
        self.git("add", "src")
        self.git("commit", "-qm", "src baseline")
        (src / "value.py").write_text("VALUE = 2\n")
        self.prepare()
        case = self.case()
        overlay = Path(case["test_overlay"])
        (overlay / "test_generated.py").write_text("import unittest\nimport value\nclass T(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(value.VALUE, 1)\n")
        case["environment"] = {"PYTHONPATH": "{workspace}/src"}
        with patch.dict(os.environ, {"PYTHONPATH": str(src)}):
            result = runner.compare(self.run, case, "src")
        self.assertEqual(result["outcome"], "differential_failure")

    def test_experiment_cannot_overwrite_prior_evidence(self):
        self.prepare()
        case = self.case()
        runner.compare(self.run, case, "same")
        with self.assertRaises(FileExistsError):
            runner.compare(self.run, case, "same")

    def test_mutant_patch_cannot_introduce_symlink(self):
        self.prepare()
        patch_file = self.base / "link.patch"
        patch_file.write_text("diff --git a/escape b/escape\nnew file mode 120000\n--- /dev/null\n+++ b/escape\n@@ -0,0 +1 @@\n+../repo\n")
        result = runner.screen_mutant(self.run, {"mutant_patch": str(patch_file),
            "baseline_command": [sys.executable, "-m", "unittest", "test_existing"]}, "link")
        self.assertEqual(result["outcome"], "mutant_rejected")
        self.assertIn("symlinks", result["error"])


class ReviewStateTests(unittest.TestCase):
    @staticmethod
    def record():
        return {"schema_version": 3, "level": "standard", "review_id": "r", "round": 1, "max_rounds": 3,
                "current_snapshot": "s1", "review_mode": "self_check", "status": "reported",
                "verification": {"status": "not_requested"},
                "scope": "worktree", "intent": {"confirmed": [], "inferred": [], "unknown": []},
                "budget": {"generation_attempts": 3}, "strategies": [{"name": "static", "status": "completed", "reason": "scope"}],
                "limitations": [], "findings": [{"id": "F1", "strategy": "static", "title": "bug", "root_cause": "cause", "trigger": "input",
                    "impact": "wrong", "expected_basis": "spec", "location": "a.py:1", "origin": "introduced", "severity": "P2",
                    "evidence_level": "static_confirmed", "assessment": "bug", "counterevidence": "caller checked", "evidence": ["path proof"],
                    "verified_snapshot": "s1", "status": "open", "history": [{"round": 1, "snapshot": "s1", "action": "opened", "reason": "confirmed"}]}]}

    def test_valid_report_and_stale_evidence(self):
        data = self.record()
        self.assertEqual(validator.validate(data), [])
        data["current_snapshot"] = "s2"
        self.assertTrue(any("stale conclusion" in e for e in validator.validate(data)))
        data["findings"][0]["status"] = "stale"
        self.assertEqual(validator.validate(data), [])

    def test_closed_findings_survive_new_snapshots(self):
        data = self.record()
        finding = data["findings"][0]
        finding.update(status="dismissed", assessment="expected_change", author_response="intended")
        data.update(current_snapshot="s2", round=2)
        self.assertEqual(validator.validate(data), [])
        data["round"] = 3
        self.assertEqual(validator.validate(data), [])
        finding["verified_snapshot"] = "s9"
        self.assertTrue(any("history" in e for e in validator.validate(data)))

    def test_author_response_rules(self):
        data = self.record()
        finding = data["findings"][0]
        finding.update(status="dismissed", author_response="not_worth_it")
        self.assertEqual(validator.validate(data), [])
        finding["author_response"] = "fixed"
        self.assertTrue(validator.validate(data))
        finding.update(status="open", author_response="ignored")
        self.assertTrue(any("author_response" in e for e in validator.validate(data)))
        del data["level"]
        self.assertTrue(any("level" in e for e in validator.validate(data)))

    def test_unverified_bug_cannot_be_open(self):
        data = self.record()
        data["findings"][0]["evidence_level"] = "hypothesis"
        self.assertTrue(validator.validate(data))

    def test_half_applied_verifier_result_is_not_deliverable(self):
        # Mode and assessment were edited after the verifier returned, but status/snapshot were not.
        data = self.record()
        finding = data["findings"][0]
        finding.update(status="candidate", verified_snapshot=None)
        data.update(review_mode="independent_context", verification={"status": "pending"})
        errors = validator.validate(data)
        self.assertTrue(any("verification.status=applied" in e for e in errors))
        self.assertTrue(any("bug assessment must be opened" in e for e in errors))
        # The honest checkpoint before the verifier returns is deliverable as a self-check.
        finding["assessment"] = "uncertain"
        data["review_mode"] = "self_check"
        self.assertEqual(validator.validate(data), [])
        # A finished-in-progress draft may still hold unassessed candidates.
        finding["assessment"] = "pending"
        self.assertTrue(any("assess every finding" in e for e in validator.validate(data)))
        data["status"] = "in_progress"
        self.assertEqual(validator.validate(data), [])

    def test_applied_verification_records_verdicts(self):
        data = self.record()
        data.update(review_mode="independent_context",
                    verification={"status": "applied", "artifacts": ["verifier.md"], "verdicts": {"F1": "confirmed"}})
        self.assertEqual(validator.validate(data), [])
        data["verification"]["verdicts"] = {"F1": "refuted"}
        self.assertTrue(any("refuted" in e for e in validator.validate(data)))
        data["verification"]["verdicts"] = {"F9": "confirmed"}
        self.assertTrue(any("unknown finding" in e for e in validator.validate(data)))
        data["verification"] = {"status": "applied", "verdicts": {"F1": "confirmed"}}
        self.assertTrue(any("artifacts" in e for e in validator.validate(data)))
        data["verification"] = {"status": "pending", "verdicts": {"F1": "confirmed"}}
        data["review_mode"] = "self_check"
        self.assertTrue(any("verdicts belong" in e for e in validator.validate(data)))

    def test_verification_artifact_and_related_ids_are_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            data = self.record()
            data.update(review_mode="independent_context",
                        verification={"status": "applied", "artifacts": ["verifier.md"], "verdicts": {"F1": "uncertain"}})
            self.assertTrue(any("verifier.md" in e for e in validator.validate(data, base=temp)))
            (Path(temp) / "verifier.md").write_text("F1: uncertain", encoding="utf-8")
            self.assertEqual(validator.validate(data, base=temp), [])
        data["findings"][0]["related"] = ["F1"]
        self.assertTrue(any("related" in e for e in validator.validate(data)))
        second = copy.deepcopy(data["findings"][0])
        second.update(id="F2", related=["F1"])
        data["findings"][0]["related"] = ["F2"]
        data["findings"].append(second)
        self.assertEqual(validator.validate(data), [])

    def test_round_cap_requires_human_and_no_duplicate_ids(self):
        data = self.record()
        data["round"] = 3
        self.assertTrue(validator.validate(data))
        data["status"] = "needs_human"
        self.assertEqual(validator.validate(data), [])
        data["findings"].append(copy.deepcopy(data["findings"][0]))
        self.assertTrue(any("Duplicate" in e for e in validator.validate(data)))


if __name__ == "__main__":
    unittest.main()
