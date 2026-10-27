# 真实仓库测评（0.3.2）

## 范围与方法

- 当前仅维护这一套 12 仓库测试集；源码提交、依赖和命令见[配置](../evaluation/python.json)。本轮调整了样本，不能将样本变化视为兼容性改善。
- Windows、Python 3.13.13、UTF-8；seed=42，random 使用 complexity=0.7、max-depth=3，flat/random 均使用默认选择范围。
- 原版和转换版使用相同依赖、命令和断言，核对根模块实际来自对应快照；不修改上游断言或为通过而排除失败用例。
- 12 个原版及 24 个转换版均完成 wheel 构建、安装和源码目录外的隔离导入。**安装成功不代表运行等价。**

## 对照结果

| 仓库 | 测试范围 | 原版 | flat | random |
| --- | --- | --- | --- | --- |
| Requests | utils、structures、hooks、packages、help、adapters | 262 通过 | 相同 | 相同 |
| Flask | `tests/` | 494 通过 | 相同 | 相同 |
| Rich | `tests/` | 984 通过 | 相同 | 相同 |
| Lark | `tests/__main__.py` | 1181 通过 | 相同 | 相同 |
| Packaging | `tests/`，保留默认 property 排除 | 63349 通过 | 相同 | 相同 |
| SQLAlchemy | base、sql、ORM unitofwork；SQLite、纯 Python | 9468 通过 | 相同 | 相同 |
| Django | 官方 runner 的 `utils_tests` | 717 运行 / 49 跳过 | 相同 | 相同 |
| Sphinx | `tests/test_util` | 229 通过 | 相同 | 相同 |
| Boltons | `--doctest-modules boltons tests` | 774 通过 | 相同 | 773 通过 / 1 失败 |
| Click | `tests/` | 687 通过 | 686 通过 / 1 失败 | 686 通过 / 1 失败 |
| attrs | `tests/`，不含可选类型检查 | 1342 通过 | 相同 | 相同 |
| Jinja2 | `tests/` | 909 通过 | 相同 | 相同 |

通过数不含跳过或预期失败；Django 的运行数包含跳过项。Django 对比官方 runner 的统计及退出码，其余项目比较 JUnit 节点与结果；仅按快照根和转换映射归一化路径，不过滤新增节点。

**结论：**flat 下 11 个仓库结果一致，Click 新增 1 个轻量导入断言失败；random 下 10 个一致，另有 Boltons 的 1 个目录布局 doctest 失败。原因见[局限性](limitations.md)。

## 环境与复现

- Click 固定 pytest 8.3.5，避免 pytest 9 的新弃用警告导致上游收集失败；三版统一重跑，不改变警告过滤规则。
- attrs 构建通过标准版本环境变量指定 25.3.0，原版和转换版设置一致，以适应不包含 Git 历史的快照。
- 工具测试：217 通过、1 跳过（Windows 符号链接权限）；Ruff、格式、sdist/wheel 构建及全新环境安装通过，版本为 0.3.2，`pip check` 通过。
- 回归覆盖导入身份、动态命名空间与资源、多进程 spawn、子项目、大资源流式发布，以及 setuptools、Poetry、Hatch、Flit 的 sdist → wheel 验证。
- 整理目录后用最终源码复核 24 份输出，转换映射、输出哈希和快照字节均一致；12 个源码仓库的已跟踪文件保持干净。

[测评脚本](../scripts/evaluate.py)支持固定提交、多种子和追加新仓库；已有仓库结果不会被覆盖。[工作流](../.github/workflows/compatibility.yml)按周或手动运行，原始产物通过 CI 附件提供，克隆和运行产物不随源码提交。

本轮仅验证上述 Windows 环境、seed=42 和所列范围。CI 配置了 Windows/Linux 与多种子，但不将未执行的 CI、完整框架测试或可选后端视为已通过。构建依赖遵循上游配置，可能随时间变化。
