# 来源与实现边界

修改本 skill、或用户询问方法来源时读。执行 review 不需要读。

## 来源

- Becker et al., *Just-in-Time Catching Test Generation at Meta*, FSE Companion '26, arXiv:2601.22832：https://arxiv.org/abs/2601.22832
- Claude Code 团队，*How the Claude Code team does code review*：https://claude.dev/blog/solving-code-review-at-anthropic/

## 对应关系

| skill 中的做法 | 来源 |
| --- | --- |
| weak catch / strong catch 定义；P 通过、H 失败才算回归捕获 | 论文第 1 节 |
| Dodgy diff：把 H 当作 P 的 mutant，生成时不推断意图 | 论文第 3.3 节、图 1 |
| Intent-aware：推断风险 → 在 P 上构造 M → M 须构建并通过已有测试 → 生成杀死 M 的 t → 在 H 上运行 | 论文第 3.1、3.2 节、图 1 |
| `intent_context=extended`：论文首版只用代码、diff 标题和摘要推断意图 | 论文第 3.2 节 |
| 定向触发项 | 论文第 1 节（类 DRS 风险模型）与表 3 真阳性模式 |
| 先描述行为变化再问是否符合预期；`dismiss_cost` | 论文第 5.1 节 |
| 规则线索与 `log_signals` | 论文第 6.2、6.3 节，表 2、表 3 |
| 语义复核的输入（测试、执行结果、diff、推断意图） | 论文第 6.1.1 节 |
| P/H 都通过的测试作为 hardening 建议 | 论文第 7 节 |
| 档位、fresh context 复核、按类报告、不重复报告、首轮后只看新 diff、3 轮上限 | 博客“AI review on every pull request”与“How we make the reviewer better” |
| `author_response` 四类、干净 PR 误报、召回优先其次轮数、事故评估集 | 博客“How we make the reviewer better” |
| REVIEW.md、`source_fix_suggestions`、证据须给文件和行号、先给数量 | 博客第 1 层“The work is verified” |

## 没有复现的部分

- Meta 内部的 prompts、风险模型、mutation-guided 测试生成器（ACH）和实验规模。测试与 mutant 由宿主模型生成，脚本只负责快照、执行和记录，不调用模型。
- 论文的 LLM-as-judge ensemble（Llama 3.3 token 概率分数、多模型类别中位数）及其阈值。宿主没有 token 概率或多个独立模型时不模拟这些分数。论文中分数是排序信号，不是校准过的 bug 概率。
- 论文没有规定规则评估器与 LLM 评估器的执行顺序或统一阈值；skill 中的顺序是建议。
- 博客中多个 verifier 投票、风险分级审批、合并队列和发布流程。本 skill 只审查并报告，不获得评论、批准或合并权限，也不自动进入修复循环。

## 本 skill 的工程扩展

- 以未提交工作区或暂存区作为 H。
- `hypothesis` 策略与仅 H 模式：论文两条路线面向无人审查的批量生成，单次 review 预算下盲目生成产出低，因此优先把审查者的具体怀疑写成测试。
- Intent-aware 中未杀死 M 也运行 H：符合论文 weak catch 定义，`mutant_killed` 另行记录。
- 时限分段、先写自检版再并入复核、复核任务书格式、`verification` 记录与终态校验、按可独立修复/驳回的论点拆分 finding、`probe` 能力探测：来自 ReviewBench 小规模试跑中的超时与证据损失，属于工程约束。
- 档位按风险而非执行能力选择、考虑过的候选都进入 findings、按影响而非代码出现版本归因、驳回的候选也送复核、拆分 finding 的判断方法：来自 ReviewBench 试跑中候选被写进 limitations、绕过记录和复核的情况，属于工程约束。
- flaky 重跑、`log_signals` 正则、依赖目录复制或链接、`{repository}` 占位符与 NODE_PATH 只读引用、review.json 状态机、预算与档位起始值。这些是运行参数和工程选择，不来自论文实验。
