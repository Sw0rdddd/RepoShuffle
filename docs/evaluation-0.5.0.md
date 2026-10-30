# 0.5.0 验证记录

本记录区分 0.5.0 分支验收与 main 合并后的复验，不混用两者结果。布局版本 5、清单 schema 4 不变；[0.4.1 记录](evaluation-0.4.1.md)及更早结果原样保留，不改成 0.5.0 复测。

## 范围与变更

- Java 沿用共享布局和 Adapter，不改包名、源码字节或上游断言；仍严格核对完整测试节点、结果、JAR 类清单及逐个迁移文件的生产编译输入。
- 唯一 Java 公开配置保持十仓库：Commons Lang、CSV、Codec、CLI、Text，Gson、jsoup、Jackson Core、Mockito、SpotBugs。八个 Maven、两个 Gradle 仓库；[替换原因与未覆盖能力](limitations.md#真实仓库替换与未覆盖场景)属于验收范围的一部分。
- 新克隆默认保存仓库本地 `core.autocrlf=false`；profile 可显式声明 `checkout_eol=native`（Windows 为 CRLF，其他平台为 LF），子模块初始化使用相同策略，避免意外继承宿主全局配置。继续遵守上游 `.gitattributes`，不修改 Git 全局配置、不回写夹具、不更改断言。
- Windows CSV 原版在上一配置运行中因 LF/CRLF 差异失败；新的受控 Git 回归先实际复现宿主 `autocrlf=true` 将 LF 变为 CRLF，再检查固定提交的 checkout 字节。不将环境修正前的失败改写为成功。

## 本地验证

Windows / Python 3.13.13 / JDK 25.0.2；受控构建使用 Maven 3.9.9、Gradle 9.1.0。本地未运行真实仓库套件。

- 全量工具测试 **315 通过、1 跳过**（Windows 符号链接权限），包括八项 Maven/Gradle 受控构建用例。
- Git 换行回归在修复前实际失败（LF 被变成 CRLF），修复后在完整测试中通过；子模块参数、版本常量及相关断言同步验证。
- Ruff 检查/格式检查、sdist → wheel 构建、隔离安装、两种 Adapter 发布及生成 Python 独立运行验证通过。
- 80 个本地文档链接/锚点、双语 README 四列示例、历史记录保留及 wheel 与当前源文件/隔离安装字节一致检查通过。

## 远端与合并门槛

0.4.1 的 [f644789 定点矩阵](https://github.com/Sw0rdddd/RepoShuffle/actions/runs/41019054633)中，Commons CLI 双平台及 Commons CSV Linux 通过；CSV Windows 未转换原版因换行差异失败。JSON-java 双平台在构建前被双 Maven/Gradle 标识歧义拒绝，未执行上游测试，因此不作为兼容通过；该候选撤换为 Commons Text，不建立备选配置组。

提交 `6fcaadd` 的 [checks #41020628365](https://github.com/Sw0rdddd/RepoShuffle/actions/runs/41020628365) 八项通过；[首轮完整矩阵 #41020629212](https://github.com/Sw0rdddd/RepoShuffle/actions/runs/41020629212) 最终为 **37/40** 成功（Python 20/20、Java 17/20），并非完整通过。CSV、CLI 双平台已通过；Gson Windows 在安装 Maven 时遇 HTTP 404，未执行上游测试；Commons Text 两版原始节点相同，但转换版各有一个源码读取错误，因此仍判失败。

Text 的失败表达式为包名计算结果与字面文件名片段拼接的 FileReader 路径。随后扩展通用字面路径后缀锚点，不做仓库名特判、不冻结整片实现；增加的三项后缀回归在修复前实际失败，修复后完整工具测试 **319 通过、1 跳过**，并实际编译执行双版包名拼接 FileReader 受控工程。修复后的 Ruff、sdist/wheel、隔离安装、两种 Adapter 发布和 80 个文档链接/锚点及历史/四列示例检查通过。此为后续工作树本地验证，不改写 `6fcaadd` 的真实失败。

提交 `70f977e` 的 [checks #41023454672](https://github.com/Sw0rdddd/RepoShuffle/actions/runs/41023454672) 八项通过；[Java 复验 #41023455806](https://github.com/Sw0rdddd/RepoShuffle/actions/runs/41023455806)中，Text 双平台通过。Gson Windows 此次进入原版构建后，Spotless 要求平台原生 CRLF，而统一 LF checkout 导致原版失败；它与前一轮 Maven 下载 404 是不同原因，均保留原记录。

随后仅为 Gson profile 声明通用 `checkout_eol=native` 环境策略，不关闭格式校验、不改源码/夹具/断言。受控 Git 用例在切回旧 LF-only 算法时实际失败；当前测评单测 **31 通过**，覆盖默认/LF/native、子模块和非法配置。该定点验证不冒充新工作树完整工具或真实仓库通过。

## 完整分支验收通过

提交 `b20bf1280697cfa3467ee166dba891cf76cd5551` 的 [checks #41025895200](https://github.com/Sw0rdddd/RepoShuffle/actions/runs/41025895200) **8/8 通过**；[完整矩阵 #41025895474](https://github.com/Sw0rdddd/RepoShuffle/actions/runs/41025895474) **40/40 对照任务通过**（另有一个成功的配置生成任务）。Python **20/20**、Java **20/20**，均覆盖十仓库与 Windows/Linux；Java random 使用 seed=42/123，Python flat/random 均使用两个 seed。Java 的上游测试、节点/结果、JAR 类清单及迁移源码生产编译输入覆盖全部通过既有严格验收。

这一结果满足合并门槛，但只覆盖当前公开选集，不补写被替换仓库的兼容结论。main 合并后自动触发的检查与完整矩阵仍须单独观察；尚未完成的主分支复验不沿用分支通过数。
