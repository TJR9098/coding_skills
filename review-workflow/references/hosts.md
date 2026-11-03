# 宿主适配

核心 SKILL.md 使用通用 Agent Skills 字段。工具名和多 Agent API 不写死在核心流程中。支持情况取决于当前宿主、会话权限与可用工具；不要靠产品名猜测能力。

## Codex

- 原生调用：`$review-workflow`，或明确要求使用该技能。
- 仓库级安装：将整个 skill 目录放入该仓库的 `.agents/skills/review-workflow/`。用户级目录以当前 Codex 官方文档和本机配置为准。安装属于单独动作，不因为阅读本文件自动安装。
- 可用时用宿主子 Agent 工具创建独立 reviewer；向它提供本轮需求、版本、候选和原始证据，要求它独立反驳/核实。工具或更高优先级指令不允许时顺序自检并如实记录。
- 将 Python 脚本交给宿主命令执行工具；命令参数按当前 shell 正确引用。Windows 不要求 Bash。
- `agents/openai.yaml` 仅用于展示与宿主元数据，不定义通用工作流。

## Claude Code

- 原生调用：`/review-workflow`，或明确要求使用该技能。
- 仓库级安装：将整个 skill 目录放入 `.claude/skills/review-workflow/`；用户级可放入 `~/.claude/skills/review-workflow/`，以本机配置为准。不要覆盖同名已有安装。
- 使用当前宿主支持的 subagent/Agent 工具完成独立复核；未提供时顺序自检。不要在通用 SKILL.md 中设置 `context: fork` 来假定整个工作流都要放在子 Agent，也不要复制某个版本的工具参数作为跨平台接口。
- 子 Agent 使用同一证据格式；协调者汇总并写 review.json，避免并行覆写。
- 子 Agent 在后台运行时，协调者给出最终回复后，进程可能仍在等待子 Agent，并在其返回时再次唤醒协调者。有时限的任务里，最终回复前就确定复核状态：已返回的整份并入并标 `applied`，未返回的保留自检版本并标 `pending`。不要先给最终回复，再利用剩余时间逐字段补改。

## 不同架构下的能力降级

| 可用能力 | 可执行范围 | 报告要求 |
| --- | --- | --- |
| 文件读取 | 静态审查与需求核验 | 无运行证据，说明无法动态验证 |
| 文件读取、命令和隔离目录 | 加入 P/H、M 执行 | 保存命令、结果和日志 |
| 独立上下文 | 候选发现与复核分离 | 复核结论并入后记录 independent_context 与 `verification.status=applied` |
| 仅当前会话 | 顺序发现与自检 | 记录 self_check 与 `verification.status=unavailable`，不能冒充独立验证 |

逻辑角色不要求特定 Agent 数量或模型。并行发现只是优化，有限范围的串行审查也有效。没有文件持久化能力时，输出足够恢复审查的结构化交接材料。

## 安装与验收

核心包没有硬编码用户目录、绝对解释器路径、模型 ID 或 API key。使用宿主可用的 Python 3.10+ 和 Git。引用命令中的 `<skill>`、`<run>` 等为占位符，调用时替换成实际路径。

安装后需分别在目标宿主的新会话里验证技能发现、读取 references、运行脚本和报告输出。用另一种宿主做独立审查不等于已验证该宿主的技能发现和加载。

参考：
- Agent Skills 标准：https://agentskills.io/specification
- Codex：https://developers.openai.com/codex/skills
- Claude Code：https://code.claude.com/docs/en/skills
