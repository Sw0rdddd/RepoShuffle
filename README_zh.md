# RepoShuffle

**重排 Python / Java 源码结构，保留逻辑身份。**

![Version](https://img.shields.io/badge/version-0.5.0-2563eb?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.10–3.13-3776ab?style=flat-square)
![Interface](https://img.shields.io/badge/interface-CLI%20%2F%20API-475569?style=flat-square)

[English](README.md) · **简体中文** · [文档](docs/README.md) · [测评](docs/evaluation.md) · [局限性](docs/limitations.md)

---

| 模式 | 效果 |
| --- | --- |
| **Flat**（Python） | 实现文件集中到同一层，重名以数字后缀消解 |
| **Random** | 生成分组目录树，用种子、复杂度和深度控制布局 |

Python 在同一源码根默认跨包混排，独立源码根分别处理；Flit 遵循单包交付边界。测试、文档、资源和配置默认保留原位，公开 import 通常无需修改。输出自包含，物理模块导入复用逻辑模块身份，命名空间路径可动态扩展。

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

## Java（仅随机模式）

```bash
reposhuffle shuffle ./java-demo --language java --mode random --seed 42 --dry-run
reposhuffle shuffle ./java-demo --language java --mode random --seed 42
```

首期面向 Maven/Gradle 递归收集的生产源码，保留文件名、原始字节及 package 声明。测试、资源和描述符留在原位，不生成加载器或原路径源码副本。规划无需 JDK，**不执行 Java 编译**。显式源码根须与实际构建输入一致，当前仍有源码边界和验收漏检等开放问题。详见 [Java 指南](docs/java.md)、[Adapter 架构](docs/architecture.md)及[审查发现](docs/review.md)。

一次只转换一种语言；自动识别优先使用根构建标识，有歧义时要求 `--language`，其他语言保留原位。

## 随机布局示例

两种语言都使用 random、seed=42、默认复杂度 `0.7`、最大深度 `3`，布局版本为 5。下图只展示源码文件，Java 保留的空目录省略：
- **Python**：八个模块，示例在 0.3.4 下实际生成，不作为新增的 0.4.0 验证记录。
- **Java**：八个生产文件，示例在 0.4.1 下实际生成；源码根为 `src/main/java`，额外保留原包路径后缀，深度参数只控制随机编号树。

```text
# Python 原始         # Python 随机                   # Java 原始                 # Java 随机
sample/               sample/                         src/main/java/              src/main/java/
├── __init__.py       ├── __init__.py                 └── demo/                   └── demo/
├── api.py            ├── _internal_runtime.py            ├── Main.java               └── _internal_code/
├── cache.py          ├── _internal_routes.py             ├── api/                        ├── demo/
├── client.py         └── _internal_code/                 │   └── Client.java             │   ├── api/
├── config.py             ├── __init__.py                 ├── core/                       │   │   └── Client.java
├── errors.py             ├── _internal_aliases.py        │   ├── Cache.java              │   ├── core/
├── models.py             ├── api.py                      │   └── Engine.java             │   │   └── Engine.java
├── parser.py             ├── client.py                   ├── model/                      │   ├── model/
└── utils.py              ├── errors.py                   │   ├── Config.java             │   │   └── Config.java
                          ├── parser.py                   │   └── User.java               │   └── util/
                          └── module_1/                   └── util/                       │       └── Parser.java
                              ├── __init__.py                 ├── Parser.java             └── module_1/
                              ├── cache.py                    └── Strings.java                └── demo/
                              ├── config.py                                                       ├── Main.java
                              ├── models.py                                                       ├── core/
                              └── utils.py                                                        │   └── Cache.java
                                                                                                  ├── model/
                                                                                                  │   └── User.java
                                                                                                  └── util/
                                                                                                      └── Strings.java
```

**Python**：公开导入如 `from sample.cache import VALUE` 无需修改；测试、资源及配置保留原位。runtime/routes/aliases 辅助文件必须保留。

**Java**：只改变源码存放位置，不改变包。例如 `Cache.java` 移到 `demo/_internal_code/module_1/demo/core/Cache.java`，文件内仍是 `package demo.core;`，调用方仍用 `import demo.core.Cache;`。源码仍位于原编译根 `src/main/java` 内；`pom.xml`、`src/test/java`、`src/main/resources` 保留原位，不生成 Python 的初始化或路由文件。

本示例已分别用 javac 编译两版、生成并运行 JAR，验证导入、反射类名、资源和类清单一致；不代表所有 Maven/Gradle 插件或真实仓库 CI 已通过。

## 🎛️ CLI 参数

| 参数 | 说明 |
| --- | --- |
| `--language auto\|python\|java` | 一次选择一种语言，默认 `auto`；Java 仅支持 random |
| `--source-dir 相对路径` | 告诉工具生产源码在哪里，相对于选定的项目根；可重复，通常可省略；不修改构建配置 |
| `--mode flat\|random` | `shuffle` 默认 `random`；`inspect` 加此参数可查看完整计划 |
| `--seed 整数` | 随机种子；省略则生成并记录 |
| `--max-depth 1..5` | 随机编号树深度上限，默认 `3`；不强制多层，Java 包路径后缀不计入 |
| `--complexity 0..1` | 仅限随机模式，默认 `0.7`；越大，文件分组越细、分支和层级越丰富 |
| `--subproject 相对路径` | 从输入目录中选择一个独立项目，只为该项目生成快照；不写则处理整个输入项目 |
| `--include 路径或通配符` | 筛选要移动的实现文件，相对于选定的项目根；可重复，其余文件仍复制、保留原位 |
| `--exclude 路径或通配符` | 筛选不移动的文件或目录，相对于选定的项目根；可重复 |
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

### `--subproject` 和 `--source-dir` 怎么选？

**一个选项目，一个说明源码位置；通常都不用写。**`--subproject` 是原有参数，`--source-dir` 是新增的源码根参数。

```text
workspace/                         # 命令的输入目录
└── services/api/                  # 可独立处理、构建的项目
    ├── pom.xml
    └── src/
        ├── main/java/             # 生产源码根
        └── test/java/             # 测试，保留原位
```

```bash
reposhuffle shuffle ./workspace --subproject services/api --language java --source-dir src/main/java --dry-run
```

这条命令中：

- `--subproject services/api` 相对于输入目录 `workspace`，选中 `workspace/services/api`。快照只包含 api 项目，不包含工作区的其他项目；不能随意截取仍依赖外部父 POM 或兄弟模块的子模块。
- `--source-dir src/main/java` 相对于选中的 api 项目，指向 `workspace/services/api/src/main/java`。它不表示只复制这个目录：`pom.xml`、测试、资源等仍随 api 项目复制。
- `--source-dir` 可重复列出多个实际生产根；显式指定后，以所列根替代默认发现范围。它只告诉工具源码在哪里，**不会让 Maven/Gradle 增加或改变编译目录**。
- 只想限制哪些文件被移动，用 `--include` / `--exclude`，不要扩大或缩小源码根来代替筛选；它们的路径也相对于选定项目根，例如 `--include 'src/main/java/demo/**'`。

上例的标准 `src/main/java` 能自动识别，实际使用可省略 `--source-dir src/main/java`。也可以直接输入 `./workspace/services/api`，此时无需 `--subproject`。0.4.1 会拒绝扩大或缩小已知 Maven 编译根；不要把实际根写成 `src` 等父目录。未知构建模型仍需独立构建验证，见[边界与修复记录](docs/review.md)。

也可用 `python -m reposhuffle`。结果为 JSON，可重定向 stdout 保存报告；退出码 `0` 表示当前操作通过，`2` 表示输入、转换或文件错误。

<details>
<summary>源码选择与布局规则</summary>

- 自动识别打包配置、普通包和实现目录，不限于 `src/`。也可在 `pyproject.toml` 的 `[tool.reposhuffle]` 下设置 `source-dirs = ["backend", "lib"]`。
- 默认遵循项目内根级及嵌套 `.gitignore`，支持 `!`；即使已跟踪或被 `--include` 选中，匹配项仍不移动。父目录须先取消忽略；不读取全局规则或 `.git/info/exclude`。
- 测试、文档、fixture 不因 `--include` 被意外搬走。Git 历史、虚拟环境、缓存和构建产物不复制；输入与输出不能互相包含。
- 实现移入 `_internal_code/`（名称冲突时为前缀追加数字）：同一源码根下多包共享存储，单包使用包内存储。**扁平化针对实现区，不是整个仓库。**
- 编号树深度从存储根的第 0 层计起，`--max-depth 3` 允许第 0～3 层；Java 还保留原包路径后缀，实际物理深度另记为 `physical_files_by_depth`。复杂度控制目录文件数、分支和层级偏好，相邻值可能产生相同布局。
- 复现需相同工具及布局版本、输入、种子和复杂度等参数。当前布局版本为 5，不复现旧版布局。
- `.md` / `.markdown` 中明确的本地文件链接及引用式链接定义自动指向迁移后的实现，修改记录写入 `manifest.json`。代码示例、目录链接、不支持的文档语法/格式及可识别的受影响路径只在 JSON diagnostics 中发出 `DOCUMENTATION_PATH` 警告，不改写；外部 URL、逻辑导入不改。锚点保留但不验证（`DOCUMENTATION_ANCHOR`）；非 UTF-8 文档不处理（`DOCUMENTATION_ENCODING`）。测试/fixture 和 gitignored 文档不改写。输入项目与 `original/` 均保持原样。
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

转换仅执行 Python 静态编译或 Java 原字节/源码边界检查，以及公共目标和清单检查，**不证明功能等价，也不自动安装依赖或运行目标测试**。文件路径和回溯会变化；源码扫描、自定义加载器、特殊构建和类型检查可能需要适配。符号链接不自动跟随；这不是源码加密工具。

**0.5.0** 整合 Java Adapter 与生产编译输入、迁移覆盖的严格验收。验证范围见[0.5.0 记录](docs/evaluation-0.5.0.md)，[选集替换及未覆盖场景](docs/limitations.md#真实仓库替换与未覆盖场景)明确保留；换仓库不代表原仓库已经兼容。完整真实仓库结果以相应提交的 Actions 为准，历史结果不自动覆盖当前版本。方法与边界统一见[测评](docs/evaluation.md)，修复跟踪见[审查记录](docs/review.md)。

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

[Python 配置](evaluation/python.json)与 [Java 配置](evaluation/java.json)各固定十仓库，共用[测评脚本](scripts/evaluate.py)。测评会执行上游代码，应使用隔离环境；完整真实仓库对照交给 Actions，不在本地或 Docker 重跑。配置范围、验收缺口、触发机制与版本证据统一维护在[测评说明](docs/evaluation.md)。
