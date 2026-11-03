#!/usr/bin/env python3
"""Prepare review snapshots and record isolated test experiments (stdlib + Git)."""
import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid


class ReviewError(Exception):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def git_environment():
    # A caller's alternate index/worktree must not redirect an explicitly requested repo.
    return {**{k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")},
            "GIT_OPTIONAL_LOCKS": "0"}


def git(repo, *args, ok=(0,)):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=git_environment(),
    )
    if result.returncode not in ok:
        raise ReviewError(result.stderr.decode("utf-8", "replace").strip() or "Git failed")
    return result.stdout


def relative_path(name):
    p = PurePosixPath(name)
    if (not name or p.is_absolute() or ".." in p.parts or "\\" in name
            or any(part.lower() == ".git" or ":" in part for part in p.parts)):
        raise ReviewError(f"Unsupported relative path: {name!r}")
    return Path(*p.parts)


def reject_link(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ReviewError(f"Symlinks/reparse points require host-specific isolation: {path}")
    return info


def tree_entries(repo, revision=None):
    entries = []
    args = ("ls-tree", "-rz", revision) if revision else ("ls-files", "--stage", "-z")
    for record in git(repo, *args).split(b"\0"):
        if not record:
            continue
        meta, raw_name = record.split(b"\t", 1)
        parts = meta.decode("ascii").split()
        mode, oid = (parts[0], parts[2]) if revision else (parts[0], parts[1])
        if not revision and parts[2] != "0":
            raise ReviewError("Index contains unresolved conflicts")
        name = raw_name.decode("utf-8")
        relative_path(name)
        if mode not in ("100644", "100755"):
            raise ReviewError(f"Unsupported Git mode {mode} (submodule/symlink): {name}")
        entries.append((name, mode, oid))
    return entries


def export_entries(repo, entries, destination):
    destination.mkdir(parents=True)
    # Batch avoids one process per source file; objects are addressed by immutable IDs.
    payload = "".join(oid + "\n" for _, _, oid in entries).encode("ascii")
    result = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"],
                            input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=git_environment())
    if result.returncode:
        raise ReviewError(result.stderr.decode("utf-8", "replace"))
    data, cursor = result.stdout, 0
    for name, mode, oid in entries:
        end = data.index(b"\n", cursor)
        header = data[cursor:end].decode("ascii").split()
        if len(header) != 3 or header[0] != oid or header[1] != "blob":
            raise ReviewError(f"Cannot export Git blob for {name}")
        size = int(header[2])
        start = end + 1
        target = destination / relative_path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data[start:start + size])
        target.chmod(0o755 if mode == "100755" else 0o644)
        cursor = start + size + 1


def worktree_files(repo, entries):
    names = {name for name, _, _ in entries}
    names.update(s.decode("utf-8") for s in git(
        repo, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if s)
    result = {}
    for name in sorted(names):
        rel = relative_path(name)
        target = repo / rel
        for prefix in reversed(target.parents):
            if prefix != repo and repo in prefix.parents and prefix.exists():
                reject_link(prefix)
        if not target.exists() and not target.is_symlink():
            continue  # Deleted tracked file.
        info = reject_link(target)
        if not stat.S_ISREG(info.st_mode):
            raise ReviewError(f"Not a regular source file: {name}")
        result[name] = (info.st_size, info.st_mtime_ns, info.st_mode)
    return result


def prepare(repo, scope="worktree", base=None, head=None, output=None):
    repo = Path(git(Path(repo).resolve(), "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if scope == "refs" and (not base or not head):
        raise ReviewError("refs scope requires explicit --base and --head")
    if scope != "refs" and head:
        raise ReviewError("--head applies only to refs scope")
    base_oid = git(repo, "rev-parse", "--verify", (base or "HEAD") + "^{commit}").decode().strip()
    head_oid = git(repo, "rev-parse", "--verify", (head or "HEAD") + "^{commit}").decode().strip()
    parent_entries = tree_entries(repo, base_oid)
    head_entries = tree_entries(repo, head_oid) if scope == "refs" else tree_entries(repo)
    before = worktree_files(repo, head_entries) if scope == "worktree" else None
    root = Path(output).resolve() if output else Path(tempfile.gettempdir()) / ("review-" + uuid.uuid4().hex[:12])
    if root == repo or repo in root.parents:
        raise ReviewError("Run directory must be outside the reviewed repository")
    root.mkdir(parents=True, exist_ok=False)
    export_entries(repo, parent_entries, root / "snapshots/P")
    if scope == "worktree":
        dest = root / "snapshots/H"
        dest.mkdir()
        for name in before:
            target = dest / relative_path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo / relative_path(name), target)
        if before != worktree_files(repo, tree_entries(repo)) or head_entries != tree_entries(repo):
            raise ReviewError(f"Source changed during capture; discard incomplete run: {root}")
    else:
        export_entries(repo, head_entries, root / "snapshots/H")
        if scope == "staged" and head_entries != tree_entries(repo):
            raise ReviewError(f"Index changed during capture; incomplete run: {root}")
    diff = git(root / "snapshots", "diff", "--no-index", "--binary", "--", "P", "H", ok=(0, 1))
    (root / "change.diff").write_bytes(diff)
    manifest = {"schema_version": 1, "run_id": root.name + "-" + uuid.uuid4().hex[:8],
                "created_at": now(), "repository": str(repo), "scope": scope,
                "base_commit": base_oid, "head_commit": head_oid,
                "head_kind": "commit" if scope == "refs" else scope,
                "snapshot_paths": {"P": "snapshots/P", "H": "snapshots/H"},
                "notes": ["Snapshots exclude ignored files and Git metadata.",
                          "File isolation only; use host sandbox for execution."]}
    write_json(root / "run.json", manifest)
    return {"run": str(root), **manifest}


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ReviewError("Experiment ID must be 1-80 letters/digits/underscores/hyphens")
    return value


def new_experiment(root, experiment_id, kind, case):
    manifest = read_json(root / "run.json")
    exp = root / "experiments" / valid_id(experiment_id)
    exp.mkdir(parents=True, exist_ok=False)
    write_json(exp / "case.json", case)
    result = {"schema_version": 1, "run_id": manifest["run_id"], "experiment_id": experiment_id,
              "kind": kind, "started_at": now(), "commands": {}, "outcome": "in_progress"}
    write_json(exp / "result.json", result)
    return exp, result


def copy_regular_tree(source, destination):
    source = Path(source).resolve()
    if not source.is_dir():
        raise ReviewError(f"Missing directory: {source}")
    destination.mkdir(parents=True, exist_ok=False)
    for base, dirs, files in os.walk(source, followlinks=False):
        for name in dirs + files:
            path = Path(base) / name
            reject_link(path)
            rel = relative_path(path.relative_to(source).as_posix())
            target = destination / rel
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                if not path.is_file():
                    raise ReviewError(f"Not a regular file: {path}")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)


def overlay_files(overlay, workspace):
    files = [p for p in overlay.rglob("*") if p.is_file()]
    if not files:
        raise ReviewError("Generated test overlay is empty")
    for src in files:
        target = workspace / src.relative_to(overlay)
        if target.exists():
            raise ReviewError(f"Test overlay would overwrite an existing file: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)


DEPENDENCY_MANIFESTS = {
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
    "requirements.txt", "pyproject.toml", "poetry.lock", "Pipfile.lock", "uv.lock", "Gemfile.lock",
    "composer.lock", "go.sum", "Cargo.lock", "pom.xml", "build.gradle", "build.gradle.kts",
}
PROBE_MANIFESTS = DEPENDENCY_MANIFESTS | {
    "requirements-dev.txt", "setup.py", "setup.cfg", "tox.ini", "pytest.ini", "go.mod", "Cargo.toml",
    "Gemfile", "composer.json", "tsconfig.json",
}
PROBE_RUNTIMES = ("git", "python", "python3", "node", "npm", "npx", "deno", "bun", "java", "mvn",
                  "gradle", "go", "cargo", "dotnet", "ruby", "php")
PROBE_DEPENDENCY_DIRS = ("node_modules", ".venv", "venv", "vendor")


def probe(repo):
    """Report runtimes on PATH, manifests and installed dependency dirs without running project code."""
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise ReviewError(f"Repository directory does not exist: {repo}")
    dependency_dirs = {}
    for name in PROBE_DEPENDENCY_DIRS:
        if (repo / name).is_dir():
            ignored = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", name + "/"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=git_environment())
            dependency_dirs[name] = {"git_ignored": {0: True, 1: False}.get(ignored.returncode)}
    scripts = {}
    if (repo / "package.json").is_file():
        try:
            scripts = read_json(repo / "package.json").get("scripts") or {}
        except (ValueError, AttributeError):
            scripts = {"error": "package.json is not a JSON object"}
    return {
        "repository": str(repo),
        "python": {"executable": sys.executable, "version": sys.version.split()[0],
                   "pytest_importable": importlib.util.find_spec("pytest") is not None},
        # Presence on PATH only; whether the host lets you run a command directly is a separate fact.
        "runtimes_on_path": {name: shutil.which(name) for name in PROBE_RUNTIMES},
        "manifests": sorted(n for n in PROBE_MANIFESTS if (repo / n).is_file()),
        "dependency_dirs": dependency_dirs,
        "package_scripts": scripts,
    }


def dependency_spec(case):
    spec = case.get("dependencies")
    if spec is None:
        return None
    if not isinstance(spec, dict) or spec.get("mode", "copy") not in ("copy", "link"):
        raise ReviewError("dependencies must be an object with mode copy or link")
    paths = spec.get("paths")
    if not isinstance(paths, list) or not paths or any(not isinstance(p, str) for p in paths):
        raise ReviewError("dependencies.paths must be a nonempty list of repository-relative directories")
    return spec.get("mode", "copy"), [relative_path(p) for p in paths]


def changed_manifests(root):
    diff = (root / "change.diff").read_bytes().decode("utf-8", "replace")
    names = re.findall(r"(?m)^diff --git a/[PH]/(\S+) b/[PH]/", diff)
    return sorted({n for n in names if PurePosixPath(n).name in DEPENDENCY_MANIFESTS})


def attach_dependencies(root, exp, spec, workspaces, result):
    """Expose ignored dependency directories (e.g. node_modules) that snapshots exclude."""
    if spec is None:
        return
    mode, paths = spec
    repo = Path(read_json(root / "run.json")["repository"])
    for rel in paths:
        source = (repo / rel).resolve()
        if repo not in source.parents or not source.is_dir():
            raise ReviewError(f"Dependency directory must exist inside the repository: {rel.as_posix()}")
        ignored = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", rel.as_posix() + "/"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=git_environment())
        if ignored.returncode != 0:
            raise ReviewError(f"Only Git-ignored directories can be attached; tracked content comes from snapshots: {rel.as_posix()}")
        for workspace in workspaces.values():
            target = workspace / rel
            if target.exists() or target.is_symlink():
                raise ReviewError(f"Dependency directory would shadow snapshot content: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == "copy":
                shutil.copytree(source, target, symlinks=True)
            elif os.name == "nt":
                import _winapi  # Junctions need no symlink privilege on Windows.
                _winapi.CreateJunction(str(source), str(target))
            else:
                os.symlink(source, target, target_is_directory=True)
    result["dependencies"] = {"mode": mode, "paths": [p.as_posix() for p in paths],
                              "source_repository": str(repo), "manifest_changed": changed_manifests(root)}
    write_json(exp / "result.json", result)


def command_spec(root, case, key):
    command = case.get(key)
    if not isinstance(command, list) or not command or any(not isinstance(v, str) or not v for v in command):
        raise ReviewError(f"{key} must be a nonempty JSON argv array")
    timeout = case.get("timeout_seconds", 120)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 3600:
        raise ReviewError("timeout_seconds must be greater than 0 and at most 3600")
    environment = case.get("environment") or {}
    if not isinstance(environment, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in environment.items()):
        raise ReviewError("environment must map variable names to string values")
    # {repository} lets commands and module paths (e.g. NODE_PATH) read the reviewed checkout's
    # installed dependencies without copying them; {workspace} is expanded per P/M/H at run time.
    repository = read_json(root / "run.json")["repository"]
    command = [arg.replace("{repository}", repository) for arg in command]
    environment = {k: v.replace("{repository}", repository) for k, v in environment.items()}
    return command, timeout, environment


def run_command(command, cwd, timeout, logfile, environment=None):
    logfile.parent.mkdir(parents=True, exist_ok=True)
    record = {"argv": command, "cwd": str(cwd), "started_at": now(), "timeout_seconds": timeout,
              "log": str(logfile), "exit_code": None}
    start = time.monotonic()
    if environment is None:
        environment = {}
    if not isinstance(environment, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in environment.items()):
        raise ReviewError("environment must map variable names to string values")
    overrides = {k: v.replace("{workspace}", str(cwd)) for k, v in environment.items()}
    env = git_environment()
    env.pop("PYTHONPATH", None)
    env.pop("NODE_PATH", None)
    env.update(PYTHONDONTWRITEBYTECODE="1")
    env.update(overrides)
    record["environment_overrides"] = overrides
    options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
               if os.name == "nt" else {"start_new_session": True})
    with logfile.open("wb") as log:
        try:
            process = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, shell=False,
                                       env=env, **options)
        except OSError as exc:
            record.update(status="launch_error", error=str(exc))
            log.write(str(exc).encode("utf-8"))
        else:
            try:
                code = process.wait(timeout=timeout)
                record.update(exit_code=code, status="passed" if code == 0 else "nonzero_exit")
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if process.poll() is None:
                    process.kill()
                process.wait()
                record.update(status="timeout", exit_code=process.returncode)
    record["duration_seconds"] = round(time.monotonic() - start, 3)
    return record


# Deterministic log cues after the paper's rule-based assessor (Tables 2 and 3).
# They are hints for triage, never verdicts; the host still reads the log.
LOG_SIGNALS = {
    "false_positive": {
        "no_tests_executed": r"Ran 0 tests|collected 0 items|no tests ran|No tests found|Tests:\s+0 total",
        "broken_test_runner": r"INTERNALERROR|ImportError while loading conftest|error: unrecognized arguments",
        "module_missing": r"ModuleNotFoundError|Cannot find module",
        "undefined_variable": r"NameError: name '|ReferenceError: \S+ is not defined|cannot find symbol",
        "type_mismatch": r"TypeError: .*(argument|positional|keyword)|AttributeError: .*has no attribute|ImportError: cannot import name|is not a function",
        "web_server_down": r"ConnectionRefused|ECONNREFUSED|Connection refused|Service Unavailable|Name or service not known|getaddrinfo",
        "not_implemented_exception": r"NotImplementedError|UnsupportedOperationException",
        "expecting_particular_calls": r"assert_called|call_count|toHaveBeenCalledTimes|Wanted but not invoked",
        "mock_broken": r"(MagicMock|Mock) object",
        "reflection": r"java\.lang\.reflect|NoSuchMethodException|NoSuchFieldException|IllegalAccessException",
    },
    "true_positive": {
        "unexpected_key_change": r"KeyError|IndexError|index out of (range|bounds)|IndexOutOfBounds",
        "empty_container": r"empty (list|sequence|array|collection|dict)|NoSuchElementException",
        "create_failure": r"InstantiationException|cannot (create|instantiate|construct)",
        "changed_bool": r"True is not false|False is not true|True != False|False != True|expected:? ?<?(true|false)>? but was:? ?<?(false|true)",
        "null_value": r"'NoneType'|NullPointerException|Cannot read propert(y|ies) of (null|undefined)|None != |!= None",
        "access_control": r"PermissionError|Forbidden|AccessDenied|Unauthorized|\b403\b",
    },
}


def scan_log(path):
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    found = {kind: [name for name, pattern in rules.items() if re.search(pattern, text)]
             for kind, rules in LOG_SIGNALS.items()}
    return {kind: names for kind, names in found.items() if names}


def finish(exp, result, outcome):
    signals = {label: scan_log(record["log"]) for label, record in result["commands"].items()}
    result.update(outcome=outcome, completed_at=now(),
                  log_signals={label: s for label, s in signals.items() if s})
    write_json(exp / "result.json", result)
    return result


def execute(exp, result, label, command, cwd, timeout, environment=None):
    record = run_command(command, cwd, timeout, exp / "logs" / (label + ".log"), environment)
    result["commands"][label] = record
    write_json(exp / "result.json", result)
    return record["status"]


def patched_parent(root, exp, patch):
    if re.search(rb"(?m)^(?:new file mode|old mode|new mode) (?:120000|160000)\r?$", patch.read_bytes()):
        raise ReviewError("Mutant patches cannot introduce symlinks or submodules")
    workspace = exp / "workspaces/M"
    copy_regular_tree(root / "snapshots/P", workspace)
    # Disable repository discovery: apply relative to this isolated directory even under another repo.
    command = ["git", "--git-dir=" + str(exp / "nonexistent-git-dir"), "apply", "--check", str(patch)]
    check = subprocess.run(command, cwd=workspace, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=git_environment())
    if check.returncode:
        raise ReviewError("Invalid mutant patch: " + check.stderr.decode("utf-8", "replace"))
    command.remove("--check")
    apply = subprocess.run(command, cwd=workspace, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=git_environment())
    if apply.returncode:
        raise ReviewError("Cannot apply mutant: " + apply.stderr.decode("utf-8", "replace"))
    for base, dirs, files in os.walk(workspace, followlinks=False):
        for name in dirs + files:
            reject_link(Path(base) / name)
    return workspace


def screen_mutant(root, case, experiment_id):
    command, timeout, environment = command_spec(root, case, "baseline_command")
    dependencies = dependency_spec(case)
    exp, result = new_experiment(root, experiment_id, "mutant-screen", case)
    patch = exp / "mutant.patch"
    shutil.copyfile(Path(case["mutant_patch"]).resolve(), patch)
    parent = exp / "workspaces/P"
    copy_regular_tree(root / "snapshots/P", parent)
    try:
        mutant = patched_parent(root, exp, patch)
    except ReviewError as exc:
        result["error"] = str(exc)
        return finish(exp, result, "mutant_rejected")
    attach_dependencies(root, exp, dependencies, {"P": parent, "M": mutant}, result)
    p_status = execute(exp, result, "P-existing", command, parent, timeout, environment)
    if p_status != "passed":
        return finish(exp, result, "invalid_baseline")
    m_status = execute(exp, result, "M-existing", command, mutant, timeout, environment)
    return finish(exp, result, "survived_existing_tests" if m_status == "passed" else "mutant_rejected")


def flaky_reruns(case):
    reruns = case.get("flaky_reruns", 1)
    if isinstance(reruns, bool) or not isinstance(reruns, int) or not 0 <= reruns <= 10:
        raise ReviewError("flaky_reruns must be an integer from 0 to 10")
    return reruns


def stable(exp, result, expected, command, workspaces, timeout, environment, reruns):
    """Re-run a differential observation; any changed status marks it flaky."""
    for i in range(1, reruns + 1):
        for label, status in expected.items():
            if execute(exp, result, f"{label}-test-rerun{i}", command, workspaces[label],
                       timeout, environment) != status:
                return False
    return True


def compare(root, case, experiment_id):
    command, timeout, environment = command_spec(root, case, "test_command")
    reruns = flaky_reruns(case)
    dependencies = dependency_spec(case)
    strategy = case.get("strategy")
    if strategy not in ("dodgy-diff", "intent-aware", "hypothesis"):
        raise ReviewError("strategy must be dodgy-diff, intent-aware or hypothesis")
    head_only = case.get("parent_applicable", True) is False
    if head_only and strategy != "hypothesis":
        raise ReviewError("parent_applicable=false is only valid for the hypothesis strategy")
    screen = None
    if strategy == "intent-aware":
        screen = root / "experiments" / valid_id(case.get("screen_id"))
        prior = read_json(screen / "result.json")
        manifest = read_json(root / "run.json")
        if (prior.get("kind") != "mutant-screen" or prior.get("outcome") != "survived_existing_tests"
                or prior.get("run_id") != manifest["run_id"]):
            raise ReviewError("Intent-aware requires a successful mutant screen from the same run")
    exp, result = new_experiment(root, experiment_id, strategy, case)
    overlay = exp / "test-overlay"
    copy_regular_tree(Path(case["test_overlay"]), overlay)
    workspaces = {}
    for label in (("H",) if head_only else ("P", "H")):
        workspace = exp / "workspaces" / label
        copy_regular_tree(root / "snapshots" / label, workspace)
        workspaces[label] = workspace
    if screen:
        patch = exp / "mutant.patch"
        shutil.copyfile(screen / "mutant.patch", patch)
        workspaces["M"] = patched_parent(root, exp, patch)
        result["screen_id"] = case["screen_id"]
    for workspace in workspaces.values():
        overlay_files(overlay, workspace)
    # After the overlay, so generated tests can never be written through a linked directory.
    attach_dependencies(root, exp, dependencies, workspaces, result)
    if head_only:
        # New behavior has no parent counterpart; the oracle is the stated requirement.
        h_status = execute(exp, result, "H-test", command, workspaces["H"], timeout, environment)
        if h_status == "nonzero_exit":
            ok = stable(exp, result, {"H": "nonzero_exit"}, command, workspaces, timeout, environment, reruns)
            return finish(exp, result, "head_only_failure" if ok else "flaky")
        return finish(exp, result, "head_only_pass" if h_status == "passed" else "inconclusive")
    p_status = execute(exp, result, "P-test", command, workspaces["P"], timeout, environment)
    if p_status != "passed":
        return finish(exp, result, "invalid_parent_test")
    if screen:
        # Killing M is recorded, but H runs regardless: P-pass/H-fail is a weak catch either way.
        m_status = execute(exp, result, "M-test", command, workspaces["M"], timeout, environment)
        result["mutant_killed"] = {"passed": False, "nonzero_exit": True}.get(m_status)
    h_status = execute(exp, result, "H-test", command, workspaces["H"], timeout, environment)
    if h_status == "nonzero_exit":
        ok = stable(exp, result, {"P": "passed", "H": "nonzero_exit"}, command, workspaces, timeout, environment, reruns)
        return finish(exp, result, "differential_failure" if ok else "flaky")
    return finish(exp, result, "no_differential_failure" if h_status == "passed" else "inconclusive")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("prepare", help="Capture P/H without modifying the source repository")
    p.add_argument("--repo", required=True)
    p.add_argument("--scope", choices=["worktree", "staged", "refs"], default="worktree")
    p.add_argument("--base")
    p.add_argument("--head")
    p.add_argument("--output")
    p = sub.add_parser("probe", help="Report runtimes, manifests and installed dependency dirs")
    p.add_argument("--repo", required=True)
    for name in ("screen-mutant", "compare"):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True)
        p.add_argument("--case", required=True)
        p.add_argument("--id", required=True)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            result = prepare(args.repo, args.scope, args.base, args.head, args.output)
        elif args.action == "probe":
            result = probe(args.repo)
        else:
            root, case = Path(args.run).resolve(), read_json(args.case)
            fn = screen_mutant if args.action == "screen-mutant" else compare
            result = fn(root, case, args.id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ReviewError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
