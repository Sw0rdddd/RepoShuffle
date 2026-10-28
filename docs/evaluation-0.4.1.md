# 修复验证记录（0.4.1）

本记录对应 `feat/java-adapter` 的 0.4.1 修复。布局版本 5、清单 schema 4 不变；0.4.0 及更早记录不改写为本次复测。

## 本地实际执行

Windows / Python 3.13.13 / JDK 25.0.2，受控构建使用 Maven 3.9.9、Gradle 9.1.0（下载校验和已核对）。没有在本地或 Docker 重跑真实仓库矩阵。

- 四类原始缺陷的 8 项新增回归在修复前均失败；另有 2 项完整目标目录碰撞回归在增加校验前失败。
- 全量工具测试：**291 通过、1 跳过**（Windows 符号链接权限），包含 Maven/Gradle 单模块与多模块的真实编译、测试、JAR 运行及生产输入路径覆盖。
- 随后收紧 Gradle 跟踪为 main 编译任务，并新增测试编译反例、自定义 Maven 根及 gitignore 重新包含回归：受影响范围 **64 通过、1 跳过、2 未选**。未选的两项为此前全量中已通过的 Maven 受控构建；本次实际重跑 Gradle 单/多模块与新增反例。
- 追加 Maven 原生输入报告归档后，相关定点回归 **52 通过、5 未选**（五项受控构建已在前述运行执行）；70 个本地文档链接/锚点、四列示例及历史记录保留检查通过。
- Ruff 检查/格式检查、sdist → wheel 构建、wheel 隔离目标安装及两种 Adapter 发布验证通过；生成的 Python 快照可独立运行。
- Gradle 反例两版构建成功，Unused 均生成测试 class、均不进入生产 JAR，且两版 JAR 类清单相同；生产输入覆盖均明确指出迁移文件缺失，不接受该对照。

## 验收证据与边界

静态规划拒绝已知 Maven 编译根扩大/缩小及已识别过滤器排除的迁移；共享前缀按 casefold 避让，并检查完整文件/目录集合。忽略的构建配置不做语义分析，但普通输入数据仍复制、原位保留。

测评要求每个迁移 Java 文件在原版与转换版都存在于生产编译输入：Maven compile/inputFiles.lst；Gradle main JavaCompile 成功任务的 source（禁用构建缓存、强制重跑）。缺少证据即失败，不把测试编译当作生产迁移，不按文件名猜 class，不回填原路径源码或放宽测试节点比较。

这不是对所有编译器、动态配置、插件或运行行为的等价性证明；更多边界见[局限性](limitations.md)，逐项修复见[审查记录](review.md)。

## 远端验证入口

提交本记录时，0.4.1 的分支检查和完整真实仓库矩阵尚未执行，不预写通过结论。先在分支检查 [checks](https://github.com/Sw0rdddd/RepoShuffle/actions/workflows/ci.yml) 及 [完整真实仓库矩阵](https://github.com/Sw0rdddd/RepoShuffle/actions/workflows/compatibility.yml)，没有阻塞后才合并 main，再观察自动运行；以对应提交的日志和附件为准。
