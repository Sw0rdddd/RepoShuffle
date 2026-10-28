from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from reposhuffle import ShuffleError, apply_plan, build_plan, inspect_project
from reposhuffle.cli import main
from reposhuffle.layout import NumberedLayout

POM = """<project xmlns="http://maven.apache.org/POM/4.0.0">
<modelVersion>4.0.0</modelVersion><groupId>demo</groupId>
<artifactId>demo</artifactId><version>1</version>
<dependencies><dependency><groupId>junit</groupId><artifactId>junit</artifactId>
<version>4.13.2</version><scope>test</scope></dependency></dependencies>
<properties><maven.compiler.release>17</maven.compiler.release></properties>
<build><plugins><plugin><artifactId>maven-compiler-plugin</artifactId>
<version>3.13.0</version></plugin></plugins></build></project>"""


def sources(build="pom.xml"):
    return {
        build: POM
        if build == "pom.xml"
        else (
            'plugins { id("java") }\nrepositories { mavenCentral() }\n'
            'dependencies { testImplementation("junit:junit:4.13.2") }\n'
        ),
        "src/main/java/demo/Main.java": """package demo;
public class Main {
    public static void main(String[] args) throws Exception {
        if (Helper.value() != 42) throw new AssertionError("package access");
        if (Class.forName("demo.Main") != Main.class) throw new AssertionError("identity");
        try (var stream = Main.class.getResourceAsStream("value.txt")) {
            if (stream == null || !new String(stream.readAllBytes()).equals("resource"))
                throw new AssertionError("resource");
        }
        System.out.println("ok");
    }
}
""",
        "src/main/java/demo/Helper.java": "package demo; class Helper { static int value() { return 42; } }\n",
        "src/main/java/other/Main.java": "package other; public class Main {}\n",
        "src/main/java/demo/package-info.java": "/** Demo. */ package demo;\n",
        "src/main/resources/demo/value.txt": "resource",
        "src/test/java/demo/Test.java": (
            "package demo; public class Test { @org.junit.Test public void value() { "
            "org.junit.Assert.assertEquals(42, Helper.value()); } }\n"
        ),
        "tools/helper.py": "print('not Java')\n",
    }


@pytest.mark.parametrize("build", ["pom.xml", "build.gradle", "build.gradle.kts"])
def test_java_relocates_bytes_with_shared_layout(project, build):
    source = project(sources(build))
    plan = build_plan(source, seed=42)
    assert plan.ok, plan.to_dict()
    assert plan.inspection.language == "java"
    assert plan.to_dict() == build_plan(source, seed=42).to_dict()
    moving = [record for record in plan.inspection.files if record.role == "source"]
    assert len(moving) == 3
    zone = "src/main/java/_internal_code"
    expected = NumberedLayout(42, 3, 0.7).targets(
        zone, {record.path: PurePosixPath(record.path).name for record in moving}
    )
    assert {record.path: plan.mapping[record.path] for record in moving} == expected
    for record in plan.inspection.files:
        target = plan.mapping[record.path]
        assert plan.contents[target] == (source / record.path).read_bytes()
        if record.role == "source":
            assert target != record.path
            assert PurePosixPath(target).name == PurePosixPath(record.path).name
        else:
            assert target == record.path
    assert not plan.generated
    output = apply_plan(plan)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["language"] == "java"
    assert "original_pythonpath" not in manifest
    assert "original_pythonpath" not in manifest["configuration"]
    assert "syntax-compilation" not in manifest["static_validation"]["checks"]
    assert "java-source-bytes" in manifest["static_validation"]["checks"]
    for record in moving:
        assert not (output / "shuffled" / record.path).exists()
        assert (output / "original" / record.path).read_bytes() == (
            source / record.path
        ).read_bytes()


def test_java_byte_preserving_moves_remain_disk_backed(project, monkeypatch):
    source = project(sources())
    read_bytes = Path.read_bytes

    def read(path):
        assert path.suffix != ".java", "byte-preserving relocation loaded a Java source"
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    plan = build_plan(source, seed=42)
    assert plan.ok, plan.to_dict()
    apply_plan(plan)


def test_java_flat_is_rejected_in_api_and_cli(project, capsys):
    source = project(sources())
    with pytest.raises(ShuffleError, match="java supports only random"):
        build_plan(source, mode="flat")
    assert main(["shuffle", str(source), "--language", "java", "--mode", "flat", "--dry-run"]) == 2
    assert "java supports only random" in capsys.readouterr().err


def test_mixed_language_requires_choice_and_preserves_other_sources(project):
    source = project(
        {
            **sources(),
            "pyproject.toml": "[project]\nname='demo'\n",
            "pkg/__init__.py": "",
            "pkg/core.py": "VALUE=1\n",
        }
    )
    with pytest.raises(ShuffleError, match="Multiple languages"):
        build_plan(source)
    for language, other in (("java", "pkg/core.py"), ("python", "src/main/java/demo/Main.java")):
        plan = build_plan(source, language=language, seed=1)
        assert plan.ok, plan.to_dict()
        assert plan.mapping[other] == other
        assert plan.contents[other] == (source / other).read_bytes()


def test_mixed_language_cli_requires_explicit_choice(project, capsys):
    source = project({**sources(), "pyproject.toml": "[project]\nname='demo'\n"})
    assert main(["inspect", str(source)]) == 2
    assert "Multiple languages" in capsys.readouterr().err
    assert main(["inspect", str(source), "--language", "java"]) == 0
    assert json.loads(capsys.readouterr().out)["language"] == "java"


def test_java_multimodule_and_build_outputs(project):
    files = {"settings.gradle.kts": 'include("a", "b")\n'}
    for name in ("a", "b"):
        files[f"{name}/src/main/java/demo/Main.java"] = "package demo; public class Main {}"
        files[f"{name}/build/classes/Old.class"] = b"old"
    files["a/src/main/java/build/Valid.java"] = "package build; public class Valid {}"
    files[".gradle/cache"] = "cache"
    source = project(files)
    plan = build_plan(source, seed=42)
    assert plan.ok, plan.to_dict()
    assert len(plan.inspection.source_units) == 2
    for record in plan.inspection.files:
        if record.role == "source":
            assert plan.mapping[record.path].startswith(record.owner + "/")
    assert "a/build/" in plan.inspection.excluded
    assert ".gradle/" in plan.inspection.excluded
    assert plan.mapping["a/src/main/java/build/Valid.java"] != "a/src/main/java/build/Valid.java"
    apply_plan(plan)


def test_production_package_names_are_not_mistaken_for_test_scopes(project):
    files = sources()
    for package in ("test", "tests", "fixtures", "generated"):
        files[f"src/main/java/demo/{package}/Value.java"] = (
            f"package demo.{package}; public class Value {{}}"
        )
    source = project(files)
    plan = build_plan(source, seed=42)
    assert plan.ok, plan.to_dict()
    for package in ("test", "tests", "fixtures", "generated"):
        path = f"src/main/java/demo/{package}/Value.java"
        assert plan.mapping[path] != path
    assert plan.mapping["src/test/java/demo/Test.java"] == "src/test/java/demo/Test.java"


def test_explicit_compiler_root_does_not_escalate_auxiliary_java_inputs(project):
    files = sources()
    for scope in ("test", "testFixtures", "integrationTest", "it"):
        files[f"src/{scope}/java/demo/Data.java"] = "not Java; a fixture"
    files["src/main/resources/demo/Data.java"] = "not Java; a resource"
    source = project(files)
    plan = build_plan(source, source_dirs=["src/main/java"], seed=42)
    assert plan.ok, plan.to_dict()
    for path in files:
        if "/Data.java" in path or path == "src/test/java/demo/Test.java":
            assert plan.mapping[path] == path


def test_java_configuration_rejects_non_table_language_section(project):
    source = project({**sources(), "reposhuffle.toml": 'java = ["invalid"]\n'})
    with pytest.raises(ShuffleError, match="java must be a table"):
        build_plan(source)


def test_java_selection_does_not_escalate_test_scope(project):
    source = project({**sources(), ".gitignore": "src/main/java/other/\n"})
    plan = build_plan(source, include=["**"], exclude=["src/main/java/demo/Helper.java"])
    assert plan.ok, plan.to_dict()
    assert plan.mapping["src/main/java/other/Main.java"] == "src/main/java/other/Main.java"
    assert plan.mapping["src/main/java/demo/Helper.java"] == "src/main/java/demo/Helper.java"
    assert plan.mapping["src/test/java/demo/Test.java"] == "src/test/java/demo/Test.java"


def test_custom_source_roots_are_explicit_not_inferred_from_arbitrary_dsl(project):
    source = project(
        {
            "build.gradle": "sourceSets.main.java.srcDirs = ['production']",
            "production/demo/Main.java": "package demo; public class Main {}",
        }
    )
    assert not build_plan(source).ok
    plan = build_plan(source, source_dirs=["production"], seed=1)
    assert plan.ok, plan.to_dict()
    assert plan.mapping["production/demo/Main.java"] != "production/demo/Main.java"
    assert plan.contents["build.gradle"] == (source / "build.gradle").read_bytes()
    (source / "reposhuffle.toml").write_text(
        '[java]\nsource-dirs=["production"]\n', encoding="utf-8"
    )
    assert build_plan(source).ok
    assert not build_plan(source, source_dirs=["../outside"]).ok


def test_maven_filters_preserve_membership_or_block(project):
    pom = POM.replace(
        "<version>3.13.0</version>",
        "<version>3.13.0</version><configuration><excludes><exclude>module-info.java</exclude></excludes></configuration>",
    )
    source = project(
        {**sources(), "pom.xml": pom, "src/main/java/module-info.java": "module demo {}"}
    )
    assert build_plan(source).ok
    (source / "pom.xml").write_text(pom.replace("module-info.java", "demo/**"), encoding="utf-8")
    plan = build_plan(source)
    assert not plan.ok
    assert any("compiler source selection" in diagnostic.message for diagnostic in plan.diagnostics)


@pytest.mark.parametrize("root", ["src", "src/main", "src/main/java/demo"])
def test_explicit_root_must_match_known_maven_compiler_boundary(project, root):
    source = project(sources())
    plan = build_plan(source, source_dirs=[root], seed=42)
    assert not plan.ok
    assert any("compiler root" in item.message for item in plan.inspection.diagnostics)


def test_maven_literal_custom_and_additional_roots_keep_actual_boundaries(project):
    pom = POM.replace("<build>", "<build><sourceDirectory>production</sourceDirectory>").replace(
        "</plugins>",
        "<plugin><artifactId>build-helper-maven-plugin</artifactId><executions><execution><goals><goal>add-source</goal></goals><configuration><sources><source>additional</source></sources></configuration></execution></executions></plugin></plugins>",
    )
    source = project(
        {
            "pom.xml": pom,
            "production/demo/Main.java": "package demo; class Main {}",
            "additional/demo/Extra.java": "package demo; class Extra {}",
        }
    )
    plan = build_plan(source, source_dirs=["production", "additional"], seed=42)
    assert plan.ok, plan.to_dict()
    assert plan.mapping["production/demo/Main.java"].startswith("production/_internal_code/")
    assert plan.mapping["additional/demo/Extra.java"].startswith("additional/_internal_code/")
    assert not build_plan(source, source_dirs=["production/demo"]).ok


def test_maven_cannot_relocate_only_compiler_excluded_sources(project):
    pom = POM.replace(
        "<version>3.13.0</version>",
        "<version>3.13.0</version><configuration><excludes><exclude>**/Unused.java</exclude></excludes></configuration>",
    )
    source = project(
        {
            **sources(),
            "pom.xml": pom,
            "src/main/java/excluded/Unused.java": "package excluded; class Unused {}",
        }
    )
    plan = build_plan(source, include=["src/main/java/excluded/**"], seed=42)
    assert not plan.ok
    assert any("excluded" in item.message for item in plan.diagnostics)


@pytest.mark.parametrize("system", ["maven", "gradle"])
def test_ignored_build_metadata_is_copied_without_semantic_discovery(project, system):
    build = "pom.xml" if system == "maven" else "build.gradle"
    source = project(
        {
            **sources(build),
            ".gitignore": "vendor/\n",
            "vendor/pom.xml": "invalid XML",
            "vendor/build.gradle": "invalid DSL",
            "vendor/src/main/java/Unused.java": "not Java",
            "vendor/target/payload.txt": "ignored but preserved",
        }
    )
    plan = build_plan(source, seed=42)
    assert plan.ok, plan.to_dict()
    assert [unit.root for unit in plan.inspection.source_units] == ["src/main/java"]
    output = apply_plan(plan)
    for record in plan.inspection.files:
        if record.path.startswith("vendor/"):
            assert plan.mapping[record.path] == record.path
            assert (output / "shuffled" / record.path).read_bytes() == (
                source / record.path
            ).read_bytes()


def test_reincluded_build_keeps_ignored_parent_pom_out_of_filter_analysis(project):
    source = project(
        {
            **sources(),
            ".gitignore": "vendor/*\n!vendor/active/\n",
            "vendor/pom.xml": "invalid XML",
            "vendor/active/pom.xml": POM,
            "vendor/active/src/main/java/demo/Active.java": "package demo; class Active {}",
        }
    )
    plan = build_plan(source, seed=42)
    assert plan.ok, plan.to_dict()
    assert plan.mapping["vendor/pom.xml"] == "vendor/pom.xml"
    assert (
        plan.mapping["vendor/active/src/main/java/demo/Active.java"]
        != "vendor/active/src/main/java/demo/Active.java"
    )


def test_java_dry_run_does_not_execute_target_or_compile(project, monkeypatch, capsys):
    source = project(sources("build.gradle"))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("executed target"))
    assert main(["shuffle", str(source), "--dry-run", "--seed", "42"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["language"] == "java"
    assert not source.with_name(source.name + "-shuffled").exists()
    assert inspect_project(source).language == "java"


@pytest.mark.parametrize("modular", [False, True])
def test_java_compile_and_run_preserves_package_access_identity_and_resources(
    project, tmp_path, modular
):
    javac, java = shutil.which("javac"), shutil.which("java")
    if not javac or not java:
        pytest.skip("JDK not installed")
    files = sources()
    if modular:
        files["src/main/java/module-info.java"] = "module demo { exports demo; }\n"
    source = project(files)
    plan = build_plan(source, seed=42)
    if modular:
        assert plan.mapping["src/main/java/module-info.java"] == "src/main/java/module-info.java"
    output = apply_plan(plan)
    for label in ("original", "shuffled"):
        snapshot = output / label
        classes = tmp_path / (label + "-classes")
        classes.mkdir()
        command = [
            javac,
            "-d",
            str(classes),
            *map(str, sorted((snapshot / "src/main/java").rglob("*.java"))),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        assert result.returncode == 0, result.stderr
        shutil.copytree(snapshot / "src/main/resources", classes, dirs_exist_ok=True)
        command = (
            [java, "--module-path", str(classes), "-m", "demo/demo.Main"]
            if modular
            else [java, "-cp", str(classes), "demo.Main"]
        )
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok"


def test_gradle_test_compilation_cannot_prove_production_migration(project, tmp_path):
    tool = os.environ.get("REPOSHUFFLE_GRADLE") or shutil.which("gradle")
    if not tool:
        pytest.skip("gradle not installed; set REPOSHUFFLE_GRADLE")
    from scripts.evaluate import (
        compiled_relocation,
        compiler_inputs,
        gradle_compiler_arguments,
        jar_inventory,
    )

    files = sources("build.gradle.kts")
    files["settings.gradle.kts"] = 'rootProject.name = "demo"\n'
    files["build.gradle.kts"] += (
        'val omitted = "**/Unused.java"\n'
        "sourceSets.main { java.exclude(omitted) }\n"
        'sourceSets.test { java.srcDir("src/main/java") }\n'
    )
    files["src/main/java/excluded/Unused.java"] = "package excluded; public class Unused {}"
    plan = build_plan(
        project(files),
        source_dirs=["src/main/java"],
        include=["src/main/java/excluded/**"],
        seed=42,
    )
    assert plan.ok, plan.to_dict()
    assert plan.layout["moved_files"] == 1
    output = apply_plan(plan)
    results = []
    inventories = []
    for label in ("original", "shuffled"):
        snapshot = output / label
        process = subprocess.run(
            [tool, "--no-daemon", *gradle_compiler_arguments(tmp_path, label), "clean", "build"],
            cwd=snapshot,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        assert (snapshot / "build/classes/java/test/excluded/Unused.class").is_file()
        inventory = jar_inventory(snapshot, ["build/libs/*.jar"])
        assert inventory and all(
            "excluded/Unused.class" not in classes for classes in inventory.values()
        )
        inventories.append(inventory)
        results.append(
            {
                "compiler_inputs": compiler_inputs(
                    snapshot, "gradle", tmp_path / f"{label}-compiler-inputs"
                )
            }
        )
    assert inventories[0] == inventories[1]
    coverage = compiled_relocation(plan.mapping, *results)
    assert not coverage["complete"]
    assert coverage["missing_original"] == ["src/main/java/excluded/Unused.java"]
    assert coverage["missing_shuffled"] == [plan.mapping["src/main/java/excluded/Unused.java"]]


@pytest.mark.parametrize("system", ["maven", "gradle"])
@pytest.mark.parametrize("multi", [False, True])
def test_standard_build_collects_relocated_sources(project, tmp_path, system, multi):
    tool = os.environ.get("REPOSHUFFLE_" + system.upper()) or shutil.which(
        "mvn" if system == "maven" else "gradle"
    )
    if not tool:
        pytest.skip(f"{system} not installed; set REPOSHUFFLE_{system.upper()}")
    from scripts.evaluate import (
        compare,
        compiled_relocation,
        compiler_inputs,
        gradle_compiler_arguments,
        jar_inventory,
        junit_results,
    )

    build_file = "pom.xml" if system == "maven" else "build.gradle.kts"
    files = sources(build_file)
    if multi:
        application = {
            "app/" + path: content for path, content in files.items() if path != build_file
        }
        helper = application.pop("app/src/main/java/demo/Helper.java")
        files = {**application, "lib/src/main/java/demo/Helper.java": helper}
        if system == "maven":
            files["pom.xml"] = POM.replace(
                "<properties>",
                "<packaging>pom</packaging><modules><module>lib</module><module>app</module></modules><properties>",
            )
            files["lib/pom.xml"] = POM.replace(
                "<artifactId>demo</artifactId>", "<artifactId>lib</artifactId>"
            )
            files["app/pom.xml"] = POM.replace(
                "<artifactId>demo</artifactId>", "<artifactId>app</artifactId>"
            ).replace(
                "<dependencies>",
                "<dependencies><dependency><groupId>demo</groupId><artifactId>lib</artifactId><version>1</version></dependency>",
            )
        else:
            files["settings.gradle.kts"] = 'include("lib", "app")\n'
            files["lib/build.gradle.kts"] = sources(build_file)[build_file]
            files["app/build.gradle.kts"] = (
                sources(build_file)[build_file]
                + 'dependencies { implementation(project(":lib")) }\n'
            )
    if system == "gradle":
        # Declare project identity instead of deriving archive names from the
        # independently named original/shuffled snapshot directories.
        files["settings.gradle.kts"] = 'rootProject.name = "demo"\n' + files.get(
            "settings.gradle.kts", ""
        )
    source = project(files)
    plan = build_plan(source, seed=42)
    output = apply_plan(plan)
    baseline = None
    for label in ("original", "shuffled"):
        snapshot = output / label
        args = (
            [tool, "-B", "clean", "package"]
            if system == "maven"
            else [
                tool,
                "--no-daemon",
                *gradle_compiler_arguments(tmp_path, label),
                "clean",
                "build",
            ]
        )
        result = subprocess.run(
            args, cwd=snapshot, capture_output=True, text=True, timeout=240, check=False
        )
        assert result.returncode == 0, result.stdout + result.stderr
        jar_pattern = "**/target/*.jar" if system == "maven" else "**/build/libs/*.jar"
        jars = sorted(snapshot.glob(jar_pattern))
        assert jars
        report_pattern = (
            "**/target/surefire-reports/TEST-*.xml"
            if system == "maven"
            else "**/build/test-results/test/TEST-*.xml"
        )
        tests = junit_results(list(snapshot.glob(report_pattern)), 0, snapshot)
        inventory = jar_inventory(snapshot, [jar_pattern])
        inputs = compiler_inputs(snapshot, system, tmp_path / f"{label}-compiler-inputs")
        assert inputs["exit"] == 0, inputs
        assert not any("/src/test/" in "/" + path for path in inputs["files"])
        assert tests["complete"] and tests["counts"] == {"passed": 1}
        if baseline is None:
            baseline = (tests, inventory, inputs)
        else:
            comparison = compare(baseline[0], tests)
            assert comparison["comparable"]
            assert all(
                not comparison[key]
                for key in ("added_nodes", "removed_nodes", "changed_nodes", "new_failures")
            )
            assert inventory == baseline[1]
            coverage = compiled_relocation(
                plan.mapping, {"compiler_inputs": baseline[2]}, {"compiler_inputs": inputs}
            )
            assert coverage["complete"], coverage
            assert coverage["moved_sources"] == plan.layout["moved_files"]
        result = subprocess.run(
            [shutil.which("java"), "-cp", os.pathsep.join(map(str, jars)), "demo.Main"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok"
