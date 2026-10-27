# RepoShuffle 0.3.1 验证记录

环境：Windows / Python 3.13.13；seed=42，complexity=0.7，max-depth=3；flat 和 random 均使用默认选择范围。

## 工具与跨包功能

- 完整工具测试：167 passed，1 skipped；跳过项为 Windows 符号链接权限。
- Ruff 检查、格式检查、sdist/wheel 构建通过；全新环境安装发布 wheel 后 `--version` 为 0.3.1，`pip check` 通过。
- 覆盖任意名称源码根、多包共享存储、同名文件、跨包和相对导入、动态导入、容器前缀、位置锚点、独立源码根，以及共享存储的 wheel 安装。
- 本地四层业务样本的 20 个可移动业务文件进入同一共享存储；原版与 random 版各 261 passed。

## 真实仓库对照

`repos/` 中 12 个源码仓库均完成 flat/random 转换和静态检查，共 24 份输出；原版与转换版的根模块导入均检查了实际源码位置。测试在生成的 original/shuffled 快照中运行，同一项目共用相同依赖环境及相同测试命令，不修改上游测试断言。

| 仓库 | 实际测试范围 | 原版 | flat | random |
| --- | --- | --- | --- | --- |
| Requests | utils、structures、hooks、packages、help、adapters 核心集 | 262 passed | 262 passed | 262 passed |
| Flask | `tests/` | 494 passed | 494 passed | 494 passed |
| Rich | `tests/` | 984 passed | 984 passed | 984 passed |
| Lark | `tests/__main__.py` 聚合入口 | 1181 passed | 1181 passed | 1181 passed |
| Packaging | `tests/`，保留上游默认 property 排除 | 63349 passed | 63349 passed | 63349 passed |
| SQLAlchemy | `test/base`、`test/sql`、`test/orm/test_unitofwork.py`，SQLite、pure Python | 9468 passed | 9468 passed | 9468 passed |
| Django | 官方 runner 的 `utils_tests` | 717 run / 49 skipped | 相同 | 相同 |
| Sphinx | `tests/test_util` | 229 passed | 229 passed | 229 passed |
| Astroid | `tests/`，保留上游默认 acceptance 排除 | 2461 passed / 5 failed | 2453 passed / 13 failed | 2454 passed / 12 failed |
| Boltons | `--doctest-modules boltons tests` | 774 passed | 774 passed | 777 passed / 2 failed |
| SymPy | core 测试、矩阵 `test_matrices.py`、多项式 `test_polytools.py` | 2366 passed | 2313 passed / 53 failed | 2306 passed / 60 failed |
| Pydantic | `test_main.py` + `test_types.py`；另试单独 `test_types.py` | Windows 栈溢出 | 同样崩溃 | 同样崩溃 |

通过数不包含 skipped/xfail。Requests 各 1 skipped，Rich 各 23 skipped，Lark 各 148 skipped，Packaging 各 1 skipped/434 deselected，SQLAlchemy 各 386 skipped；Sphinx 各 1 skipped/1 xfailed；SymPy 各 85 skipped/24 xfailed/5 deselected。Django 的 717 run 包含 49 skipped。Astroid 的 skipped/xfail 在三版一致，原版的 5 个失败不计为新增失败。

## 未解决的兼容性限制

- Astroid 的静态模块查找、源码路径推导等测试在 flat/random 分别新增 8/7 个失败。
- Boltons random 的物理模块扫描额外收集 5 个测试节点；缓存键的物理模块身份、目录遍历 doctest 各失败 1 项。flat 的通过数相同。
- SymPy 的物理源码遍历会绕过逻辑路由、重复加载实现并影响类型和单例身份；这批测试在转换后出现 53/60 个失败。此前测评也已记录这一限制，本次未修改这些测试或扩大本功能的修复范围。
- Pydantic 原版和两种转换版在循环验证用例原生栈溢出，退出码 3221225725；单独运行类型测试也在另一个验证默认值用例栈溢出。没有完整 JUnit 结果，不能据此判定测试通过或功能等价。

初次运行缺失的测试依赖已补齐，受影响范围已重跑；缺失依赖和崩溃前遗留的 XML 不计作最终测试结果。Pydantic 的崩溃保留日志，不用较早的依赖错误 XML 替代。

## 证据与复现边界

本地 `repos/evaluation-source-roots/reports/` 保存提交号、测试命令、依赖列表、日志、JUnit、summary.json 和 final-render-audit.json；测试与转换结果分开记录。旧测评目录未覆盖，上游克隆的已跟踪文件保持干净。

转换快照先于版本号更新生成，清单仍记录开发时的 0.3.0。发布前使用最终 0.3.1 源码重新规划全部 24 份输出，逐项核对映射和清单中的输出字节哈希；版本号字段变化不影响这些运行源码。布局版本为 4，清单 schema 仍为 3。

这轮结果仅覆盖所列 Windows 环境和测试范围；完整框架测试、未安装的可选后端及其他平台不计为本地已验证。
