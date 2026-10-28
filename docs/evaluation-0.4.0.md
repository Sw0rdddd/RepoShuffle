# 验证记录（0.4.0）

本记录对应 `feat/java-adapter` 的本地工作树，不代表已发布版本或远端 CI 通过。工具版本 0.4.0、布局版本 5、清单 schema 4。

## 本地已执行

环境：Windows、Python 3.13.13；Java 定点使用 JDK 25.0.2、Maven 3.9.9、Gradle 9.1.0。构建工具临时下载后核验官方校验值，未执行任何真实上游仓库测试。

| 检查 | 实际结果 |
| --- | --- |
| 完整工具单测，包含受控 Java 构建用例 | 277 passed、1 skipped |
| 最后将 Python 路径提示移入 Adapter 配置后的受影响范围回归 | 89 passed、1 skipped、4 deselected |
| Ruff check / format check | 通过 |
| sdist / wheel 构建及 wheel 隔离目录安装 | 通过；安装版本 0.4.0，两种 Adapter 均可规划/发布 |
| 随机分配器抽取前后比较 | 216 组输入、种子、深度、复杂度组合的映射完全相同 |
| 工作流 YAML 解析、两种语言配置检查 | 通过；每种语言十仓库 |

唯一跳过项为 Windows 未授予创建符号链接权限的安全用例。最终范围回归未重复执行的四项，是已纳入完整单测的 Maven/Gradle 单/多模块构建用例，不是上游测试跳过。

Java 定点工程均由本工具测试构造：

- Maven / Gradle × 单模块 / 有依赖的多模块，分别构建 original、shuffled；执行真实 JUnit 测试，严格比较报告节点/结果及 JAR 类清单，再从生成的 JAR 运行断言。
- JAR 运行验证包内访问、反射类身份及资源读取；另用 javac/java 验证普通工程与简单 JPMS 工程。
- 验证同名文件、生产包名包含 test/fixtures 等词、测试/资源保护、构建产物排除、显式源码根、Maven 路径过滤变化阻断，以及 dry-run 不执行构建。
- 用第三种测试 Adapter 验证公共规划与发布不要求 Python 模块语义；未引入运行时插件系统。
- 从构建的 wheel 隔离安装后验证版本、两种 Adapter 和清单，再以禁用 site-packages 的 Python 执行生成项目，确认 Python 输出自包含。

复现完整工具单测需要 JDK、Maven 和 Gradle 可用；工具不在 PATH 时，可用 `REPOSHUFFLE_MAVEN` / `REPOSHUFFLE_GRADLE` 指定可执行文件。没有构建工具时，相关四项会明确跳过，不能将此视为构建验证通过。

## README Java 示例追加验证

在相同本地 Python/JDK 环境下，以八个 Java 生产文件实际生成 random、seed=42、complexity=0.7、max-depth=3 的 README 目录树。八个文件均实际迁移，编译根仍为 `src/main/java`。

两版分别用 `javac --release 17` 编译，再生成 JAR 并在本地 JDK 25.0.2 上运行，验证正常导入、`Class.forName` 类身份和包内资源读取；JAR 类清单一致。POM、测试源码和资源文件的路径/字节保持不变。此项不是 Maven/Gradle 构建验证，也没有执行上游测试或真实仓库 CI；此前单测计数不因本次示例验证而改写。

## 真实仓库矩阵：尚未执行

- [Python 配置](../evaluation/python.json)保留原来的十仓库、flat/random 范围。
- [Java 配置](../evaluation/java.json)固定十仓库的真实提交、JDK、构建版本、生产源码根、任务、报告和制品范围；引用与提交已通过远端元数据核实，不等于已完成构建测试。
- 六个 Maven 项目执行声明的 `clean verify`；四个 Gradle 项目覆盖声明的核心构建单元：Mockito core、Caffeine core、JUnit platform-commons/jupiter-api、SpotBugs 实现/注解及其指定测试任务。选定单元的生产源码实际迁移，不为测试结果添加仓库名称特判。
- 工作流配置 Windows/Linux、seed=42/123；Java 只运行 random，按配置使用 JDK 17 或 21，构建工具版本与本地定点环境不完全相同。
- 测评要求非空测试报告、相同节点及重复次数/结果、非空 JAR 类清单且清单一致；不关闭上游断言、不增加 skip 参数、不用原路径源码副本代替迁移。报告保存本次使用的完整 profile。

完整对照留给 GitHub Actions。当前没有触发远端运行，也没有把旧结果改写成 0.4.0 复测；历史说明合并至[统一测评页](evaluation.md#历史验证记录)。支持范围及尚需单独验证的构建行为见 [Java 与扩展架构](java.md)、[局限性](limitations.md)。
