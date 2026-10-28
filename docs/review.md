# 代码审查与修复跟踪

## 0.4.0 原始发现

范围：`feat/java-adapter` 的公共核心、Java Adapter、测评入口及文档口径。以下四项在 0.4.0 审查时均以受控小型样例复现，当时尚未修复，未修改生产源码或触发 Actions。原始反例保留如下；0.4.1 的修复与实际复验见末节。

## P1：迁移数量不能证明参与了编译

位置：[Java 过滤检查](https://github.com/Sw0rdddd/RepoShuffle/blob/1175dec/src/reposhuffle/adapters/java.py#L155-L211)、[JAR 清单](https://github.com/Sw0rdddd/RepoShuffle/blob/1175dec/scripts/evaluate.py#L295-L311)、[验收函数](https://github.com/Sw0rdddd/RepoShuffle/blob/1175dec/scripts/evaluate.py#L510-L514)。

- 构造 `demo/Main.java` 与 `excluded/Unused.java`，Maven 过滤为 `**/Unused.java`，显式 `source_dirs=["src/main/java/excluded"]`，只选择迁移 Unused（测评 profile 支持该参数）。该过滤在迁移前后均排除 Unused，因此规划通过，`moved_files=1`，Main 留在原位。
- 以真实 javac 分别编译两版未移动的 Main，生成的两份 JAR 都只有 `demo/Main.class`。向验收函数传入相同的完整测试节点和成功构建记录，得到 `acceptable=True`。
- 这证明验收谓词不能拒绝“只迁移未参与构建的文件”。该复现使用真实 javac/JAR，但测试节点和构建退出记录为受控构造，**没有执行完整 Maven 测评或上游测试**。

影响：可把未发生有效实现迁移的对照判为成功，违背必须实际迁移参与构建实现的要求。类名清单相等并不能证明文件被编译。

建议：区分不参与构建的文件，记录编译输入/生产源码单元与迁移之间的证据；按实际构建覆盖验收，不能只看全仓库非空类清单和全局移动数量，也不能简单按 Java 文件名猜测 class 名。

## P1：显式源码根可将文件移出已知编译根

位置：[源码根覆盖](https://github.com/Sw0rdddd/RepoShuffle/blob/1175dec/src/reposhuffle/adapters/java.py#L290-L316)、[存储根分配](https://github.com/Sw0rdddd/RepoShuffle/blob/1175dec/src/reposhuffle/adapters/java.py#L450)。

标准 Maven 输入为 `src/main/java/demo/Main.java`，传入 `source_dirs=["src"]` 后，规划仍 `ok=True`，目标为 `src/_internal_code/Main.java`，已不在实际默认编译根 `src/main/java` 内；POM 未改写。构造规划即可复现，没有执行 Maven 构建。

影响：生成静态通过但默认构建漏编译的输出。声明生产根和选择实现范围被混为一谈；对应的现有宽根测试只检查测试/资源未移动，没有检查主源码仍被构建收集。

建议：分开真实构建根和用户选择；对已知字面 Maven 编译根检查包含关系。不能无条件用显式根替换可确认的构建边界，也不能通过关闭检查或回填原路径副本获得通过。

## P2：存储前缀未按大小写不敏感规则避让

位置：[storage_prefix](https://github.com/Sw0rdddd/RepoShuffle/blob/1175dec/src/reposhuffle/transform.py#L9-L20)。该比较沿用原 Python 实现，抽取到共享层后仍存在。

在生产根放置 `_INTERNAL_code/data.txt`，新存储仍选 `_internal_code`；Windows 上规划 `ok=True`，`apply_plan` 创建大小写等价目录时触发 `FileExistsError`，未能发布。

影响：两种语言共用的便携目标检查不完整，可能规划成功而发布失败。输入目录本身并不存在大小写冲突。

建议：命名前缀与文件/目录目标采用一致的 casefold 碰撞规则，在规划阶段检查完整目标目录集合。

## P2：gitignored 子项目配置仍阻塞规划

位置：[构建发现](https://github.com/Sw0rdddd/RepoShuffle/blob/1175dec/src/reposhuffle/adapters/java.py#L74-L105)、[POM 分析](https://github.com/Sw0rdddd/RepoShuffle/blob/1175dec/src/reposhuffle/adapters/java.py#L240-L243)。

正常 Maven 项目设置 `.gitignore` 的 `vendor/`，其下放置无效 `vendor/pom.xml`。虽然后续文件角色会遵循忽略规则，构建发现仍提前解析该 POM，得到阻塞的 `JAVA_BUILD_CONFIGURATION`，使正常主项目规划失败。

建议：语义构建发现应复用公共清单/忽略上下文，不分析不参与转换的 gitignored 子项目；仍按既有策略复制、原位保留这些文件，不能借修复删除输入数据。

## 验证与处理状态

0.4.0 审查只执行上述受控规划/发布、javac 编译和验收谓词探针，未运行真实仓库测试或复用旧通过数作为复验；当时四项保持开放，历史工具单测通过不覆盖这些场景。

## 0.4.1 修复与复验

四项已通过本地针对性回归，不能据此宣称完整真实仓库矩阵通过。版本、环境、测试计数及远端入口见[0.4.1 记录](evaluation-0.4.1.md)。

| 原始问题 | 修复及实际验证 |
| --- | --- |
| 迁移未编译代码也可验收 | Maven 拒绝迁移已识别过滤器排除的源码。测评读取 Maven compile 输入及 Gradle main JavaCompile 成功任务输入，要求每个迁移文件在两版均被生产编译选择；缺证据即失败。真实 Gradle 反例中 Unused 两版都被测试编译、都不在生产 JAR 内，JAR 清单一致，覆盖检查仍明确拒绝。 |
| 显式根逃逸编译输入 | 保存字面 Maven 编译根，拒绝将标准根扩大至 src/src/main 或缩小至包目录；声明的自定义/额外根仍可实际迁移，不改 POM、不回填源码副本。未知动态模型继续要求实际构建验证。 |
| 大小写不敏感碰撞 | 共享前缀按 casefold 避让；完整文件/目录目标集合在规划阶段检查。Python/Java 均实际发布带 _INTERNAL_code 和 _Internal1_runtime.py 的样例；模拟目录大小写冲突和文件/目录冲突均被拒绝。 |
| 忽略配置阻塞规划 | 构建发现使用公共遍历的忽略上下文，不解析被忽略的 POM/Gradle 配置；原样保留忽略文件及其数据目录。覆盖 Maven、Gradle 以及重新包含有效子项目而忽略父 POM 的场景。 |

新增回归在修复前确认四类原始行为失败，修复后通过；生产源码、测试和版本常量均已修改，不以文档变更替代修复。
