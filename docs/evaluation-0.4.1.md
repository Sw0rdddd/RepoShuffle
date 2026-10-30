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

## 远端实际结果与合并状态

提交 `56468f1` 的 [checks #40706731989](https://github.com/Sw0rdddd/RepoShuffle/actions/runs/40706731989) 八个工具检查任务全部通过。[完整矩阵 #40706732847](https://github.com/Sw0rdddd/RepoShuffle/actions/runs/40706732847) 已完成：Python **20/20**，Java **5/20**，合计 **25/40** 对照任务成功，不能写成完整通过；main 未合并。

Java 成功的是 Jackson Core、Mockito 的双平台，以及 Commons Codec 的 Windows。失败证据包括：Gson 的 PackageLocation 警告被 Werror 拒绝；Commons IO 的 10 项源码目录遍历断言失败；jsoup Javadoc 失败且缺少原生编译输入记录；SpotBugs 原版也因无 Git 元数据而失败；Caffeine 超时/构建失败；部分用例通过/跳过状态不一致。

另定位并修正两项工具问题：显式源码根不再分析无关嵌套构建 fixture（仍完整复制），JUnit 聚合允许合法的零测试容器报告，但仍要求总结果含真实测试节点，拒绝丢失节点或错误报告。使用保存的 Commons Lang Linux XML 做只读重解析，两种种子的 11469 通过、43 跳过及全部节点一致；这不是重新运行上游测试，也不将旧失败运行改写为成功。Windows 的实际跳过差异仍保留为失败。

以上是 `56468f1` 的真实失败记录，不会改写为后续修复的通过证据。保持严格验收，存在阻塞时不合并 main。

## 首轮 Actions 后的兼容性增强

- Java 继续共用分配器，载荷保留原根相对路径后缀，存储位于公共源目录内，并保留原目录。字面 File/Path 引用可保留个别源码锚点，不冻结整个实现目录；实际迁移文件仍逐一核对生产编译输入。
- 已识别 Maven Javadoc 改为真实源文件列表；源码/Javadoc 路径过滤随迁移调整，并核对选择集合，不关闭原检查。非增量编译增加实际 javac 文件对象跟踪，限定生产 compile 阶段，兼容本地化诊断，不按类名猜文件。
- 声明 VCS 的 profile 使用真实固定提交、origin 和共享对象元数据；原版/转换版保留真实 dirty 状态。不会检出原路径源码、伪造提交或伪造干净状态，发布本身仍不复制 Git 历史。
- 报告采用流式 XML/JSON；超时清理子进程，原版失败明确终止无效对照。Caffeine 修正派生测试报告收集范围，保留所有原测试，声明资源及 CI 时间预算；其他环境稳定性设置两版一致。
- README 的 0.4.1 八文件 Java 示例已重新实际生成，并分别用 javac --release 17 编译两版、打包运行 JAR，核对八个类、导入、反射和资源；四列布局保持不变，省略保留的空目录。

这些增强完成后，本地完整工具测试 **306 通过、1 跳过**（Windows 符号链接权限），包含七项真实 Maven/Gradle 构建用例、字面源码访问的双版编译运行、真实 Git 元数据及超时子进程清理。Ruff 检查/格式检查（46 个文件）、sdist/wheel、隔离安装及生成 Python 独立运行验证通过；70 个本地文档链接/锚点、四列示例及历史记录保留检查通过。工作流矩阵验证默认二十 profile、定点选择、超时预算和未知项目拒绝。

这些增强的远端复验尚未记录为通过；以新提交的 Actions 为准，不沿用首轮或历史通过数。

## 第二轮 Actions 与后续修复

提交 `afce126` 的 [checks #40956275719](https://github.com/Sw0rdddd/RepoShuffle/actions/runs/40956275719) 八项通过；[完整矩阵 #40956275308](https://github.com/Sw0rdddd/RepoShuffle/actions/runs/40956275308) 已结束：Python **20/20**，Java **8/20** 通过、11 失败、1 取消，合计 **28/40** 成功，main 仍未合并。

Java 通过项为 jsoup/Jackson Core 双平台，以及 Gson、Mockito、Commons Lang 的 Linux 和 Commons Codec 的 Windows。剩余证据包括：Gson Windows 的 POM 换行格式、JUnit5 模块源码覆盖合并重复类、Commons IO 两项文件遍历深度断言、Commons Lang Windows 的真实跳过差异、Mockito Windows 一项时间敏感失败、Codec Linux 原版堆不足。SpotBugs 两版测试和类清单一致，但 GUI 生产 source set 的 78 个迁移文件未被仅跟踪 main 的采集器记录，仍按证据缺失判失败。Caffeine Windows 原版异步测试超时；Linux 对照任务达到六小时上限取消，未得到完整结果。另确认 Caffeine 上游 XML 实际位于各任务的 `build/reports/*/results`，前一配置未收集到，旧空报告不补写为有效结果。

后续改动分别处理插件无操作改写/CRLF、个别字面文件名及生产版本覆盖锚点、按选中生产 JAR 实际 class 输入关联 JavaCompile 证据，并启用调度中 Test 任务的 XML 报告。测试编译反例仍要求被拒绝；不依靠冻结整个实现区或放宽节点比较。环境调整保持原版/转换版一致；按时间预算拆分长任务 seed，各 seed 保留完整原测试范围。新改动的本地及远端复验结果另行记录，不沿用上述通过数。

本轮本地完整工具测试 **314 通过、1 跳过**（Windows 符号链接权限），包含八项 Maven/Gradle 受控构建用例。新增文件名锚点、两项 CRLF/无操作及版本覆盖回归，在内存切回 `afce126` 算法时四项均失败，恢复当前实现后通过；版本覆盖双版均以 javac 编译执行。自定义生产 source set 确实进入 JAR，采集器记录其源码；未选中 JAR 时拒绝证据，测试编译反例仍未获误认。原先关闭的 Gradle XML 报告也在受控工程中采集到真实测试结果。工作流回归核对二十 profile、两个 seed 完整覆盖、长任务预算和未知选择拒绝。

Ruff 检查/格式检查、sdist → wheel 构建、隔离安装和两种 Adapter 发布验证通过；生成 Python 可独立运行。70 个文档链接/锚点、四列示例及历史记录保留检查通过。本轮远端复验尚未记录为通过，仍不合并 main。
