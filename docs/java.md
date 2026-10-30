# Java 使用指南

首期提供 random 物理迁移，不支持 flat。0.4.1 对[四项审查问题](review.md)增加修复与回归；这不构成对所有 Maven/Gradle 工程的完整兼容保证。

```bash
reposhuffle inspect ./demo --language java
reposhuffle shuffle ./demo --language java --mode random --seed 42 --dry-run
reposhuffle shuffle ./demo --language java --mode random --seed 42
```

## 迁移行为

- 真正移动生产 `.java` 文件，保持文件名和原始字节，不改 package/import/类名，不保留原路径源码副本，不生成加载器。随机分配后保留原源码根相对路径作为后缀，以保持包路径检查所需的目录结尾。
- 首期按源码根和已识别的构建单元分组；识别标准 `src/main/java`，支持 Gradle 子项目继承根构建配置。发现并非完整有效 POM 或 Gradle 模型解析。
- 测试、资源、其他语言和原目录保留；`module-info.java`、`package-info.java` 及可静态识别的字面源码路径/文件名引用保留为锚点。约定生产版本根（`src/main/java9`、`src/mainRelease9/java` 等）与普通根同相对路径的覆盖文件也保留，避免模块源码合并产生重复类；不因此冻结整个源码目录。存储区放在可移动文件的公共父目录内，尽量不改变外层目录形状。
- 已识别 Maven Javadoc 插件使用实际文件列表；源码插件过滤随迁移路径适配并核对原选择结果。无实际选择变化时保留原插件字节，需要改写时保留换行格式。不禁用 doclint、Werror、测试或其他上游检查，变更记入清单。
- `--max-depth` 限制随机编号树，Java 包路径后缀另计。清单的 `allocation_directories` 记录编号分配位置，`physical_files_by_depth` 记录相对存储根的实际深度。
- 规划无需 JDK，不执行 Maven/Gradle，不检查 Java 语法。`plan.ok` 只是当前检查结果，不证明编译、运行或发布成功。

## 显式生产源码根

源码根应与构建工具**实际递归编译的生产根一致**。选择其中一部分文件用 `--include` / `--exclude`，不要通过扩大 `--source-dir` 到 `src` 等父目录选择范围：已知字面 Maven 编译根不允许这样扩大或缩小；无法静态确认的根仍需实际编译输入验证。

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

发布本身不包含 Git 历史；需要 VCS 的测评可用 profile 字段 `"vcs": true` 补充真实固定提交的共享对象元数据，并保留真实 dirty 状态，不伪造干净仓库。输出后仍按原项目要求独立构建和测试。构建、资源和路径兼容边界集中在[局限性](limitations.md)，扩展接口见[架构](architecture.md)，真实仓库配置与验证状态见[测评方法](evaluation.md)。
