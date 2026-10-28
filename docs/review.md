# 代码审查（0.4.0 工作树）

范围：`feat/java-adapter` 的公共核心、Java Adapter、测评入口及文档口径。以下四项均已用新建的受控小型样例复现，**尚未修复**；本轮不修改生产源码、测试断言或真实仓库配置，不触发 Actions。

## P1：迁移数量不能证明参与了编译

位置：[Java 过滤检查](../src/reposhuffle/adapters/java.py#L155-L211)、[JAR 清单](../scripts/evaluate.py#L295-L311)、[验收函数](../scripts/evaluate.py#L510-L514)。

- 构造 `demo/Main.java` 与 `excluded/Unused.java`，Maven 过滤为 `**/Unused.java`，显式 `source_dirs=["src/main/java/excluded"]`，只选择迁移 Unused（测评 profile 支持该参数）。该过滤在迁移前后均排除 Unused，因此规划通过，`moved_files=1`，Main 留在原位。
- 以真实 javac 分别编译两版未移动的 Main，生成的两份 JAR 都只有 `demo/Main.class`。向验收函数传入相同的完整测试节点和成功构建记录，得到 `acceptable=True`。
- 这证明验收谓词不能拒绝“只迁移未参与构建的文件”。该复现使用真实 javac/JAR，但测试节点和构建退出记录为受控构造，**没有执行完整 Maven 测评或上游测试**。

影响：可把未发生有效实现迁移的对照判为成功，违背必须实际迁移参与构建实现的要求。类名清单相等并不能证明文件被编译。

建议：区分不参与构建的文件，记录编译输入/生产源码单元与迁移之间的证据；按实际构建覆盖验收，不能只看全仓库非空类清单和全局移动数量，也不能简单按 Java 文件名猜测 class 名。

## P1：显式源码根可将文件移出已知编译根

位置：[源码根覆盖](../src/reposhuffle/adapters/java.py#L290-L316)、[存储根分配](../src/reposhuffle/adapters/java.py#L450)。

标准 Maven 输入为 `src/main/java/demo/Main.java`，传入 `source_dirs=["src"]` 后，规划仍 `ok=True`，目标为 `src/_internal_code/Main.java`，已不在实际默认编译根 `src/main/java` 内；POM 未改写。构造规划即可复现，没有执行 Maven 构建。

影响：生成静态通过但默认构建漏编译的输出。声明生产根和选择实现范围被混为一谈；对应的现有宽根测试只检查测试/资源未移动，没有检查主源码仍被构建收集。

建议：分开真实构建根和用户选择；对已知字面 Maven 编译根检查包含关系。不能无条件用显式根替换可确认的构建边界，也不能通过关闭检查或回填原路径副本获得通过。

## P2：存储前缀未按大小写不敏感规则避让

位置：[storage_prefix](../src/reposhuffle/transform.py#L9-L20)。该比较沿用原 Python 实现，抽取到共享层后仍存在。

在生产根放置 `_INTERNAL_code/data.txt`，新存储仍选 `_internal_code`；Windows 上规划 `ok=True`，`apply_plan` 创建大小写等价目录时触发 `FileExistsError`，未能发布。

影响：两种语言共用的便携目标检查不完整，可能规划成功而发布失败。输入目录本身并不存在大小写冲突。

建议：命名前缀与文件/目录目标采用一致的 casefold 碰撞规则，在规划阶段检查完整目标目录集合。

## P2：gitignored 子项目配置仍阻塞规划

位置：[构建发现](../src/reposhuffle/adapters/java.py#L74-L105)、[POM 分析](../src/reposhuffle/adapters/java.py#L240-L243)。

正常 Maven 项目设置 `.gitignore` 的 `vendor/`，其下放置无效 `vendor/pom.xml`。虽然后续文件角色会遵循忽略规则，构建发现仍提前解析该 POM，得到阻塞的 `JAVA_BUILD_CONFIGURATION`，使正常主项目规划失败。

建议：语义构建发现应复用公共清单/忽略上下文，不分析不参与转换的 gitignored 子项目；仍按既有策略复制、原位保留这些文件，不能借修复删除输入数据。

## 验证与处理状态

本轮只执行上述受控规划/发布、javac 编译和验收谓词探针，未运行真实仓库测试或复用旧通过数作为复验。四项均保持开放；历史工具单测通过不覆盖这些场景。修复需另行修改源码、增加针对性回归并更新版本及验证记录，不能仅通过改文档消除缺陷。
