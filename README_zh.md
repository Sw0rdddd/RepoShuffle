# RepoShuffle

**重排 Python 源码结构，保留逻辑导入。**

![Version](https://img.shields.io/badge/version-0.3.3-2563eb?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.10–3.13-3776ab?style=flat-square)
![Interface](https://img.shields.io/badge/interface-CLI%20%2F%20API-475569?style=flat-square)

[English](README.md) · **简体中文** · [真实仓库测评](docs/evaluation-0.3.3.md) · [局限性](docs/limitations.md)

---

| 模式 | 效果 |
| --- | --- |
| **Flat** | 实现文件集中到同一层，重名以数字后缀消解 |
| **Random** | 生成分组目录树，用种子、复杂度和深度控制布局 |

同一源码根默认跨包混排，独立源码根分别处理；Flit 遵循单包交付边界。测试、文档、资源和配置默认保留原位，公开 import 通常无需修改。输出自包含，物理模块导入复用逻辑模块身份，命名空间路径可动态扩展。

## ⚡ 快速开始

在本仓库根目录安装，支持 Python 3.10–3.13；建议使用虚拟环境。

```bash
python -m pip install .

# 查看识别结果与保留原因，不写文件
reposhuffle inspect ./demo

# 预览布局与静态检查
reposhuffle shuffle ./demo --mode random --seed 42 --dry-run

# 生成随机布局，或扁平化实现代码
reposhuffle shuffle ./demo --mode random --seed 42
reposhuffle shuffle ./demo --mode flat
```

默认在输入项目同级生成配对输出，**不修改原项目、不覆盖已有目录**：

```text
demo-shuffled/
├── original/       # 原结构快照
├── shuffled/       # 重排后的完整项目
└── manifest.json   # 参数、映射、保留原因与静态检查
```

在独立虚拟环境中，按原项目要求安装依赖并测试：

```bash
cd ./demo-shuffled/shuffled
python -m pip install -e .
python -m pytest tests/
```

## 🎛️ CLI 参数

| 参数 | 说明 |
| --- | --- |
| `--mode flat\|random` | `shuffle` 默认 `random`；`inspect` 加此参数可查看完整计划 |
| `--seed 整数` | 随机种子；省略则生成并记录 |
| `--max-depth 1..5` | 随机目录深度上限，默认 `3`；不强制生成多层 |
| `--complexity 0..1` | 仅限随机模式，默认 `0.7`；越大，文件分组越细、分支和层级越丰富 |
| `--subproject 相对路径` | 选择工作区中的独立子项目；选择规则相对于该子项目 |
| `--include 路径或通配符` | 限定实现选择范围，可重复；范围外文件仍复制并保留原位 |
| `--exclude 路径或通配符` | 匹配文件或目录保留原位，可重复 |
| `-o, --output 路径` | 配对输出容器，父目录须存在；仅限 `shuffle` |
| `--dry-run` | 仅分析，不创建输出；仅限 `shuffle` |
| `--version` / `--help` | 查看版本 / 帮助 |

```bash
# 调整树形并指定输出位置
reposhuffle shuffle ./demo --seed 42 --complexity 0.85 --max-depth 2 -o ./results/demo-pair

# 单独处理工作区中的一个项目
reposhuffle shuffle ./workspace --subproject services/api --seed 42

# 选择实现范围，可重复传入 include / exclude
reposhuffle shuffle ./demo --include 'backend/**' --include 'lib/**' --exclude 'backend/vendor/**'
```

也可用 `python -m reposhuffle`。结果为 JSON，可重定向 stdout 保存报告；退出码 `0` 表示当前操作通过，`2` 表示输入、转换或文件错误。

<details>
<summary>源码选择与布局规则</summary>

- 自动识别打包配置、普通包和实现目录，不限于 `src/`。也可在 `pyproject.toml` 的 `[tool.reposhuffle]` 下设置 `source-dirs = ["backend", "lib"]`。
- 默认遵循项目内根级及嵌套 `.gitignore`，支持 `!`；即使已跟踪或被 `--include` 选中，匹配项仍不移动。父目录须先取消忽略；不读取全局规则或 `.git/info/exclude`。
- 测试、文档、fixture 不因 `--include` 被意外搬走。Git 历史、虚拟环境、缓存和构建产物不复制；输入与输出不能互相包含。
- 实现移入 `_reposhuffle_code/`：同一源码根下多包共享存储，单包使用包内存储。**扁平化针对实现区，不是整个仓库。**
- 深度从存储根的第 0 层计起，`--max-depth 3` 允许第 0～3 层。复杂度控制目录文件数、分支和层级偏好，相邻值可能产生相同布局。
- 复现需相同工具及布局版本、输入、种子和复杂度等参数。当前布局版本为 4，不复现版本 3。
- 包初始化、位置敏感文件和资源锚点按需保留，脚本保留薄入口。模块路由表是运行必需文件；选择与保留原因可查看 `inspect` 的 `role`、`reason`。

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

- `inspect_project`：查看源码识别及保留原因。
- `build_plan`：只读规划，改写内容在内存生成，未改动资源不常驻内存；不执行目标代码。
- `apply_plan`：校验哈希并流式发布配对输出；省略 `output` 使用默认同级目录。验证后若输入、目录清单或计划变化，需重新规划。

API 同样支持 `subproject="services/api"`。`plan.compatibility` 汇总构建适配、移动比例、保留原因和风险，不等同于运行验证。

## 🛡️ 验证边界

转换仅执行静态编译、目标和清单检查，**不证明功能等价，也不自动安装依赖或运行目标测试**。文件路径和回溯会变化；源码扫描、自定义加载器、特殊构建和类型检查可能需要适配。符号链接不自动跟随；这不是源码加密工具。

公开保留 10 个真实仓库的通过集，见[测评记录](docs/evaluation-0.3.3.md)；本地 Windows/Linux 对照已通过，远端 Actions 尚未复验。后续完整真实仓库验证交给 GitHub Actions，已知限制见[局限性](docs/limitations.md)。

## 开发

```bash
python -m pip install -e '.[dev]'
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
python -c "from pathlib import Path; Path('.tmp').mkdir(exist_ok=True)"
python -m pytest -q --basetemp=.tmp/pytest
python -m build --outdir .tmp/dist
```

CI 配置：Windows / Linux × Python 3.10–3.13；通过情况以实际运行记录为准。

[测评配置](evaluation/python.json)记录仓库提交、依赖和测试范围；[测评脚本](scripts/evaluate.py)支持新增仓库、多种子对照和 wheel 隔离安装。用 `--help` 查看用法，`--output` 默认须指定全新目录；`--append --projects ...` 可追加新仓库，不覆盖已有结果。真实仓库工作流按周或手动运行，原始产物通过 CI 附件提供。测评会执行上游构建和测试，请使用隔离环境。
