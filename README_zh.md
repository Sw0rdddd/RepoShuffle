# RepoShuffle

[English](README.md) | 简体中文

重排 Python 实现代码的目录结构，提供 CLI 和 Python API。当前版本 **0.3.1**。

- **扁平模式**：每个实现区内的代码集中到同一层；重名使用 `main_1.py`、`main_2.py` 等数字后缀。
- **随机模式**：先分组再生成目录树，避免单文件目录和空的中间层；目录依次命名为 `module_1`、`module_2`……。`complexity` 可取 0～1，控制密度、分支和深度，默认 `0.7`。
- **默认跨包混排**：同一源码根下的多个包共享文件分配池，文件可以跨原有子包混合，不依赖 `layer` 等特定目录名；独立源码根分别处理。
- **种子与复杂度配合**：seed 在同一复杂度档内改变树形和分配，complexity 调整整体密度与深度；固定 seed 和 complexity 可复现同一布局。
- **保留辅助结构**：`tests/`、文档、示例、fixture、资源和项目配置默认不搬迁。
- **遵循 `.gitignore`**：匹配的文件保留原位、仍复制到输出，不参与打乱。
- **保持公开 import**：移动的是物理源码，包名、相对导入、动态导入和测试中的模块名通常无需修改。

## 安装

支持 Python 3.10–3.13。

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

开发安装：`python -m pip install -e '.[dev]'`。不激活环境也可直接调用 `.venv/Scripts/python.exe`（Windows）或 `.venv/bin/python`（Linux）。

## CLI

```bash
# 查看识别的包、实现区、保留文件及其原因，不写文件
reposhuffle inspect ./demo

# 预览完整布局和静态检查
reposhuffle shuffle ./demo --mode random --seed 42 --dry-run

# 扁平化实现代码
reposhuffle shuffle ./demo --mode flat

# 随机分组，最大目录深度为 2
reposhuffle shuffle ./demo --mode random --seed 42 --max-depth 2

# 更复杂的树：更多混合节点，在最大深度内使用更多层级
reposhuffle shuffle ./demo --mode random --seed 42 --complexity 0.85

# 自定义输出容器；父目录必须存在
reposhuffle shuffle ./demo --mode random -o ./results/demo-pair

# 限定要移动的实现范围，其余文件仍复制但不移动
reposhuffle shuffle ./demo --include 'src/mypkg/**' --exclude 'src/mypkg/vendor/**'

# 自定义实现目录，不要求叫 src 或 scripts
reposhuffle shuffle ./demo --include 'backend/**' --include 'lib/**'
```

| 参数 | 说明 |
| --- | --- |
| `--mode flat\|random` | `shuffle` 默认 `random`；`inspect` 可加此参数查看完整计划 |
| `--seed 整数` | 固定随机分组与层级；省略则生成并记录 |
| `--max-depth 1..5` | 随机模式的目录深度上限，默认 `3`；不强制生成多层目录 |
| `--complexity 0..1` | 随机布局的密度和分支程度，默认 `0.7`；`0` 最简单，`1` 最复杂 |
| `--include 路径或通配符` | 限定实现代码选择范围，可重复；不表示删除范围外文件 |
| `--exclude 路径或通配符` | 指定保持原位的文件或目录，可重复 |
| `-o, --output 路径` | 整个配对输出容器的位置；仅用于 `shuffle` |
| `--dry-run` | 只分析，不创建输出；仅用于 `shuffle` |
| `--version` / `--help` | 查看版本 / 帮助 |

也支持 `python -m reposhuffle`。结果为 JSON；退出码 `0` 表示当前操作通过，`2` 表示输入、转换或文件错误。可将 stdout 重定向保存报告。

随机布局的深度相对于实现存储根目录：`--max-depth 3` 允许文件位于第 0、1、2、3 层。复杂度调整每目录目标文件数（`0` 约 10 个、`0.7` 约 6 个、`1` 约 4 个）、偏好的层级分布和分支。seed 为层级配额和分支选择增加变化，因此同一复杂度下不同 seed 会生成不同的树，但整体趋势不变。目录数是离散的，`0.70` 和 `0.71` 等相邻值可能得到相同布局。`complexity` 只适用于 `random` 模式。

默认从打包配置、普通包结构和实现目录发现源码，不限于 `src/`、`scripts/`：例如根目录包、单文件模块、`app/`、`backend/`、`lib/`、`tools/`。可在 `pyproject.toml` 的 `[tool.reposhuffle]` 下用 `source-dirs = ["..."]` 明确设置实现区。测试、文档和 fixture 的作用域不会因 `--include` 被意外搬走。

默认读取项目内的根级及嵌套 `.gitignore`，支持通配符、目录规则和 `!` 反向规则；即使文件已被 Git 跟踪或使用 `--include` 选中，匹配忽略规则后也不移动。忽略的父目录需先取消忽略，才能重新包含其文件。此行为不依赖安装 Git，也不读取用户全局忽略配置或 `.git/info/exclude`。`inspect` 会以 `matched .gitignore rule` 标记保留原因。虚拟环境、缓存、Git 历史等原有硬排除项仍不复制。

## 输出与运行

默认在输入项目同级创建 `<项目名>-shuffled/`：

```text
demo-shuffled/
├── original/       # 原结构快照
├── shuffled/       # 重排后的完整项目
└── manifest.json   # 配置、映射、布局统计、保留原因、修改摘要、静态检查结果
```

不覆盖已有目录，输入与输出不能互相包含。不修改输入项目；排除 Git 历史、虚拟环境、缓存和构建产物。

实现放入 `_reposhuffle_code/`：同一源码根下有多个包时，共享源码根下的存储目录，例如 `src/layer1`、`src/layer2` 的实现共同分配到 `src/_reposhuffle_code/`；只有一个包时继续使用包内存储。扁平模式直接放文件，随机模式使用编号子目录。包入口会加入小型、自包含的模块路由，不依赖安装 RepoShuffle，不访问原仓库，不修改全局文件系统 API。原模块名仍有效，因此测试中的 import、`mock.patch` 和配置中的入口通常不需要改名。路由表是运行所需文件，包含逻辑模块与实际源码的对应关系。`inspect` 的 `source_roots` 记录包所属的源码边界。

所有包的 `__init__.py`、构建时静态读取的版本模块、名为 `*_cy.py` 的编译回退模块，以及直接使用 `__file__` 的文件保留在原位并记录原因。资源目录旁向其他代码传入 `__name__` 的文件也保留位置，以兼容由模块身份推导资源根的工厂（例如 Flask Blueprint）。独立脚本在原位置保留薄入口，实际代码移到实现区。资源、空辅助目录和逻辑包资源根保留；迁空的包可能留下不含业务实现的标记文件，确保普通 wheel 安装后资源目录仍存在。测试和文档默认逐字节保留。**“扁平”只指各实现区，不是整个仓库没有目录。**

构建脚本和保留的测试若按路径读取另一个 Python 源文件，会获得最小文件锚点：读取者和目标均留在原位，并记录原因。这覆盖构建时读取版本文件、测试 bootstrap 加载器，以及按源码列表拼装工具的情况；不会因为存在一个读取者就保留整个包。

支持 `pyproject.toml` 中 setuptools 的具名 `package-dir` 映射；`pyproject.toml` 或 `setup.py` 中显式的 `py-modules` 和 `packages` 列表会在需要时加入实现存储包，修改记录保存在清单中。转换后的项目在运行时不需要 RepoShuffle 的 TOML 或代码编辑依赖。

在 `shuffled/` 按原项目方式安装和测试，例如：

```bash
cd ./demo-shuffled/shuffled
python -m pip install -e .
python -m pytest tests/
```

建议使用独立虚拟环境；依赖和实际 pytest 参数仍以原项目要求为准。目录选择和保留文件可从 `inspect` 的 `role`、`reason` 字段查看。

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

不传 `output` 使用同级默认目录。规划只读取文件、在内存生成内容；不会执行目标模块或构建钩子。输入文件、目录清单或计划在验证后变化时拒绝发布，需重新规划。

## 验证与边界

转换只进行静态编译、目标和清单检查，**不宣称证明功能等价，也不自动安装目标依赖或执行目标测试**。不再因为常见配置、日志、反射或文件读写 API 的存在而拒绝整个项目。

目前主要处理 `.py` 实现文件；二进制、资源和辅助文件原位保留。符号链接暂不自动跟随。显式列举源码文件的特殊构建方式、自定义加载器、直接从旧磁盘路径加载源码、类型检查器的静态解析等仍可能需要额外适配，应进行实际验证。内置路由保留逻辑包名，但文件路径和回溯路径会变化；这不是源码加密工具。

已在 Windows / Python 3.13 上以 0.2.0 验证 Requests、Flask、Rich 的 flat/random 转换和所选核心测试；当前布局与兼容性改动由工具测试和针对性检查覆盖，真实仓库结果以各轮测评报告为准。这并不等同于所有版本、平台或完整测试集已经通过。当前布局版本为 4，不承诺复现布局版本 3；只有相同工具及布局版本、输入、seed 和 complexity 才保证可复现。

[0.3.1 测评报告](docs/evaluation-0.3.1.md) 记录了 12 个真实仓库的 flat/random 对照结果，包括尚未解决的兼容性失败和 Windows 原生栈溢出限制。

## 开发

```bash
python -m pip install -e '.[dev]'
python -m ruff check src tests
python -m ruff format --check src tests
python -c "from pathlib import Path; Path('.tmp').mkdir(exist_ok=True)"
python -m pytest -q --basetemp=.tmp/pytest
python -m build --outdir .tmp/dist
```

CI 配置 Windows/Linux × Python 3.10–3.13；跨平台结果以实际运行记录为准。
