# 变更与验收记录

维护记录，执行 review 时不需要读。评估方法见 [references/evaluation.md](references/evaluation.md)。

## v4 复测后的待确认项修订（2026-11-02）

- 防御性缺口作为待确认项上报：路径可达但证明不了危害的缺口，例如未校验的外部输入、绕过类型约束的强转、与同类代码校验约定不一致。
  - 记为 candidate / uncertain / P3 / dismiss_cost=low，不判 bug，也不以“没有危害”判 not_a_bug。
  - 复核只问路径是否可达、约定是否确实不一致。
- 复核发出后才找到的候选：时间够就发第二次复核；时间不够时，不能只凭自检以“原有问题”“影响有限”驳回，改为待确认项。
- 复核任务书对多论点候选逐个论点给出结论；结论不同的论点在并入时拆成多条 finding。
- 明确待确认项的记录形式：candidate + uncertain + dismiss_cost 为 low/medium。high 成本的保留在 findings 中但不在报告里列出，不再写进 limitations。
- 评测 runner（不属于 skill）：
  - 另外导出 `findings-with-questions`，包含 open bug 和待确认项，单独评分；
  - `findings` 仍只含 open bug，与之前各条件可比。
- 脚本未改，33 项确定性测试通过。

## v5 留出验证（2026-11-02）

按固定规则从 ReviewBench 另选 6 个未用过的 PR，v5 与 baseline 各跑一次，报告见 `reviewbench-eval/v5留出验证.md`。

- v5 没有超过 baseline：
  - 只计 open bug，覆盖 5/44 对 9/44，precision 83.3% 对 90.0%；
  - 计入待确认项，覆盖 7/44，precision 87.5%。
- 差距主要来自三处：
  - 有明确预期依据的功能遗漏被记为待确认项（两例，计入待确认项后都匹配到 TP）；
  - 同根因多处实例合并成一条；
  - 发现数较少。
- 开发集上 v3 → v5 的改进没有推广到新 PR。按这次结果修改后，需要另选新的留出 PR 验证。

## v5 ReviewBench 复测（2026-11-02）

冻结为 `reviewbench-eval/skill-snapshots/review-workflow-v5-001` 后，在同样 3 个 PR 上运行，报告见 `reviewbench-eval/v5Skill复测.md`。

- 三例都按时交付，都发出并并入了复核。总耗时 16 分 33 秒，审查 token 5.67M。
- 只计 open bug：召回 6/21，precision 80%。计入待确认项：召回 9/21，precision 83.3%，与 baseline 持平。
  - gimmes 的两个论点首次拆开，已复现的那条匹配 TP，推断的那条匹配 FP；
  - statswales 的输入校验缺口作为待确认项上报，复核确认路径可达，覆盖 2 条参考 TP。
- 复核发出后处理新候选的规则本轮没有被触发；测试质量类的参考 TP 仍未覆盖。每个条件只跑一次，裁判判定也有 ±1 的波动。

## v4 ReviewBench 复测（2026-11-02）

冻结为 `reviewbench-eval/skill-snapshots/review-workflow-v4-001` 后，在同样 3 个 PR 上运行，报告见 `reviewbench-eval/v4Skill复测.md`。

- 三例都选 standard、按时交付、发出并并入复核。总耗时 14 分 12 秒，审查 token 5.29M。
- 召回 4/21 → 5/21，precision 仍为 75%。
  - statswales 的候选进入 findings 和复核，但被复核驳回；
  - gimmes 仍以阈值论点为主，被匹配为 FP；复核发出后找到的候选被自行驳回，没有送复核；
  - aafactory 的覆盖增加主要来自报告细节。
- 待验证：复核发出后的新候选如何处理；拆分规则需要校验或复核端支持；无可证明危害的校验缺口是否作为低严重度待确认项上报。

## v3 复测后的候选记录修订（2026-11-02）

- SKILL.md：
  - 档位按风险选，不按执行能力选；`quick` 去掉“或无法执行命令”，行为改动不能执行时动态策略记 blocked。
  - 考虑过的候选都作为 finding 记录（不成立的 dismissed，不确定的 candidate），limitations 只写覆盖缺口。
  - 以“原有问题”“超出意图”驳回前，检查改动是否让旧缺口到达新的位置。
  - 只要有候选就发复核，被驳回的候选连同驳回理由一起送复核。
  - 拆分 finding 的判断方法：作者只同意或只修其中一个论点时，另一个是否仍需单独讨论；标题与影响只写本条证据支撑的论点。
- strategies.md：归因看影响，P 中旧缺口在 H 中接到新位置时记 introduced；“超出意图”不是驳回理由。
- evidence.md：`origin` 与 `limitations` 的取值说明。
- 脚本未改，33 项确定性测试通过。

## v3 ReviewBench 复测（2026-11-02）

冻结为 `reviewbench-eval/skill-snapshots/review-workflow-v3-001` 后，在同样 3 个 PR 上重跑，报告见 `reviewbench-eval/v3Skill复测.md`。

- 三例都按时交付了自洽记录，总耗时 12 分 14 秒，审查 token 4.82M。statswales 的首次尝试因评测脚手架问题失败（`cd repo` 后写权限失效），修复 runner 后用同一冻结版本重跑。
- 召回 4/21，precision 75%（v2 为 7/21、100%）：
  - gimmes 的 finding 以有争议的阈值论点为主，被匹配为 FP；
  - statswales 把 download_language 候选写进 limitations，没有记录为 finding，也没有复核，最终交付 0 条。
- 待验证的修改方向：
  - 考虑过的候选都要进入 findings；
  - 以“原有问题”驳回前，先检查本次改动是否带来了新的影响；
  - 不能执行命令的行为改动不应选 quick。

## ReviewBench 试跑后的交付修订（2026-11-02）

依据 `cc-skill-v2-001` 三例的执行轨迹（详见工作区 `context/` 与 `reviewbench-eval/新版Skill复测.md`）。statswales #626 的时间线：
- 239 秒时才发出复核，此前做了大量静态阅读，其中包括仓库外后端的语义；
- 复核任务含一个开放式问题“还有没有其他缺陷”，耗时约 343 秒（另两例分别为 53 秒和 125 秒）；
- 协调者在复核运行期间重新检查了同一候选，416 秒时以 uncertain/candidate 给出最终回复，并写下“复核在截止前未返回”；
- 583 秒复核返回后，协调者开始逐字段修改，600 秒被终止，留下 review_mode 与 status 自相矛盾的记录。

gimmes #468 把已复现的累计量回退和未复现的默认阈值推断合并在一条 finding 里。

- SKILL.md：
  - 时限分段：最后至少留 15% 收尾；仓库外问题查一次无果就记为 unknown。
  - 先写出完整的自检版 review.json，复核只用来推翻或加强。
  - 复核任务书固定为可证伪问题、读取预算和 verdict 格式，不含找新缺陷。
  - 复核期间不重复检查同一批候选；并入时整份重写，时间不够就保留自检版本。
  - finding 按能单独修复或单独驳回的论点拆分，用 `related` 关联。
  - 区分运行时存在、依赖已安装和直接命令获准这三件事。
- review.json 升到 schema 3：
  - 新增 `verification` 记录（not_requested / pending / unavailable / applied、artifacts、verdicts）和 `related`。
  - 只有 `verification.status=applied` 才允许 independent_context。
  - `reported` / `needs_human` 时不允许 pending assessment，也不允许 bug+candidate。
  - `refuted` 的 finding 不能 open；复核原文路径必须存在。
- `review_run.py probe`：报告运行时、依赖清单和已安装的依赖目录，不运行项目代码。
- hosts.md：后台子 Agent 时，在最终回复前确定复核状态。
- 33 项确定性测试通过。用新校验器检查超时草稿的副本（仅把 schema_version 改为 3、补上 `verification.status=pending`），两处不一致均被报出；原始 run 未修改。尚未用新版重跑 ReviewBench，上述改动对按时交付和召回的效果未经评测。

## NODE_PATH 只读引用（2026-11-02）

- 命令参数和 `environment` 支持 `{repository}` 占位符，可用 `NODE_PATH={repository}/node_modules` 读取待审仓库已安装的依赖，不复制也不建链接；execution.md 写明适用范围（CommonJS、Jest、Mocha）和不适用的 ESM/Vitest/打包器。
- Windows 下 29 项确定性测试通过，其中 Node 用例用 Node v24 实际运行；未在真实 Jest 项目上验证。

## P2 修订（2026-11-02）

- 来源说明与论文对应关系移到 `references/provenance.md`，策略文档只保留操作规则；验收记录从 evaluation.md 移到本文件。
- `validate_review.py` 从文件校验时检查证据路径存在，并核对 `reproduced` 结论引用了同一快照、同一策略、结果为差异失败的实验。
- `screen-mutant` / `compare` 支持 `dependencies`，把待审仓库中被忽略的依赖目录（如 `node_modules`）复制或链接进隔离副本；diff 改动依赖清单时在结果中提示。
- Windows 下 27 项确定性测试通过；尚未在 Linux/macOS 或真实 Node 项目上运行 link 模式。

## P0/P1 修订（2026-11-02）

- 新增 `hypothesis` 策略与仅 H 模式、quick/standard/deep 档位、Intent-aware 未杀死 M 时仍跑 H、flaky 重跑、`log_signals`、review.json schema 2（`level`、`author_response`、`dismiss_cost`、`source_fix_suggestions`、`not_a_bug`），已关闭结论不再随新快照失效。
- Windows 下 24 项确定性测试通过。下面两节的验收均基于修订前版本，修订后尚未重跑宿主端到端验收或消融评估。

## Claude Code + DeepSeek 原生追加验收（2026-11-02）

- 使用 DeepSeek Anthropic 兼容端点与 deepseek-flash，原生 Skill 工具通过会话局部插件加载未经修改的 skill 副本。Claude Code 主审及独立 review-verifier 均实测运行；没有修改全局登录、安装或配置。
- 固定三个 ReviewBench PR 的首次完整尝试均完成，输出 3、1、0 条 finding；三例独立复核均完成，结构、导出位置和源码保持检查通过。结果在工作区 `reviewbench-eval/runs/cc-pilot-001`，接入说明在 `reviewbench-eval/ClaudeCode接入.md`，这些是该次评测产物而非 skill 运行依赖。该批 review.json 为 schema 1，不能用当前校验器直接校验。
- gimmes #468 执行了 Dodgy diff：两次生成尝试均 P 通过、H 目标断言失败；第二次 fixture 满足上游默认筛选约束，语义复核后模型保留一条 finding。完整 CLI 路径仍只有静态证据。
- 另外两例动态阶段受测试环境限制；本轮没有构造 M，未覆盖真实 PR 上完整 Intent-aware。前述合成示例的流程验收不能代替这一覆盖项。
- 评分裁判未运行，输出数量不是 TP 数，没有 precision/recall。成本按 CLI modelUsage 汇总，包含子 Agent 和压缩前用量；不能直接使用顶层 usage 或未知模型的 CLI 美元估价。

## 首次实现验收（2026-11-02）

- Windows 环境下 19 项确定性测试全部通过；skill-creator 的格式校验通过，Markdown 资源引用全部存在。
- 独立 Codex 使用者在定价示例仓库完成静态、Dodgy diff 和 Intent-aware 三条流程，每条动态路线一个候选。执行记录标为 self_check，因为该次受控评估禁止再调用子 Agent。
- Intent-aware：P 上构造边界风险 M；P/M 各通过已有两项测试后才生成新测试；新测试 P 通过、M/H 失败，确认等于折扣门槛时漏折扣的缺陷。生成前读了需求文件，明确记录 intent_context=extended。
- Dodgy diff：测试观察到超时由 30 改为 60，形成有效 weak catch；结合需求判定 expected_change 并驳回，没有要求撤回正确改动。
- review.json 结构检查通过；示例仓库源码、HEAD、index、staged/unstaged 状态保持一致。
- 独立代码审查发现的 Git 环境重定向、src-layout 导入归因及 mutant symlink 隔离问题已修复；相应用例通过，审查者已复核修复。
- 首次 Claude Code 2.1.310 验收在模型执行前返回 `Not logged in · Please run /login`，调用 token 为 0；该次未通过原生加载与执行验证。随后通过上方 DeepSeek 接入试跑验证了该链路。
- 未在 Linux/macOS 运行本次测试；跨平台标准库实现及链接拒绝规则不等于所有平台均已实测。
