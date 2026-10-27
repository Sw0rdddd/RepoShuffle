# 真实仓库测评（0.3.1）

## 范围与方法

- 环境：Windows、Python 3.13.13；seed=42、complexity=0.7、max-depth=3，flat/random 均使用默认选择范围。
- 12 个仓库均完成两种模式的转换和静态检查，共 24 份输出；根模块导入已核对实际源码位置。
- 原版与转换版在独立快照中使用相同依赖和测试命令，不修改上游测试断言。以下结果仅代表所列测试范围，不代表完整项目兼容。

## 对照结果

| 仓库 | 测试范围 | 原版 | flat | random |
| --- | --- | --- | --- | --- |
| Requests | utils、structures、hooks、packages、help、adapters 核心集 | 262 通过 | 262 通过 | 262 通过 |
| Flask | `tests/` | 494 通过 | 494 通过 | 494 通过 |
| Rich | `tests/` | 984 通过 | 984 通过 | 984 通过 |
| Lark | `tests/__main__.py` 聚合入口 | 1181 通过 | 1181 通过 | 1181 通过 |
| Packaging | `tests/`，保留默认 property 排除 | 63349 通过 | 63349 通过 | 63349 通过 |
| SQLAlchemy | `test/base`、`test/sql`、`test/orm/test_unitofwork.py`；SQLite、纯 Python | 9468 通过 | 9468 通过 | 9468 通过 |
| Django | 官方 runner 的 `utils_tests` | 717 运行 / 49 跳过 | 相同 | 相同 |
| Sphinx | `tests/test_util` | 229 通过 | 229 通过 | 229 通过 |
| Astroid | `tests/`，保留默认 acceptance 排除 | 2461 通过 / 5 失败 | 2453 通过 / 13 失败 | 2454 通过 / 12 失败 |
| Boltons | `--doctest-modules boltons tests` | 774 通过 | 774 通过 | 777 通过 / 2 失败 |
| SymPy | core、矩阵 `test_matrices.py`、多项式 `test_polytools.py` | 2366 通过 | 2313 通过 / 53 失败 | 2306 通过 / 60 失败 |
| Pydantic | `test_main.py` + `test_types.py`；另试单独 `test_types.py` | 原生栈溢出 | 同样崩溃 | 同样崩溃 |

通过数不含 skipped/xfail。Requests 各 1 skipped，Rich 各 23 skipped，Lark 各 148 skipped，Packaging 各 1 skipped/434 deselected，SQLAlchemy 各 386 skipped，Sphinx 各 1 skipped/1 xfailed，SymPy 各 85 skipped/24 xfailed/5 deselected。Django 的运行数包含跳过项；Astroid 三版的 skipped/xfail 一致。

结论：8 个仓库在所测范围内结果一致；Astroid、Boltons、SymPy 存在转换后新增失败；Pydantic 无法完成对照。原因与适用边界见[局限性](limitations.md)。

## 工具自身验证

- 工具测试：167 通过、1 跳过（Windows 符号链接权限）；Ruff、格式检查、sdist/wheel 构建通过。
- 全新环境安装发布 wheel 后，版本为 0.3.1，`pip check` 通过。
- 回归覆盖按源码根跨包共享存储、同名文件、跨包/相对/动态导入、容器前缀、脚本、资源、位置锚点、独立源码根、排除规则及 wheel 隔离安装。
- 本地四层业务样本的 20 个可移动文件进入共享存储，原版与 random 版各 261 通过。

## 证据与复现

本地 `repos/evaluation-source-roots/reports/` 保存提交号、命令、依赖、日志、JUnit、`summary.json` 和 `final-render-audit.json`；转换与测试结果分开记录。缺失依赖已补齐并重跑，旧 XML 不作为最终结果；Pydantic 仅保留崩溃日志，不认定通过。

快照生成时清单版本仍为开发期 0.3.0；发布前已用最终 0.3.1 源码重新规划全部输出，核对映射及输出字节哈希。布局版本为 4，清单 schema 为 3；旧测评未覆盖，上游克隆的已跟踪文件未修改。
