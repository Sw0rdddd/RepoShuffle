# RepoShuffle

**Reshape Python source trees. Keep logical imports.**

![Version](https://img.shields.io/badge/version-0.3.1-2563eb?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.10–3.13-3776ab?style=flat-square)
![Interface](https://img.shields.io/badge/interface-CLI%20%2F%20API-475569?style=flat-square)

**English** · [简体中文](README_zh.md) · [Evaluation](docs/evaluation-0.3.1.md) · [Limitations](docs/limitations.md)

---

| Mode | Layout |
| --- | --- |
| **Flat** | Collect implementation files at one level; resolve duplicate names with numeric suffixes |
| **Random** | Generate grouped directory trees, controlled by seed, complexity, and depth |

Packages within one source root share an allocation pool; independent roots stay separate. Tests, docs, resources, and configuration stay in place by default. Public imports usually need no changes. Output is self-contained, with no RepoShuffle runtime dependency.

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

## 🎛️ CLI options

| Option | Description |
| --- | --- |
| `--mode flat\|random` | Defaults to `random` for `shuffle`; add to `inspect` for a complete plan |
| `--seed INTEGER` | Random seed; generated and recorded if omitted |
| `--max-depth 1..5` | Maximum random-directory depth, default `3`; does not force multiple levels |
| `--complexity 0..1` | Random mode only, default `0.7`; higher values favor smaller groups, richer branching, and more levels |
| `--include PATH_OR_PATTERN` | Limit implementation selection; repeatable; other files are still copied in place |
| `--exclude PATH_OR_PATTERN` | Keep matching files or directories in place; repeatable |
| `-o, --output PATH` | Paired output container; parent must exist; `shuffle` only |
| `--dry-run` | Analyze without creating output; `shuffle` only |
| `--version` / `--help` | Show version / help |

```bash
# Tune the tree and choose an output location
reposhuffle shuffle ./demo --seed 42 --complexity 0.85 --max-depth 2 -o ./results/demo-pair

# Select implementation areas with repeatable include / exclude options
reposhuffle shuffle ./demo --include 'backend/**' --include 'lib/**' --exclude 'backend/vendor/**'
```

Also available as `python -m reposhuffle`. Results are JSON; redirect stdout to save a report. Exit code `0` means the current operation passed; `2` indicates an input, conversion, or file error.

<details>
<summary>Source selection and layout rules</summary>

- Discovery uses packaging configuration, ordinary packages, and implementation directories—not just `src/`. Set `source-dirs = ["backend", "lib"]` under `[tool.reposhuffle]` in `pyproject.toml` to select roots explicitly.
- Root and nested project `.gitignore` files are respected, including `!` rules. Matches stay in place even if tracked or selected by `--include`. Unignore parent directories first; global rules and `.git/info/exclude` are not read.
- `--include` does not inadvertently relocate tests, docs, or fixtures. Git history, virtual environments, caches, and build artifacts are not copied. Input and output must not contain each other.
- Implementations move into `_reposhuffle_code/`: multiple packages in one source root share storage; a single package uses internal storage. **Flat applies to implementation areas, not the entire repository.**
- Depth starts at storage-root level 0: `--max-depth 3` permits levels 0–3. Complexity controls group size, branching, and depth preferences; nearby values may produce identical layouts.
- Reproduction requires the same tool and layout versions, inputs, seed, complexity, and other parameters. Current layout version is 4; version 3 layouts are not reproduced.
- Package initializers, location-sensitive files, and resource anchors are preserved as needed; scripts retain thin entry points. Module routing tables are required at runtime. See `inspect` fields `role` and `reason` for selection decisions.

</details>

## 🧩 Python API

```python
from reposhuffle import apply_plan, build_plan, inspect_project

inspection = inspect_project("./demo")
plan = build_plan(
    "./demo", mode="random", seed=42, max_depth=3, complexity=0.7,
    include=["backend/**", "lib/**"],
    exclude=["backend/vendor/**"],
)
if plan.ok:
    output = apply_plan(plan, output="./results/demo-pair")
else:
    report = plan.to_dict()
```

- `inspect_project`: report source discovery and preservation reasons.
- `build_plan`: read-only planning and in-memory rendering; no target modules or build hooks are executed.
- `apply_plan`: write paired output; omit `output` for the default sibling directory. If inputs, the directory inventory, or the plan change after validation, build a new plan.

## 🛡️ Validation boundaries

Conversion runs static compilation, target checks, and inventory checks. **It does not prove functional equivalence, install dependencies, or run target tests.** File and traceback paths change; source scanning, custom loaders, special builds, and type checking may need adaptation. Symbolic links are not automatically followed. This is not source encryption.

See the [12-repository evaluation](docs/evaluation-0.3.1.md) and [known limitations](docs/limitations.md) for failures and unverified scenarios (both in Chinese).

## Development

```bash
python -m pip install -e '.[dev]'
python -m ruff check src tests
python -m ruff format --check src tests
python -c "from pathlib import Path; Path('.tmp').mkdir(exist_ok=True)"
python -m pytest -q --basetemp=.tmp/pytest
python -m build --outdir .tmp/dist
```

CI is configured for Windows / Linux × Python 3.10–3.13; passing status depends on actual runs.
