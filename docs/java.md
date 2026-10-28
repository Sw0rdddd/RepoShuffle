# Java 使用指南

首期提供 random 物理迁移，不支持 flat。当前存在[已复现的开放问题](review.md)，不能将其描述为对所有标准 Maven/Gradle 工程的完整兼容保证。

```bash
reposhuffle inspect ./demo --language java
reposhuffle shuffle ./demo --language java --mode random --seed 42 --dry-run
reposhuffle shuffle ./demo --language java --mode random --seed 42
```

## 迁移行为

- 真正移动生产 `.java` 文件，保持文件名和原始字节，不改 package/import/类名，不保留原路径源码副本，不生成加载器。同名文件分配到不同目录。
- 首期按源码根和已识别的构建单元分组；识别标准 `src/main/java`，支持 Gradle 子项目继承根构建配置。发现并非完整有效 POM 或 Gradle 模型解析。
- 测试、资源、其他语言和构建脚本默认保留原位；`module-info.java`、`package-info.java` 保留为描述符锚点。
- 规划无需 JDK，不执行 Maven/Gradle，不检查 Java 语法。`plan.ok` 只是当前检查结果，不证明编译、运行或发布成功。

## 显式生产源码根

源码根应与构建工具**实际递归编译的生产根一致**。选择其中一部分文件用 `--include` / `--exclude`，不要通过扩大 `--source-dir` 到 `src` 等父目录选择范围：当前实现未可靠验证这种扩大，可能把文件移出编译输入。

```bash
reposhuffle shuffle ./demo --language java --source-dir module/src/main/java
```

自定义根必须已经存在于原构建配置中；例如构建工具原本使用 `module/production`，可在输入根目录的 `reposhuffle.toml` 声明：

```toml
[java]
source-dirs = ["module/production"]
```

API 为 `build_plan(..., language="java", source_dirs=[...])`，显式参数优先于该文件。声明只提供发现信息，不修改实际构建输入，也不能证明动态脚本、继承配置或过滤规则已适配。`--include` 不用于把测试或生成产物升级为实现。

## 单语言与后续验证

`--language auto|python|java` 默认 auto，优先根构建标识，无标识时检查非辅助目录源码；识别有歧义时要求显式选择。复杂工作区建议指定语言和 `--subproject`。一次只转换一种语言，不联合编排构建。

输出后仍按原项目要求独立构建和测试。构建、资源和路径兼容边界集中在[局限性](limitations.md)，扩展接口见[架构](architecture.md)，真实仓库配置与验证状态见[测评方法](evaluation.md)。
