from __future__ import annotations

import json
import posixpath
from pathlib import PurePosixPath
from urllib.parse import quote

import pytest

from reposhuffle import apply_plan, build_plan


def _relative(plan, document, source="demo/worker.py"):
    return posixpath.relpath(plan.mapping[source], str(PurePosixPath(document).parent))


@pytest.mark.parametrize("mode", ["flat", "random"])
def test_markdown_links_and_definitions_are_updated_and_audited(project, mode):
    markdown = (
        b'[source](../demo/worker.py "Implementation")\r\n'
        b"[source][impl]\r\n"
        b'[impl]: <../demo/worker.py> "Title"\r\n'
        b"![source](../demo/worker.py)\r\n"
        b"[external](https://example.com/demo/worker.py)\r\n"
        b"`from demo.worker import VALUE`\r\n"
    )
    source = project(
        {"demo/__init__.py": "", "demo/worker.py": "VALUE=1\n", "docs/guide.md": markdown}
    )
    plan = build_plan(source, mode=mode, seed=42)
    assert plan.ok, plan.to_dict()
    relative = _relative(plan, "docs/guide.md")
    assert plan.contents["docs/guide.md"] == markdown.replace(
        b"../demo/worker.py", relative.encode()
    )
    changes = [c for c in plan.changes if c["kind"] == "documentation-reference"]
    assert len(changes) == 3
    assert all(c["path"] == "docs/guide.md" for c in changes)
    output = apply_plan(plan)
    assert (source / "docs/guide.md").read_bytes() == markdown
    assert (output / "original/docs/guide.md").read_bytes() == markdown
    assert (output / "shuffled/docs/guide.md").read_bytes() == plan.contents["docs/guide.md"]
    manifest = json.loads((output / "manifest.json").read_text())
    assert any(c["kind"] == "documentation-reference" for c in manifest["changes"])


def test_non_link_paths_only_warn_and_logical_imports_stay_intact(project):
    markdown = (
        "See `demo/worker.py` or demo/.\n"
        "```python\nfrom demo.worker import VALUE\n# demo/worker.py\n```\n"
        "    python demo/worker.py\n"
        "<!-- [source](demo/worker.py) -->\n"
        "<pre>[source](demo/worker.py)</pre>\n"
        "[directory](demo/)\n"
        "[outside](https://example.com/demo/worker.py)\n"
    )
    source = project({"demo/__init__.py": "", "demo/worker.py": "VALUE=1\n", "README.md": markdown})
    plan = build_plan(source, seed=42)
    assert plan.ok
    assert plan.contents["README.md"] == markdown.encode()
    warnings = [d for d in plan.diagnostics if d.rule == "DOCUMENTATION_PATH"]
    assert warnings
    assert all(d.severity == "warning" and d.path == "README.md" for d in warnings)
    assert {1, 4, 6, 7, 8, 9} <= {d.line for d in warnings}
    assert 3 not in {d.line for d in warnings}
    assert 10 not in {d.line for d in warnings}


@pytest.mark.parametrize("extension", ["rst", "txt", "mdx", "html", "adoc"])
def test_other_document_formats_warn_without_changes(project, extension):
    text = ".. literalinclude:: ../demo/worker.py\n"
    path = f"docs/guide.{extension}"
    source = project({"demo/__init__.py": "", "demo/worker.py": "VALUE=1\n", path: text})
    plan = build_plan(source)
    assert plan.ok
    assert plan.contents[path] == text.encode()
    assert any(d.path == path and d.severity == "warning" for d in plan.diagnostics)


def test_url_encoding_unicode_bom_query_anchor_and_retained_script_entry(project):
    markdown = (
        "\ufeff[source](<source area/demo/中文.py>)\n"
        f"[encoded]({quote('source area/demo/中文.py')}?raw=1#L1)\n"
        "[script](./runner.py)\n"
    ).encode()
    source = project(
        {
            "source area/demo/__init__.py": "",
            "source area/demo/中文.py": "VALUE=1\n",
            "pyproject.toml": '[tool.setuptools.packages.find]\nwhere=["source area"]\n',
            "runner.py": "print('ok')\n",
            "README.md": markdown,
        }
    )
    plan = build_plan(source, seed=42)
    assert plan.ok, plan.to_dict()
    target = plan.mapping["source area/demo/中文.py"]
    assert target != "source area/demo/中文.py"
    expected = markdown.decode().replace("source area/demo/中文.py", target)
    expected = expected.replace(quote("source area/demo/中文.py"), quote(target))
    expected = expected.replace("./runner.py", "./" + plan.mapping["runner.py"])
    assert plan.contents["README.md"] == expected.encode()
    assert any(d.rule == "DOCUMENTATION_ANCHOR" for d in plan.diagnostics)


def test_escaped_parentheses_titles_and_unchanged_links(project):
    source = project(
        {
            "source(1)/demo/__init__.py": "",
            "source(1)/demo/worker.py": "VALUE=1\n",
            "pyproject.toml": '[tool.setuptools.packages.find]\nwhere=["source(1)"]\n',
            "README.md": '[x](source\\(1\\)/demo/worker.py "keep title")\n[x](#section)\n[x](docs/guide.md)\n',
            "docs/guide.md": "Hello\n",
        }
    )
    plan = build_plan(source)
    assert plan.ok
    assert plan.mapping["source(1)/demo/worker.py"] != "source(1)/demo/worker.py"
    target = plan.mapping["source(1)/demo/worker.py"].replace("(", "\\(").replace(")", "\\)")
    assert plan.contents["README.md"].decode() == (
        f'[x]({target} "keep title")\n[x](#section)\n[x](docs/guide.md)\n'
    )


@pytest.mark.parametrize(
    "markdown",
    [
        "\\[source](demo/worker.py)\n",
        "`[source](demo/worker.py)`\n",
        "``[source](demo/worker.py)``\n",
        "<!-- [source](demo/worker.py)\n",
        "Text <span>[source](demo/worker.py)</span>\n",
        'Text <a title="[source](demo/worker.py)">example</a>\n',
        "> ```\n> [source](demo/worker.py)\n> ```\n",
        "- ```\n  [source](demo/worker.py)\n  ```\n",
        "[source](/demo/worker.py)\n",
        "[source](`example` demo/worker.py)\n",
    ],
)
def test_unsupported_contexts_warn_without_rewriting(project, markdown):
    source = project({"demo/__init__.py": "", "demo/worker.py": "VALUE=1\n", "README.md": markdown})
    plan = build_plan(source)
    assert plan.ok
    assert plan.contents["README.md"] == markdown.encode()
    assert any(d.rule == "DOCUMENTATION_PATH" for d in plan.diagnostics)


def test_test_fixtures_are_never_rewritten(project):
    markdown = "[source](../../demo/worker.py)\n"
    source = project(
        {
            "demo/__init__.py": "",
            "demo/worker.py": "VALUE=1\n",
            "tests/fixtures/sample.md": markdown,
        }
    )
    plan = build_plan(source)
    assert plan.ok
    assert plan.contents["tests/fixtures/sample.md"] == markdown.encode()
    assert any(d.rule == "DOCUMENTATION_PATH" for d in plan.diagnostics)


def test_external_urls_module_names_and_unchanged_links_do_not_warn(project):
    markdown = (
        "[external](https://example.com/demo/worker.py)\n"
        "<https://example.com/demo/worker.py>\n"
        "[mail](mailto:demo/worker.py)\n"
        "[guide](docs/guide.md#section)\n"
        "`from demo.worker import VALUE`\n"
    )
    source = project(
        {
            "demo/__init__.py": "",
            "demo/worker.py": "VALUE=1\n",
            "README.markdown": markdown,
            "docs/guide.md": "# Section\n",
        }
    )
    plan = build_plan(source)
    assert plan.ok
    assert plan.contents["README.markdown"] == markdown.encode()
    assert not any(d.rule.startswith("DOCUMENTATION_") for d in plan.diagnostics)


def test_invalid_encoding_and_ignored_documents_are_not_rewritten(project):
    source = project(
        {
            "demo/__init__.py": "",
            "demo/worker.py": "VALUE=1\n",
            "docs/legacy.md": b"\xff demo/worker.py\n",
            "docs/ignored.md": "[source](../demo/worker.py)\n",
            ".gitignore": "docs/ignored.md\n",
        }
    )
    plan = build_plan(source)
    assert plan.ok
    assert plan.contents["docs/legacy.md"] == b"\xff demo/worker.py\n"
    assert plan.contents["docs/ignored.md"] == b"[source](../demo/worker.py)\n"
    assert any(d.rule == "DOCUMENTATION_ENCODING" for d in plan.diagnostics)
    assert any(d.path == "docs/ignored.md" and d.severity == "warning" for d in plan.diagnostics)
