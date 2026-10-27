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
    work = args.output / name
    work.mkdir()
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
    report = {
        "project": name,
        "revision": revision,
        "tool_version": __version__,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "setup": setup,
        "scope": profile["tests"],
        "variants": {},
    }
    if setup["exit"] != 0:
        return report
    run([python, "-m", "pip", "freeze"], work, work / "dependencies.txt", env, args.timeout)
    baseline = None
    for mode in args.modes:
        for seed in args.seeds:
            label = f"{mode}-{seed}"
            print(f"{name}: planning {label}", flush=True)
            plan = build_plan(source, mode=mode, seed=seed, max_depth=3)
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
                continue
            output = apply_plan(plan, work / label)
            module_aliases = {}
            for record in plan.inspection.files:
                if record.module and plan.mapping[record.path] != record.path:
                    target = Path(plan.mapping[record.path]).with_suffix("").as_posix()
                    module_aliases[target.replace("/", ".")] = record.module
                    for base in profile.get("source_paths", []):
                        if target.startswith(base + "/"):
                            module_aliases[target[len(base) + 1 :].replace("/", ".")] = (
                                record.module
                            )
            snapshots = [("original", output / "original")] if baseline is None else []
            snapshots.append((label, output / "shuffled"))
            for snapshot_name, snapshot in snapshots:
                print(f"{name}: testing {snapshot_name}", flush=True)
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
                if snapshot_name == "original":
                    baseline = result
                    report["original"] = result
                else:
                    variant["tests"] = result
                    variant["comparison"] = compare(baseline, result)
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
    baseline = report.get("original", {})
    if baseline.get("exit") != 0 or baseline.get("source_import", {}).get("exit") != 0:
        return False
    for variant in report["variants"].values():
        result = variant.get("tests", {})
        if (
            not variant["plan_ok"]
            or result.get("exit") != 0
            or result.get("source_import", {}).get("exit") != 0
        ):
            return False
        comparison = variant.get("comparison", {})
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
    parser.add_argument("--repos-root", type=Path, default=Path("repos/python/sources"))
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
        "--modes", nargs="+", choices=("flat", "random"), default=["flat", "random"]
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
