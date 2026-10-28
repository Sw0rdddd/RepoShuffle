# RepoShuffle

**Reshape Python and Java source trees. Preserve logical identities.**

![Version](https://img.shields.io/badge/version-0.4.1-2563eb?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.10–3.13-3776ab?style=flat-square)
![Interface](https://img.shields.io/badge/interface-CLI%20%2F%20API-475569?style=flat-square)

**English** · [简体中文](README_zh.md) · [Docs](docs/README.md) · [Evaluation](docs/evaluation.md) · [Limitations](docs/limitations.md)

---

| Mode | Layout |
| --- | --- |
| **Flat** (Python) | Collect implementation files at one level; resolve duplicate names with numeric suffixes |
| **Random** | Generate grouped directory trees, controlled by seed, complexity, and depth |

For Python, packages within one source root share an allocation pool; independent roots stay separate. Flit follows single-package delivery boundaries. Tests, docs, resources, and configuration stay in place by default. Output is self-contained: public imports usually need no changes, physical imports reuse canonical module identities, and namespace paths remain extensible.

## ⚡ Quick start

Install from this repository's root. Supports Python 3.10–3.13; a virtual environment is recommended.

```bash
python -m pip install .

# Inspect discovery and preservation reasons without writing files
reposhuffle inspect ./demo

# Preview the layout and static checks
reposhuffle shuffle ./demo --mode random --seed 42 --dry-run

# Generate a random layout, or flatten implementation code
reposhuffle shuffle ./demo --mode random --seed 42
reposhuffle shuffle ./demo --mode flat
```

By default, paired output is created beside the input. **The original project is unchanged; existing output is never overwritten.**

```text
demo-shuffled/
├── original/       # Original structure snapshot
├── shuffled/       # Complete reorganized project
└── manifest.json   # Parameters, mappings, preservation reasons, and static checks
```

Use a separate virtual environment to install dependencies and test as the original project requires:

```bash
cd ./demo-shuffled/shuffled
python -m pip install -e .
python -m pytest tests/
```

## Java (random only)

```bash
reposhuffle shuffle ./java-demo --language java --mode random --seed 42 --dry-run
reposhuffle shuffle ./java-demo --language java --mode random --seed 42
```

Initial Java support targets recursive Maven/Gradle production source collection, preserving filenames and source bytes, including package declarations. Tests, resources and descriptors stay in place; no runtime loader or original-path source copies are generated. Planning requires no JDK and does **not** compile Java. Explicit source roots must match actual build inputs; known source-boundary and acceptance gaps remain open. See the [Java guide](docs/java.md), [adapter architecture](docs/architecture.md) and [review findings](docs/review.md) (Chinese).

Only one language is transformed at a time. Auto detection prioritizes root build metadata; ambiguous projects require `--language`. Other languages stay in place.

## Random layout examples

Both use random mode, seed=42, default complexity `0.7`, maximum depth `3` and layout version 5:
- **Python**: eight modules, generated with 0.3.4; this is not a new 0.4.0 validation record.
- **Java**: eight production files, generated with 0.4.0; the source root is auto-detected as `src/main/java`.

```text
# Python: original    # Python: random                # Java: original            # Java: random
sample/               sample/                         src/main/java/              src/main/java/
├── __init__.py       ├── __init__.py                 └── demo/                   └── _internal_code/
├── api.py            ├── _internal_runtime.py            ├── Main.java               ├── Cache.java
├── cache.py          ├── _internal_routes.py             ├── api/                    ├── Config.java
├── client.py         └── _internal_code/                 │   └── Client.java         ├── Main.java
├── config.py             ├── __init__.py                 ├── core/                   ├── Strings.java
├── errors.py             ├── _internal_aliases.py        │   ├── Cache.java          └── module_1/
├── models.py             ├── api.py                      │   └── Engine.java             ├── Client.java
├── parser.py             ├── client.py                   ├── model/                      ├── Engine.java
└── utils.py              ├── errors.py                   │   ├── Config.java             ├── Parser.java
                          ├── parser.py                   │   └── User.java               └── User.java
                          └── module_1/                   └── util/
                              ├── __init__.py                 ├── Parser.java
                              ├── cache.py                    └── Strings.java
                              ├── config.py
                              ├── models.py
                              └── utils.py
```

**Python**: public imports such as `from sample.cache import VALUE` stay unchanged. Tests, resources, and configuration remain in place; runtime/routes/aliases files must be kept.

**Java**: only physical source locations change, not packages. For example, `Cache.java` moves to `_internal_code/Cache.java` but still declares `package demo.core;`; callers still use `import demo.core.Cache;`. Sources remain under the original compiler root, `src/main/java`. The POM, `src/test/java` and `src/main/resources` stay in place; no Python initializers or routing files are generated.

Both versions were compiled with javac, packaged into JARs and executed to check imports, reflective class identity, resources and matching class inventories. This does not establish compatibility with every Maven/Gradle plugin or imply that real-repository CI has passed.

## 🎛️ CLI options

| Option | Description |
| --- | --- |
| `--language auto\|python\|java` | Select one language; default `auto`; Java supports random only |
| `--source-dir RELATIVE_PATH` | Tell the tool where production sources live, relative to the selected project; repeatable, usually optional; does not modify the build |
| `--mode flat\|random` | Defaults to `random` for `shuffle`; add to `inspect` for a complete plan |
| `--seed INTEGER` | Random seed; generated and recorded if omitted |
| `--max-depth 1..5` | Maximum random-directory depth, default `3`; does not force multiple levels |
| `--complexity 0..1` | Random mode only, default `0.7`; higher values favor smaller groups, richer branching, and more levels |
| `--subproject RELATIVE_PATH` | Select an independent project inside the input directory; only that project is snapshotted; omit to use the input project |
| `--include PATH_OR_PATTERN` | Select implementation files to move, relative to the selected project; repeatable; other files are copied in place |
| `--exclude PATH_OR_PATTERN` | Keep matching files/directories in place, relative to the selected project; repeatable |
| `-o, --output PATH` | Paired output container; parent must exist; `shuffle` only |
| `--dry-run` | Analyze without creating output; `shuffle` only |
| `--version` / `--help` | Show version / help |

```bash
# Tune the tree and choose an output location
reposhuffle shuffle ./demo --seed 42 --complexity 0.85 --max-depth 2 -o ./results/demo-pair

# Process one project inside a workspace
reposhuffle shuffle ./workspace --subproject services/api --seed 42

# Select implementation areas with repeatable include / exclude options
reposhuffle shuffle ./demo --include 'backend/**' --include 'lib/**' --exclude 'backend/vendor/**'
```

### Choosing `--subproject` versus `--source-dir`

**One selects the project; the other describes its source location. Usually neither is needed.** `--subproject` already existed; `--source-dir` is the new source-root option.

```text
workspace/                         # Input directory
└── services/api/                  # Independently processable/buildable project
    ├── pom.xml
    └── src/
        ├── main/java/             # Production source root
        └── test/java/             # Tests stay in place
```

```bash
reposhuffle shuffle ./workspace --subproject services/api --language java --source-dir src/main/java --dry-run
```

In this command:

- `--subproject services/api` is relative to the input `workspace`, selecting `workspace/services/api`. Only api is snapshotted, not the other workspace projects. Do not isolate a module that still requires an external parent POM or sibling modules.
- `--source-dir src/main/java` is relative to the selected api project, resolving to `workspace/services/api/src/main/java`. It does not mean copying only that directory: the POM, tests and resources are still copied with api.
- Repeat `--source-dir` for multiple actual production roots. Explicit roots replace the default discovery scope; they **do not add or change Maven/Gradle compiler inputs**.
- To limit which files move, use `--include` / `--exclude`, not a broader or narrower source root. These patterns are also relative to the selected project, for example `--include 'src/main/java/demo/**'`.

The standard `src/main/java` above is auto-detected, so normally omit `--source-dir src/main/java`. Passing `./workspace/services/api` directly also removes the need for `--subproject`. 0.4.1 rejects broader or narrower roots than known Maven compiler boundaries; do not replace an actual root with a parent such as `src`. Unknown build models still need independent build verification; see [boundaries and fixes](docs/review.md).

Also available as `python -m reposhuffle`. Results are JSON; redirect stdout to save a report. Exit code `0` means the current operation passed; `2` indicates an input, conversion, or file error.

<details>
<summary>Source selection and layout rules</summary>

- Discovery uses packaging configuration, ordinary packages, and implementation directories—not just `src/`. Set `source-dirs = ["backend", "lib"]` under `[tool.reposhuffle]` in `pyproject.toml` to select roots explicitly.
- Root and nested project `.gitignore` files are respected, including `!` rules. Matches stay in place even if tracked or selected by `--include`. Unignore parent directories first; global rules and `.git/info/exclude` are not read.
- `--include` does not inadvertently relocate tests, docs, or fixtures. Git history, virtual environments, caches, and build artifacts are not copied. Input and output must not contain each other.
- Implementations move into `_internal_code/` (with a numeric prefix suffix on name conflicts): multiple packages in one source root share storage; a single package uses internal storage. **Flat applies to implementation areas, not the entire repository.**
- Depth starts at storage-root level 0: `--max-depth 3` permits levels 0–3. Complexity controls group size, branching, and depth preferences; nearby values may produce identical layouts.
- Reproduction requires the same tool and layout versions, inputs, seed, complexity, and other parameters. Current layout version is 5; earlier layouts are not reproduced.
- In `.md` / `.markdown`, unambiguous local file links and reference definitions are updated to relocated implementations; changes are audited in `manifest.json`. Code examples, directory links, unsupported document syntax/formats, and recognized affected path mentions are left unchanged with `DOCUMENTATION_PATH` warnings in JSON diagnostics. External URLs and logical imports are not rewritten. Link fragments are retained but not verified (`DOCUMENTATION_ANCHOR`); non-UTF-8 documents are not processed (`DOCUMENTATION_ENCODING`). Test/fixture and gitignored documents are never rewritten. The input and `original/` remain unchanged.
- Package initializers, location-sensitive files, and resource anchors are preserved as needed; scripts retain thin entry points. Module routing tables are required at runtime. See `inspect` fields `role` and `reason` for selection decisions.

</details>

## 🧩 Python API

```python
from reposhuffle import apply_plan, build_plan, inspect_project

inspection = inspect_project("./demo")
plan = build_plan(
    "./demo",
    mode="random",
    seed=42,
    max_depth=3,
    complexity=0.7,
    include=["backend/**", "lib/**"],
    exclude=["backend/vendor/**"],
)
if plan.ok:
    output = apply_plan(plan, output="./results/demo-pair")
else:
    report = plan.to_dict()
```

- `inspect_project`: report source discovery and preservation reasons.
- `build_plan`: read-only planning; changed content is rendered in memory, while unchanged assets remain disk-backed. No target code is executed.
- `apply_plan`: publish paired output with streaming and hash verification; omit `output` for the default sibling directory. If inputs, the directory inventory, or the plan change after validation, build a new plan.

The API also accepts `subproject="services/api"`. `plan.compatibility` summarizes build adapters, relocation ratios, preservation reasons, and risks—not runtime verification.

## 🛡️ Validation boundaries

Conversion runs Python static compilation or Java source-byte/boundary checks, plus shared target and inventory checks. **It does not prove functional equivalence, install dependencies, or run target tests.** File and traceback paths change; source scanning, custom loaders, special builds, and type checking may need adaptation. Symbolic links are not automatically followed. This is not source encryption.

**0.4.1** fixes the four review findings and requires strict production-compiler input coverage for relocated Java sources. See the [local validation record](docs/evaluation-0.4.1.md); full real-repository results belong to Actions runs for the corresponding commit, not historical passes. Methods and limits are maintained in [evaluation](docs/evaluation.md), with fix tracking in [review](docs/review.md) (Chinese).

## Development

```bash
python -m pip install -e '.[dev]'
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
python -c "from pathlib import Path; Path('.tmp').mkdir(exist_ok=True)"
python -m pytest -q --basetemp=.tmp/pytest
python -m build --outdir .tmp/dist
```

CI is configured for Windows / Linux × Python 3.10–3.13; passing status depends on actual runs.

[Python profiles](evaluation/python.json) and [Java profiles](evaluation/java.json) each contain ten pinned repositories. The shared [harness](scripts/evaluate.py) executes upstream code; use an isolated environment. Full repository comparisons belong in Actions, not local/Docker reruns. Configuration scopes, acceptance gaps, scheduling and versioned evidence are documented once in [evaluation](docs/evaluation.md) (Chinese).
