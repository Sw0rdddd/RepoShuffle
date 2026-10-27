# RepoShuffle

English | [简体中文](README_zh.md)

Reorganize the directory structure of Python implementation code through a CLI or Python API. Current version: **0.3.0**.

- **Flat mode**: collect code at one level within each implementation area; resolve duplicate names with numeric suffixes such as `main_1.py` and `main_2.py`.
- **Random mode**: group files before building a directory tree, avoiding single-file directories and empty intermediate directories. Directories are named `module_1`, `module_2`, and so on. A `complexity` value from 0 to 1 controls density, branching, and depth; the default is `0.7`.
- **Seed and complexity together**: the seed changes the tree shape and assignments within a complexity level, while complexity biases the overall density and depth. Fixed seed and complexity reproduce the same layout.
- **Preserve auxiliary structure**: keep `tests/`, documentation, examples, fixtures, resources, and project configuration in their original locations by default.
- **Respect `.gitignore`**: matching files are still copied to the output but remain in place rather than being shuffled.
- **Keep public imports**: physical source locations change, while package names, relative imports, dynamic imports, and module names in tests usually require no changes.

## Installation

Supports Python 3.10–3.13.

```bash
python -m venv .venv

# Windows / Git Bash
source .venv/Scripts/activate
# Linux
# source .venv/bin/activate
# Windows / PowerShell
# .\.venv\Scripts\Activate.ps1

python -m pip install .
```

For development: `python -m pip install -e '.[dev]'`. Without activating the environment, use `.venv/Scripts/python.exe` on Windows or `.venv/bin/python` on Linux.

## CLI

```bash
# Inspect detected packages, implementation areas, and preserved files with reasons
# without writing files
reposhuffle inspect ./demo

# Preview the complete layout and static checks
reposhuffle shuffle ./demo --mode random --seed 42 --dry-run

# Flatten implementation code
reposhuffle shuffle ./demo --mode flat

# Randomize groups with a maximum directory depth of 2
reposhuffle shuffle ./demo --mode random --seed 42 --max-depth 2

# Richer tree: more mixed nodes and deeper levels within max-depth
reposhuffle shuffle ./demo --mode random --seed 42 --complexity 0.85

# Choose the output container; its parent directory must exist
reposhuffle shuffle ./demo --mode random -o ./results/demo-pair

# Limit relocation; other files are still copied but remain in place
reposhuffle shuffle ./demo --include 'src/mypkg/**' --exclude 'src/mypkg/vendor/**'

# Select custom implementation directories, not just src or scripts
reposhuffle shuffle ./demo --include 'backend/**' --include 'lib/**'
```

| Option | Description |
| --- | --- |
| `--mode flat\|random` | Defaults to `random` for `shuffle`; add to `inspect` to view a complete plan |
| `--seed INTEGER` | Fix the random grouping and hierarchy; generated and recorded if omitted |
| `--max-depth 1..5` | Maximum depth in random mode, default `3`; does not force multiple levels |
| `--complexity 0..1` | Random-layout density and branching, default `0.7`; `0` is the simplest shallow layout, `1` the richest |
| `--include PATH_OR_PATTERN` | Limit implementation selection; repeatable; does not delete other files |
| `--exclude PATH_OR_PATTERN` | Keep matching files or directories in place; repeatable |
| `-o, --output PATH` | Location of the entire paired output container; `shuffle` only |
| `--dry-run` | Analyze without creating output; `shuffle` only |
| `--version` / `--help` | Show the version / help |

You can also use `python -m reposhuffle`. Results are JSON. Exit code `0` means the current operation passed; `2` indicates an input, conversion, or file error. Redirect stdout to save a report.

Random-layout depth is relative to the implementation storage root: `--max-depth 3` permits files at levels 0, 1, 2, and 3. Complexity changes the target files per directory (about 10 at `0`, 6 at `0.7`, and 4 at `1`), the preferred depth distribution, and branching. The seed adds variation to the level quotas and branch choices, so different seeds at the same complexity produce different trees without changing the overall trend. Discrete directory counts can make nearby values such as `0.70` and `0.71` produce the same layout. `complexity` is only valid for `random` mode.

Source discovery uses packaging configuration, ordinary package structure, and implementation directories. It is not limited to `src/` or `scripts/`: root-level packages, single-file modules, `app/`, `backend/`, `lib/`, and `tools/` are supported layouts. To specify implementation areas explicitly, set `source-dirs = ["..."]` under `[tool.reposhuffle]` in `pyproject.toml`. Using `--include` does not inadvertently relocate test, documentation, or fixture scopes.

By default, RepoShuffle reads root and nested `.gitignore` files within the project, supporting wildcards, directory rules, and `!` negation. Matching files remain in place even if tracked by Git or selected with `--include`. An ignored parent directory must be unignored before its files can be re-included. Git does not need to be installed; global ignore configuration and `.git/info/exclude` are not read. `inspect` reports `matched .gitignore rule` as the preservation reason. Existing hard exclusions, such as virtual environments, caches, and Git history, are still not copied.

## Output and execution

By default, RepoShuffle creates `<project-name>-shuffled/` beside the input project:

```text
demo-shuffled/
├── original/       # Snapshot of the original structure
├── shuffled/       # Complete reorganized project
└── manifest.json   # Configuration, mappings, layout statistics, preservation reasons, changes, and static checks
```

Existing output directories are never overwritten. Input and output must not contain each other. The input project is not modified; Git history, virtual environments, caches, and build artifacts are excluded.

Package implementations are placed under `_reposhuffle_code/`: directly in flat mode, or in numbered subdirectories in random mode. Package entry points receive a small, self-contained module router. The output does not require RepoShuffle, does not access the original repository, and does not patch global filesystem APIs. Original module names remain valid, so imports in tests, `mock.patch` targets, and configured entry points usually need no renaming. The routing table is required at runtime and contains the mapping between logical modules and physical source locations.

All package `__init__.py` files, version modules read statically during builds, compiled-fallback modules named `*_cy.py`, and files that directly use `__file__` remain in place with recorded reasons. Files beside resources that pass `__name__` to other code also retain their locations, supporting factories that derive resource roots from module identity, such as Flask Blueprint. Standalone scripts retain thin entry points at their original locations, with their implementations moved into the implementation area. Resources, empty auxiliary directories, and logical package resource roots are preserved. Packages emptied by relocation may retain marker files containing no business implementation, ensuring their resource directories survive ordinary wheel installation. Tests and documentation are preserved byte-for-byte by default. **“Flat” applies to each implementation area, not to the entire repository.**

Build scripts and preserved tests that read another Python source file by path receive a minimal filesystem anchor: the reader and its target remain in place and the reason is recorded. This covers setup-time version readers, test bootstrap loaders, and tools that assemble themselves from a list of package sources. It does not preserve an entire package merely because such a reader exists.

Named setuptools `package-dir` mappings in `pyproject.toml` are supported. Explicit `py-modules` and `packages` lists in `pyproject.toml` or `setup.py` are extended with implementation storage packages when needed, with changes recorded in the manifest. The converted project does not depend on RepoShuffle's TOML or code-editing dependencies at runtime.

Install and test from `shuffled/` as you would the original project, for example:

```bash
cd ./demo-shuffled/shuffled
python -m pip install -e .
python -m pytest tests/
```

Use a separate virtual environment. Dependencies and actual pytest arguments still depend on the original project. The `role` and `reason` fields in `inspect` explain selection and preservation decisions.

## Python API

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

Omit `output` to use the default sibling directory. Planning only reads files and renders output in memory; it does not execute target modules or build hooks. Publication is rejected if input files, the directory inventory, or the plan change after validation; build a new plan instead.

## Validation and limitations

Conversion performs static compilation, target checks, and inventory checks only. **It does not prove functional equivalence or automatically install target dependencies or run target tests.** Common configuration files, logging, reflection, and file I/O APIs do not by themselves cause an entire project to be rejected.

The primary relocation targets are `.py` implementation files; binaries, resources, and auxiliary files remain in place. Symbolic links are not automatically followed. Special builds that explicitly enumerate source files, custom loaders, direct loading from old filesystem paths, and static type-checker resolution may require additional adaptation and should be verified. The built-in router preserves logical package names, but file paths and traceback paths change. This is not a source encryption tool.

Requests, Flask, and Rich were validated in flat/random modes with selected core tests on Windows / Python 3.13 under 0.2.0. The 0.3.0 layout and compatibility changes are covered by the tool test suite and targeted checks; a fresh evaluation across all sample repositories is a separate step. This does not mean all versions, platforms, or complete test suites have passed. Layout version 3 is not intended to reproduce layouts generated by 0.2.0; reproducibility is guaranteed only for the same version, inputs, seed, and complexity.

## Development

```bash
python -m pip install -e '.[dev]'
python -m ruff check src tests
python -m ruff format --check src tests
python -c "from pathlib import Path; Path('.tmp').mkdir(exist_ok=True)"
python -m pytest -q --basetemp=.tmp/pytest
python -m build --outdir .tmp/dist
```

CI is configured for Windows/Linux × Python 3.10–3.13. Cross-platform results depend on actual CI runs.
