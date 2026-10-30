"""Adapt Maven source-consuming plugins to real relocated paths, not shadow copies."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

from ..models import Diagnostic, Plan

_PLUGIN = re.compile(
    rb"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<plugin(?:\s[^>]*)?>.*?</plugin\s*>", re.DOTALL
)


def adapt_maven_plugins(plan: Plan, ant_match) -> None:
    for record in plan.inspection.files:
        if (
            PurePosixPath(record.path).name != "pom.xml"
            or record.reason == "matched .gitignore rule"
        ):
            continue
        project = PurePosixPath(record.path).parent
        units = [
            unit
            for unit in plan.inspection.source_units
            if unit.build_system == "maven" and PurePosixPath(unit.project).is_relative_to(project)
        ]
        if not units:
            continue
        original = plan.contents[record.path]
        pairs = []
        for unit in units:
            root = PurePosixPath(unit.root)
            for source in plan.inspection.files:
                path = PurePosixPath(source.path)
                if path.is_relative_to(root):
                    pairs.append(
                        (
                            source.path,
                            path.relative_to(root).as_posix(),
                            PurePosixPath(plan.mapping[source.path]).relative_to(root).as_posix(),
                        )
                    )

        def adapt(match, record=record, pairs=pairs):
            data = match[0]
            if data.startswith(b"<!"):
                return data
            if not any(name in data for name in (b"maven-javadoc-plugin", b"maven-source-plugin")):
                return data
            parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
            try:
                plugin = ET.fromstring(data, parser=parser)
            except ET.ParseError:
                plan.diagnostics.append(
                    Diagnostic(
                        "JAVA_BUILD_CONFIGURATION",
                        record.path,
                        "Cannot safely adapt namespaced/nested Maven source plugin XML.",
                    )
                )
                return data
            artifact = plugin.findtext("artifactId")
            if artifact not in {"maven-javadoc-plugin", "maven-source-plugin"}:
                return data
            changed = False
            configuration = plugin.find("configuration")
            if configuration is None and artifact == "maven-javadoc-plugin":
                configuration = ET.SubElement(plugin, "configuration")
                changed = True
            configurations = (
                [configuration] if configuration is not None else []
            ) + plugin.findall("executions/execution/configuration")
            for config in configurations:
                fields = (
                    ("sourceFileIncludes", "sourceFileExcludes")
                    if artifact == "maven-javadoc-plugin"
                    else ("includes", "excludes")
                )
                for field in fields:
                    node = config.find(field)
                    if node is None:
                        continue
                    for item in node:
                        if not isinstance(item.tag, str) or not item.text:
                            continue
                        before = item.text.strip()
                        if not any(
                            ant_match(old, before) != ant_match(new, before)
                            for _, old, new in pairs
                        ):
                            continue
                        after = before if before.startswith("**/") else "**/" + before
                        for path, old, new in pairs:
                            if ant_match(old, before) != ant_match(new, after):
                                raise ValueError(
                                    f"{record.path}: source plugin filter cannot preserve selection for {path}"
                                )
                        item.text = after
                        changed = True
                if artifact == "maven-javadoc-plugin" and config.find("sourceFileIncludes") is None:
                    if any(
                        config.findtext(name, "").strip()
                        for name in ("subpackages", "sourcepath", "excludePackageNames")
                    ):
                        plan.diagnostics.append(
                            Diagnostic(
                                "JAVA_BUILD_ASSUMPTIONS",
                                record.path,
                                "Custom Javadoc package selection needs independent verification.",
                                severity="warning",
                            )
                        )
                    else:
                        includes = ET.SubElement(config, "sourceFileIncludes")
                        ET.SubElement(includes, "sourceFileInclude").text = "**/*.java"
                        changed = True
            if not changed:
                return data
            rendered = ET.tostring(plugin, encoding="utf-8")
            return rendered.replace(b"\n", b"\r\n") if b"\r\n" in data else rendered

        rewritten = _PLUGIN.sub(adapt, original)
        if rewritten != original:
            plan.contents[record.path] = rewritten
            plan.changes.append(
                {
                    "kind": "rewrite-build",
                    "path": record.path,
                    "reason": "preserve Maven source/Javadoc selection using relocated source files",
                }
            )
