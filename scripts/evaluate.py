"""Reproducible original/transformed comparisons; target execution is explicit.

Profiles pin source commits and dependencies. Only this opt-in harness executes
upstream code: inspection and planning remain read-only.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import time
import venv
import xml.etree.ElementTree as ET
from pathlib import Path

from reposhuffle import ShuffleError, __version__, apply_plan, build_plan


def junit_result(
    path: Path,
    exit_code: int | None,
    snapshot: Path | None = None,
    module_aliases: dict[str, str] | None = None,
) -> dict:
    if not path.is_file() or exit_code not in (0, 1):
        return {"complete": False, "counts": {}, "cases": {}}
    try:
        document = ET.parse(path)
    except ET.ParseError:
        return {"complete": False, "counts": {}, "cases": {}}
    cases: dict[str, list[str]] = {}
    counts: collections.Counter = collections.Counter()
    for case in document.iter("testcase"):
        classname, name = case.get("classname", ""), case.get("name", "")
        for physical, logical in sorted(
            (module_aliases or {}).items(), key=lambda pair: -len(pair[0])
        ):
            if classname == physical or classname.startswith(physical + "."):
                classname = logical + classname[len(physical) :]
                break
        key = classname + "::" + name
        if snapshot is not None:
            for root in (str(snapshot), snapshot.as_posix()):
                key = key.replace(root.replace("\\", "\\\\"), "<snapshot>").replace(
                    root, "<snapshot>"
                )
        outcome = next(
            (name for name in ("failure", "error", "skipped") if case.find(name) is not None),
            "passed",
        )
        counts[outcome] += 1
        cases.setdefault(key, []).append(outcome)
    return {"complete": bool(cases), "counts": dict(counts), "cases": cases}


def compare(original: dict, shuffled: dict) -> dict:
    if not original.get("complete") or not shuffled.get("complete"):
        return {"comparable": False}
    before, after = original["cases"], shuffled["cases"]
    failures = lambda outcomes: sum(value in {"failure", "error"} for value in outcomes)
    return {
        "comparable": True,
        "added_nodes": sorted(set(after) - set(before)),
        "removed_nodes": sorted(set(before) - set(after)),
        "changed_nodes": sorted(
            key for key in before.keys() & after.keys() if sorted(before[key]) != sorted(after[key])
        ),
        "new_failures": sorted(
            key for key in after if failures(after[key]) > failures(before.get(key, []))
        ),
    }


def run(argv: list[str], cwd: Path, log: Path, env: dict, timeout: int) -> dict:
    start = time.monotonic()
    with log.open("w", encoding="utf-8") as stream:
        try:
            process = subprocess.run(
                argv,
                cwd=cwd,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
            result = {"exit": process.returncode, "timeout": False}
        except subprocess.TimeoutExpired:
            result = {"exit": None, "timeout": True}
    return {**result, "seconds": round(time.monotonic() - start, 2)}


def python_in(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def initialize_submodules(source: Path, timeout: int) -> None:
    if (source / ".gitmodules").is_file():
        subprocess.run(
            ["git", "-C", str(source), "submodule", "update", "--init", "--recursive"],
            check=True,
            timeout=timeout,
        )


class PythonRunner:
    language = "python"
    required_evidence = ("source_import",)

    def setup(self, profile, args, work):
        environment = work / "environment"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = str(python_in(environment))
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
                "PYTHONUTF8": "1",
                "DISABLE_SQLALCHEMY_CEXT": "1",
            }
        )
        env.update(profile.get("env", {}))
        setup = run(
            [python, "-m", "pip", "install", *profile["dependencies"]],
            work,
            work / "dependencies.log",
            env,
            args.timeout,
        )
        self.python, self.env = python, env
        if setup["exit"] == 0:
            run([python, "-m", "pip", "freeze"], work, work / "dependencies.txt", env, args.timeout)
        return setup

    def aliases(self, plan, profile):
        module_aliases = {}
        for record in plan.inspection.files:
            if record.module and plan.mapping[record.path] != record.path:
                target = Path(plan.mapping[record.path]).with_suffix("").as_posix()
                module_aliases[target.replace("/", ".")] = record.module
                for base in profile.get("source_paths", []):
                    if target.startswith(base + "/"):
                        module_aliases[target[len(base) + 1 :].replace("/", ".")] = record.module
        return module_aliases

    def execute(self, snapshot, snapshot_name, work, profile, args, module_aliases):
        python, env = self.python, self.env
        environment = work / "environment"
        evidence = {}
        if args.artifacts:
            wheels = work / f"{snapshot_name}-wheels"
            wheels.mkdir()
            built = run(
                [
                    python,
                    "-m",
                    "pip",
                    "wheel",
                    "--no-deps",
                    "--wheel-dir",
                    str(wheels),
                    str(snapshot),
                ],
                work,
                work / f"{snapshot_name}-build.log",
                env,
                args.timeout,
            )
            evidence["wheel"] = built
            if built["exit"] == 0:
                wheel = next(wheels.glob("*.whl"))
                installed = run(
                    [
                        python,
                        "-m",
                        "pip",
                        "install",
                        "--no-deps",
                        "--force-reinstall",
                        str(wheel),
                    ],
                    work,
                    work / f"{snapshot_name}-install.log",
                    env,
                    args.timeout,
                )
                evidence["install"] = installed
                if installed["exit"] == 0:
                    code = (
                        "import importlib; from pathlib import Path; "
                        f"m=importlib.import_module({profile['module']!r}); "
                        f"assert Path(m.__file__).resolve().is_relative_to(Path({str(environment)!r}).resolve()); "
                        "print(m.__name__)"
                    )
                    evidence["installed_import"] = run(
                        [python, "-I", "-B", "-c", code],
                        work,
                        work / f"{snapshot_name}-wheel-import.log",
                        env,
                        args.timeout,
                    )
        test_env = env.copy()
        test_env["PYTHONPATH"] = os.pathsep.join(
            [
                str(snapshot),
                *(str(snapshot / path) for path in profile.get("source_paths", [])),
            ]
        )
        code = (
            "import importlib; from pathlib import Path; "
            f"m=importlib.import_module({profile['module']!r}); "
            f"assert Path(m.__file__).resolve().is_relative_to(Path({str(snapshot)!r}).resolve()); print(m.__name__)"
        )
        evidence["source_import"] = run(
            [python, "-B", "-c", code],
            snapshot,
            work / f"{snapshot_name}-import.log",
            test_env,
            args.timeout,
        )
        xml = work / f"{snapshot_name}.xml"
        if profile.get("runner") == "django":
            command = [
                python,
                "-B",
                "tests/runtests.py",
                *profile["tests"],
                "--parallel",
                "1",
                "--verbosity",
                "1",
            ]
        else:
            command = [
                python,
                "-B",
                "-m",
                "pytest",
                "-q",
                *profile["tests"],
                "--tb=short",
                f"--rootdir={snapshot}",
                f"--confcutdir={snapshot}",
                "-p",
                "no:cacheprovider",
                f"--basetemp={work / (snapshot_name + '-temp')}",
                f"--junitxml={xml}",
            ]
        executed = run(
            command, snapshot, work / f"{snapshot_name}-tests.log", test_env, args.timeout
        )
        result = {
            **executed,
            **junit_result(
                xml,
                executed["exit"],
                snapshot,
                module_aliases if snapshot_name != "original" else None,
            ),
            **evidence,
        }
        if profile.get("runner") == "django":
            text = (work / f"{snapshot_name}-tests.log").read_text(encoding="utf-8")
            match = re.search(r"Ran (\d+) tests", text)
            result["counts"] = {"run": int(match[1])} if match else {}
            skipped = re.search(r"skipped=(\d+)", text)
            if skipped:
                result["counts"]["skipped"] = int(skipped[1])
        return result


def junit_results(paths: list[Path], exit_code: int | None, snapshot: Path) -> dict:
    cases = {}
    counts = collections.Counter()
    complete = bool(paths)
    for path in sorted(paths):
        result = junit_result(path, exit_code, snapshot)
        complete = complete and result["complete"]
        scope = path.parent.relative_to(snapshot).as_posix()
        for node, outcomes in result["cases"].items():
            cases.setdefault(scope + "::" + node, []).extend(outcomes)
        counts.update(result["counts"])
    return {"complete": complete, "counts": dict(counts), "cases": cases}


def jar_inventory(snapshot: Path, patterns: list[str]) -> dict:
    import zipfile

    inventory = {}
    for path in sorted({path for pattern in patterns for path in snapshot.glob(pattern)}):
        if path.name.endswith(("-sources.jar", "-javadoc.jar", "-tests.jar", "-test-fixtures.jar")):
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                classes = sorted(name for name in archive.namelist() if name.endswith(".class"))
        except zipfile.BadZipFile as exc:
            raise ValueError(f"Invalid JAR artifact: {path.relative_to(snapshot)}") from exc
        if classes:
            inventory[path.relative_to(snapshot).as_posix()] = classes
    return inventory


class JavaRunner:
    language = "java"
    required_evidence = ("build_tool", "artifact_inventory")

    def setup(self, profile, args, work):
        self.env = os.environ.copy()
        self.env.pop("CLASSPATH", None)
        self.env.update(profile.get("env", {}))
        result = run(["java", "-version"], work, work / "java-version.log", self.env, args.timeout)
        text = (work / "java-version.log").read_text(encoding="utf-8")
        version = re.search(r'(?:openjdk|java) version "(\d+)', text)
        if result["exit"] == 0 and (version is None or int(version[1]) != profile["jdk"]):
            result["exit"] = 1
            result["error"] = "JDK major version differs from profile"
        return result

    def aliases(self, plan, profile):
        return {}

    def command(self, snapshot, profile):
        if profile["build_system"] == "maven":
            executable = shutil.which("mvn.cmd" if os.name == "nt" else "mvn")
            if not executable:
                raise ValueError("Maven is not installed")
            return [executable, "-B", "-ntp"]
        wrapper = "gradlew.bat" if os.name == "nt" else "gradlew"
        if not (snapshot / wrapper).is_file():
            raise ValueError("Pinned Gradle wrapper is missing")
        return [str(snapshot / wrapper)] if os.name == "nt" else ["bash", "./gradlew"]

    def execute(self, snapshot, snapshot_name, work, profile, args, module_aliases):
        command = self.command(snapshot, profile)
        tool_log = work / f"{snapshot_name}-build-tool.log"
        tool = run([*command, "--version"], snapshot, tool_log, self.env, args.timeout)
        version = profile["build_version"]
        text = tool_log.read_text(encoding="utf-8")
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
        if tool["exit"] == 0 and not re.search(
            r"(?:Apache Maven|Gradle) " + re.escape(version) + r"(?:\s|$)", text
        ):
            tool["exit"] = 1
            tool["error"] = "Build tool version differs from profile"
        if tool["exit"] != 0:
            return {"exit": 1, "complete": False, "counts": {}, "cases": {}, "build_tool": tool}
        executed = run(
            [*command, *profile["tasks"]],
            snapshot,
            work / f"{snapshot_name}-tests.log",
            self.env,
            args.timeout,
        )
        paths = sorted({path for pattern in profile["reports"] for path in snapshot.glob(pattern)})
        results = junit_results(paths, executed["exit"], snapshot)
        report_root = work / f"{snapshot_name}-reports"
        for path in paths:
            target = report_root / path.relative_to(snapshot)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        inventory = jar_inventory(snapshot, profile["artifacts"])
        return {
            **executed,
            **results,
            "build_tool": tool,
            "artifact_inventory": {"exit": 0 if inventory else 1, "jars": inventory},
        }


_RUNNERS = {"python": PythonRunner, "java": JavaRunner}


def evaluate(name: str, profile: dict, args) -> dict:
    source = (args.repos_root / name).resolve()
    if not source.exists() and args.clone:
        source.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--no-checkout", profile["url"], str(source)],
            check=True,
            timeout=args.timeout,
        )
        subprocess.run(
            ["git", "-C", str(source), "checkout", "--detach", profile["revision"]],
            check=True,
            timeout=args.timeout,
        )
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != profile["revision"]:
        raise ValueError(f"{name}: commit differs from the pinned profile")
    if subprocess.check_output(
        ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"], text=True
    ).strip():
        raise ValueError(f"{name}: tracked upstream files are modified")
    initialize_submodules(source, args.timeout)
    work = args.output / name
    work.mkdir()
    language = profile.get("language", "python")
    runner = _RUNNERS[language]()
    report = {
        "project": name,
        "revision": revision,
        "tool_version": __version__,
        "language": language,
        "profile": profile,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "setup": runner.setup(profile, args, work),
        "scope": profile["tests"] if language == "python" else profile["tasks"],
        "variants": {},
    }
    if report["setup"]["exit"] != 0:
        (work / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report
    baseline = None
    modes = args.modes or (["random"] if language == "java" else ["flat", "random"])
    for mode in modes:
        for seed in args.seeds:
            label = f"{mode}-{seed}"
            print(f"{name}: planning {label}", flush=True)
            plan = build_plan(
                source,
                language=language,
                source_dirs=profile.get("source_dirs"),
                mode=mode,
                seed=seed,
                max_depth=3,
            )
            variant = {
                "plan_ok": plan.ok,
                "layout": plan.layout,
                "compatibility": plan.compatibility,
            }
            report["variants"][label] = variant
            if not plan.ok:
                variant["diagnostics"] = [
                    vars(item) for item in plan.diagnostics + plan.inspection.diagnostics
                ]
            else:
                if not plan.layout["moved_files"]:
                    raise ValueError("No implementation files were relocated")
                output = apply_plan(plan, work / label)
                aliases = runner.aliases(plan, profile)
                snapshots = [("original", output / "original")] if baseline is None else []
                snapshots.append((label, output / "shuffled"))
                for snapshot_name, snapshot in snapshots:
                    print(f"{name}: testing {snapshot_name}", flush=True)
                    result = runner.execute(
                        snapshot,
                        snapshot_name,
                        work,
                        profile,
                        args,
                        aliases if snapshot_name != "original" else {},
                    )
                    if snapshot_name == "original":
                        baseline = result
                        report["original"] = result
                    else:
                        variant["tests"] = result
                        variant["comparison"] = compare(baseline, result)
                        if "artifact_inventory" in baseline:
                            variant["comparison"]["artifact_classes_equal"] = baseline[
                                "artifact_inventory"
                            ].get("jars") == result.get("artifact_inventory", {}).get("jars")
                        if profile.get("runner") == "django":
                            variant["comparison"]["summary_equal"] = (
                                baseline["counts"] == result["counts"]
                                and baseline["exit"] == result["exit"]
                            )
            (work / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def acceptable(report: dict) -> bool:
    if report.get("setup", {}).get("exit") != 0:
        return False
    runner = _RUNNERS.get(report.get("language", "python"))
    if runner is None or not report.get("variants"):
        return False
    required = runner.required_evidence
    baseline = report.get("original", {})
    if baseline.get("exit") != 0 or any(baseline.get(key, {}).get("exit") != 0 for key in required):
        return False
    if "wheel" in baseline and any(
        baseline.get(key, {}).get("exit") != 0 for key in ("wheel", "install", "installed_import")
    ):
        return False
    for variant in report["variants"].values():
        result = variant.get("tests", {})
        if (
            not variant["plan_ok"]
            or result.get("exit") != 0
            or any(result.get(key, {}).get("exit") != 0 for key in required)
        ):
            return False
        comparison = variant.get("comparison", {})
        if not comparison.get("comparable") and comparison.get("summary_equal") is not True:
            return False
        if report.get("language") == "java" and (
            comparison.get("artifact_classes_equal") is not True
            or variant.get("layout", {}).get("moved_files", 0) <= 0
        ):
            return False
        if comparison.get("summary_equal") is False:
            return False
        if comparison.get("comparable") and any(
            comparison[key]
            for key in ("added_nodes", "removed_nodes", "changed_nodes", "new_failures")
        ):
            return False
        if "wheel" in result and any(
            result.get(key, {}).get("exit") != 0 for key in ("wheel", "install", "installed_import")
        ):
            return False
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=Path, default=Path("evaluation/python.json"))
    parser.add_argument(
        "--repos-root", type=Path, help="Source cache; default: repos/<language>/sources"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="New evidence directory; never overwritten"
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Add new projects to an existing report; never replace project results",
    )
    parser.add_argument("--projects", nargs="+")
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=("flat", "random"),
        help="Default: flat/random for Python, random only for Java",
    )
    parser.add_argument("--seeds", nargs="+", type=int, default=[42])
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument(
        "--artifacts",
        action="store_true",
        help="Build/install wheels and import outside source trees",
    )
    parser.add_argument(
        "--clone", action="store_true", help="Clone missing repositories at pinned commits"
    )
    args = parser.parse_args(argv)
    if args.jobs < 1 or args.timeout < 1:
        parser.error("jobs and timeout must be positive")
    profiles = json.loads(args.profiles.read_text(encoding="utf-8"))
    selected = args.projects or list(profiles)
    if not selected or any(name not in profiles for name in selected):
        parser.error("Unknown or empty project selection")
    languages = {profiles[name].get("language", "python") for name in selected}
    if not languages <= _RUNNERS.keys():
        parser.error("Unknown profile language")
    if args.modes and "java" in languages and "flat" in args.modes:
        parser.error("Java profiles support only random mode")
    if args.repos_root is None:
        if len(languages) != 1:
            parser.error("Mixed-language profiles require --repos-root")
        args.repos_root = Path("repos") / next(iter(languages)) / "sources"
    for name in selected:
        if name in {".", ".."} or re.fullmatch(r"[\w.-]+", name) is None:
            parser.error("Unsafe project name")
        profile = profiles[name]
        if profile.get("language") == "java":
            required = {"jdk", "build_system", "build_version", "tasks", "reports", "artifacts"}
            if not required <= profile.keys() or profile["build_system"] not in {"maven", "gradle"}:
                parser.error(f"Incomplete Java profile: {name}")
            if "clean" not in profile["tasks"]:
                parser.error(f"Java profile must clean build outputs: {name}")
    args.output = args.output.resolve()
    results = {}
    if args.append:
        summary = args.output / "summary.json"
        if not summary.is_file():
            parser.error("--append requires an existing evaluation summary")
        results = json.loads(summary.read_text(encoding="utf-8"))
        for name, report in results.items():
            if (
                name not in profiles
                or report.get("revision") != profiles[name]["revision"]
                or report.get("tool_version") != __version__
                or ("profile" in report and report["profile"] != profiles[name])
            ):
                parser.error("Existing results do not match the current profiles and tool version")
        if any(name in results or (args.output / name).exists() for name in selected):
            parser.error("Append cannot overwrite an existing project's results")
    else:
        args.output.mkdir(parents=True, exist_ok=False)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(evaluate, name, profiles[name], args): name for name in selected}
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except (OSError, ValueError, subprocess.SubprocessError, ShuffleError) as exc:
                results[name] = {"error": type(exc).__name__, "message": str(exc)}
            (args.output / "summary.json").write_text(
                json.dumps(results, indent=2) + "\n", encoding="utf-8"
            )
            print(
                f"{name}: {'matched' if acceptable(results[name]) else 'review required'}",
                flush=True,
            )
    return 0 if all(acceptable(report) for report in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
