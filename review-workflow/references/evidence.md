# 证据与状态

## 文件归属

运行目录与 skill 源码、用户待审仓库分开。`run.json` 由快照脚本生成，标识 P/H 的来源；`review.json` 由协调者维护；每个实验写独立目录，保留命令与日志。多个 reviewer 只返回候选，由协调者串行合并记录。

## review.json（schema_version=3）

```json
{
  "schema_version": 3,
  "level": "deep",
  "review_id": "review-001",
  "round": 1,
  "max_rounds": 3,
  "current_snapshot": "run-001",
  "review_mode": "independent_context",
  "status": "reported",
  "verification": {"status": "applied", "artifacts": ["verifier-F001.md"], "verdicts": {"F001": "confirmed"}},
  "scope": "HEAD 到最终工作区，含相关新增文件",
  "intent": {"confirmed": ["保持租户隔离"], "inferred": ["简化表达式"], "unknown": []},
  "budget": {"generation_attempts": 4, "command_timeout_seconds": 120},
  "strategies": [
    {"name": "static", "status": "completed", "reason": "追踪权限路径"},
    {"name": "hypothesis", "status": "skipped", "reason": "Dodgy diff 已覆盖同一怀疑"},
    {"name": "dodgy-diff", "status": "completed", "reason": "可运行版本对照"},
    {"name": "intent-aware", "status": "skipped", "reason": "已取得直接复现证据"}
  ],
  "findings": [
    {
      "id": "F001",
      "strategy": "dodgy-diff",
      "title": "管理员分支绕过租户限制",
      "root_cause": "admin 分支不再受租户条件限制",
      "trigger": "跨租户且 admin=true",
      "impact": "允许跨租户删除",
      "expected_basis": "用户确认保持原权限规则",
      "location": "src/access.py:12",
      "origin": "introduced",
      "severity": "P1",
      "evidence_level": "reproduced",
      "assessment": "bug",
      "counterevidence": "调用方无额外过滤；未发现允许该变化的需求",
      "evidence": ["experiments/case-001/result.json", "experiments/case-001/logs/H-test.log"],
      "verified_snapshot": "run-001",
      "status": "open",
      "author_response": null,
      "dismiss_cost": "low",
      "related": [],
      "history": [{"round": 1, "snapshot": "run-001", "action": "opened", "reason": "复现且违反确认需求"}]
    }
  ],
  "limitations": [],
  "source_fix_suggestions": []
}
```

预算数字是示例，依据实际命令耗时与任务规模选择。不要因为示例含 finding 就凑数。

取值：
- `level`：`quick` / `standard` / `deep`。
- `review_mode`：`independent_context` / `self_check`。只有 `verification.status=applied` 时才是 `independent_context`。
- `verification.status`：`not_requested` / `pending`（已发出、结论未并入）/ `unavailable`（宿主不支持或不允许）/ `applied`（结论已并入本文件）。`applied` 时 `artifacts` 列出保存的复核原文（路径相对 review.json），`verdicts` 按 finding ID 记 `confirmed` / `refuted` / `uncertain`；其他状态不写 verdicts。复核没回来、超时等情况只用这个字段表达，不在 limitations 里另写。
- 顶层 `status`：`in_progress` / `reported` / `needs_human`。
- 策略名：`static` / `hypothesis` / `dodgy-diff` / `intent-aware`；策略状态：`completed` / `partial` / `skipped` / `blocked`。
- `origin`：`introduced` / `pre_existing` / `unknown`；`severity`：`P0` 到 `P3` 或未知时 `null`。旧缺口在 H 中产生了 P 没有的影响时记 `introduced`，见策略文档“静态候选”。
- `limitations`：未执行的检查、环境限制等覆盖缺口。考虑过的候选不写在这里，作为 finding 记录（不成立的 dismissed，不确定的 candidate）。
- `evidence_level`：`hypothesis` / `static_confirmed` / `reproduced`。`reproduced` 指复现观察，不自动等于 bug。
- `assessment`：`pending` / `bug` / `expected_change` / `test_or_environment_issue` / `not_a_bug` / `uncertain`。
- `author_response`（可选）：`null` / `fixed` / `wrong` / `intended` / `not_worth_it`，记录作者对已上报 finding 的实际反应，用于度量噪声。
- `dismiss_cost`（可选）：`null` / `low` / `medium` / `high`，作者排除误报的估计成本。
- `source_fix_suggestions`（可选）：建议沉淀到 REVIEW.md、项目约定或 lint 的规则。
- `related`（可选）：与本条共享代码位置或根因、但需单独判断的其他 finding ID。
- finding `status`：`candidate` / `open` / `resolved` / `dismissed` / `blocked` / `stale`。
- 待确认项：`status=candidate`、`assessment=uncertain`、`dismiss_cost` 为 `low` 或 `medium` 的 finding，在报告中列为需要作者回答的问题。`dismiss_cost=high` 的 candidate 留在记录中，不在报告里列出。

`current_snapshot`、`verified_snapshot` 和 history 中的 snapshot 都使用 run.json 的 `run_id`（示例简写为 run-001），校验器据此核对实验。未验证候选可以为 null。open 必须在当前快照验证，否则标为 stale。resolved / dismissed 是已关闭结论，保留得出结论时的快照（须出现在 history 中），新快照下不需复验；新 diff 触及其位置或根因时重新打开。

## 转移与收敛

- 候选进入 open 前必须判为 bug，且有 static_confirmed 或 reproduced 证据；附预期依据、影响和反证检查。
- expected_change / test_or_environment_issue / not_a_bug 可以 dismissed，写明理由；作者回复 `not_worth_it` 时确认的 bug 也可以 dismissed（作者接受风险）。uncertain 保持 candidate 或 blocked。
- 作者回复 `fixed` 不等于 resolved：在新快照复测通过前保持 open 或 stale。
- 修复后重放触发条件，在新快照记录证据后才 resolved。新提交不会自动解决全部问题。
- 同一根因、触发场景和影响复用 ID；位置变化不创建重复问题。独立的新根因可以创建新 ID。
- 达到 max_rounds 且仍有 open/candidate/blocked/stale 时设 needs_human；不得自动通过。继续需明确记录用户调整的上限或新的审查序列。
- 被复核判为 `refuted` 的 finding 不能 open；协调者不同意时改为 uncertain 并写明分歧。复核对一条候选中的不同论点给出不同结论时，拆成多条 finding 再分别记 verdict。

## 终态与中断

顶层 `in_progress` 是允许不完整的草稿；`reported` / `needs_human` 是交付物，校验器要求：每个 finding 都有结论（不留 `pending`）；判为 `bug` 的不能停在 candidate，要么在当前快照 open，要么改判 uncertain；review_mode 与 verification 一致。

第一轮判断完成后就写出一份 `reported` 的自检版本，之后每次变化都整份重写并重新校验。这样运行被中断时，文件仍是最后一次完整写入的状态。不要通过逐字段编辑并入复核结论：中途停止会留下 review_mode 已改、status 未改这类自相矛盾的记录。也不要为了让记录可交付而把未确认的候选提升为 bug。

schema 2 记录没有 `verification`。继续使用旧记录时，按实际情况补上 `verification` 并改为 schema 3；不要改写已归档的历史记录。

## 证据引用与交叉校验

`evidence` 中以 `experiments/`、`snapshots/`、`./`、`../` 开头的条目或绝对路径视为运行产物，路径相对 review.json 所在目录；其他条目是文字说明（如源码位置、调用链）。review.json 默认放在当前 run 目录，引用旧 run 的证据用 `../<old-run>/experiments/...`。

从文件运行 `validate_review.py` 时额外检查：
- 引用的运行产物都存在。
- review.json 同目录有 run.json 时，`current_snapshot` 等于其 run_id。
- `evidence_level=reproduced` 的 finding 至少引用一个实验，其 `kind` 与 finding 的 `strategy` 相同、outcome 为 `differential_failure` 或 `head_only_failure`、run_id 等于 `verified_snapshot`。静态审查后用测试复现的问题记为 `hypothesis` 策略。

校验器不能证明需求真实、结论正确或根因确实重复。协调者仍须读证据判断。
