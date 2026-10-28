# 扩展架构

公共核心负责布局和安全发布，语言差异通过内置 Adapter 处理。一次转换一种语言，不加载目标项目插件，不执行目标构建脚本来完成发现，也不编排跨语言联合构建。

## 公共流水线

语言识别 → 清单/源码分析 → Adapter 形成移动边界 → 共享布局分配 → Adapter 渲染与检查 → 公共检查 → 哈希校验及配对发布。

| 模块 | 职责 |
| --- | --- |
| `inventory.py`、`paths.py` | 遍历、忽略规则、项目选择与字节清单 |
| `contents.py`、`transform.py` | 磁盘后备内容、存储名分配及可审计迁移 |
| `layout.py` | 唯一随机分配器与布局统计，不感知包或类 |
| `plan.py`、`documentation.py` | 只读规划和公共文档引用处理 |
| `output.py` | 输入/计划变化检查、配对快照和清单发布 |

随机种子、复杂度、容量和深度计算共用；重命名约束、跨文件关联及可移动范围由 Adapter 决定。Python flat 的命名处理留在 Python Adapter，不把其规则套到 Java。统计使用显式存储根，而非目录名推断。

## 接入一种语言

实现 `src/reposhuffle/adapters/base.py` 的结构化接口，并注册到 `adapters/__init__.py`：

- `name`、`markers`、`suffixes`：识别标识；CLI 语言选项来自同一注册表。
- `modes`、`strategy`、`static_checks`：能力与实际检查声明。
- `inspect`：源码和构建边界、保留原因、阻塞诊断；不执行目标代码。
- `explain`：兼容说明，不能把静态推断写成运行验证。
- `render`：调用共享分配器，处理语言所需的改写或辅助文件。
- `validate`：检查该语言的约束；复杂构建无法证明时明确限制，不添加仓库名称特判。

Python Adapter 封装导入路由、脚本入口、构建交付及语法编译；Java Adapter 保持源码字节和文件名，处理生产源码根及已识别的构建过滤。实际缺陷见[审查记录](review.md)，此处职责划分不等同于所有边界已实现正确。

新增真实仓库执行支持时，实现测评 runner，共用 `scripts/evaluate.py` 的调度、快照、日志和结果对照；不复制整套工具或预建第三方插件框架。

## 公共模型与清单

`SourceUnit` 表示构建/源码边界；Python 的 package/module 旧字段保留以兼容 API，不要求其他语言伪造这些身份。schema 4 记录语言、源码单元、存储根及实际检查，Python 的 `original_pythonpath` 提示位于其专属 `configuration` 中。

当前布局版本为 5。Java 清单不宣称执行 Python 路由检查或 Java 编译；规划成功仍不证明编译、运行或所有目标路径都兼容，见[局限性](limitations.md)。

## 语义改写的参考边界

[OpenRewrite ChangePackage](https://docs.openrewrite.org/recipes/java/changepackage#source)、[Spoon](https://github.com/INRIA/spoon) 用于源码语义重构；[Jar Jar](https://github.com/shevek/jarjar/blob/9a7eca72f958038d46bd4f83bbe2275519f2693f/README.md) 属于字节码重打包，不是物理源码目录重排的直接替代。本实现未引入这些依赖；以后确需改 package，应评估语法/符号模型，而非使用全局字符串替换。
