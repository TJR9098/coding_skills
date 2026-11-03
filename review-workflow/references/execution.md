# 隔离执行

## 能力与限制

`scripts/review_run.py` 使用 Python 3.10+ 标准库和 Git，不调用模型。它固定 P/H 快照，在副本中应用 mutant、加入新测试、执行命令并保存结果。宿主 Agent 负责生成风险、patch 和测试以及判断错误类型。

这是文件目录隔离，不是操作系统沙箱。沿用宿主的沙箱/权限，先确认测试命令不会写真实服务或待审仓库。脚本不会安装依赖；项目依赖使用现成解释器/环境，必须初始化时只在隔离目录内按授权操作。

## 能力探测

```text
python <skill>/scripts/review_run.py probe --repo <repo>
```

输出当前 Python（及能否导入 pytest）、PATH 上常见运行时的位置、仓库根目录的依赖清单、已存在的 `node_modules` / `.venv` / `venv` / `vendor` 及其是否被 Git 忽略、`package.json` 的 scripts。它只检查文件，不运行项目代码，也不能说明宿主是否允许直接执行某条命令。

- 运行时存在但依赖未安装：仍可对不依赖第三方包的模块写测试，例如标准库 unittest，或用 Node 的类型剥离直接运行只含可擦除类型语法的 `.ts`（23.6 起默认开启，22.6 起需 `--experimental-strip-types`）。被测模块导入了未安装的包时记为 blocked。
- 宿主拒绝了一条直接命令（如 `node -v`）：记为该命令的权限限制，不写成“没有运行时”。宿主允许执行 helper 时，测试命令经 `compare` / `screen-mutant` 在快照副本中运行，这是该授权的用途；不要用 helper 执行被拒绝的其他操作，例如安装依赖、访问网络或修改仓库。

## 快照范围

快照不复制 Git 元数据或 ignored 文件（例如 node_modules、虚拟环境、密钥）。需要仓库内依赖目录才能运行测试时，按下文“依赖目录”显式附加，或用 NODE_PATH 只读引用。脚本拒绝子模块及 symlink；LFS 指针须由宿主识别并准备受控内容。不能运行时记为 blocked，继续静态审查。

## 固定版本

输出路径必须不存在且位于待审仓库外。省略 `--output` 时使用系统临时目录并返回绝对路径。替换下面的占位路径与 Python 可执行文件。

```text
python <skill>/scripts/review_run.py prepare --repo <repo> --scope worktree
python <skill>/scripts/review_run.py prepare --repo <repo> --scope staged
python <skill>/scripts/review_run.py prepare --repo <repo> --scope refs --base <base-commit> --head <head-commit>
```

worktree 默认以 HEAD 为 P，H 是 tracked 文件最终内容和非 ignored 的 untracked 文件。staged 默认以 HEAD 为 P，H 是 index。refs 必须指定 base/head；任务要求 merge-base 时先解析具体 commit 再传入。非 Git 项目不使用此 helper。

prepare 写 `run.json`、`snapshots/P`、`snapshots/H` 和 `change.diff`。脚本不提供原子快照：采集前后变化会中止；结束前仍核对源范围，新改动需要新 run 和相关复审。

## 输入产物

新测试放入独立 overlay 目录，内部路径相对项目根。P/M/H 使用同一份 overlay。helper 只接受新增文件，不允许覆盖源码、已有测试或配置。必须扩展已有测试文件的框架，可由宿主在隔离副本中进行受控的相同测试修改并保存完整证据；不要谎称 helper 已执行该路径。

mutant 为标准 Git unified diff patch，目标为 P 的生产代码；不修改 S、测试配置或依赖来让 M 存活。Agent 审核 patch 后，在复制出的 P 上应用。

命令是 JSON 参数数组，使用 `shell=False`。不要将整条 shell 命令写成一个字符串。构建/测试组合优先使用项目既有包装命令；确需 shell 时显式调用并正确引用。

脚本清除继承的 Git 定位变量、PYTHONPATH 和 NODE_PATH，避免命令指回原仓库。src-layout 项目可在筛选和比较配置中增加 `"environment": {"PYTHONPATH": "{workspace}/src"}`；`{workspace}` 按每个 P/M/H 工作目录展开；`{repository}` 展开为待审仓库根目录，只用于读取已安装的依赖（见下文）。其他变量也可按需设置。覆盖值会保存，不在配置里写密钥。

动态验证前检查实际导入的源码/构建目标来自对应隔离目录（Python 可查看模块 `__file__`），特别是使用 editable install、链接依赖、全局 CLI 或绝对路径命令的项目。脚本无法自动识别所有语言的依赖解析；若仍指向原仓库，先修正隔离环境，未解决则标记验证受阻。

## 依赖目录

依赖装在仓库内被忽略目录的项目（Node 的 `node_modules`、PHP/Ruby 的 `vendor`、仓库内 `.venv`），在 `screen-mutant` 或 `compare` 配置中声明：

```json
"dependencies": {"mode": "copy", "paths": ["node_modules"]}
```

- 路径相对仓库根，必须是已存在且被 Git 忽略的目录；tracked 内容只能来自快照。目录从当前待审仓库取，在新增测试放入之后再附加到每个 P/M/H 副本。
- `copy`（默认）：每个副本一份完整拷贝，保留目录内的相对符号链接。隔离可靠，但大目录会占用较多空间和时间。
- `link`：在副本中创建指向原目录的链接（Windows 用 junction，无需管理员权限）。测试若写入依赖目录会改到用户仓库，只在确认测试不写依赖时使用。删除 run 时用不跟随链接的方式删除（Python 3.8+ 的 `shutil.rmtree` 可以）。
- 所有副本共用当前安装的依赖。结果中的 `dependencies.manifest_changed` 列出 diff 改动的依赖清单（`package.json`、锁文件、`pyproject.toml` 等）；非空时 P 实际运行在 H 的依赖上，P/H 差异可能来自依赖而非代码，需分诊或标为受阻。
- 依赖在全局缓存的生态（Maven/Gradle 的 `~/.m2`、Go module cache、Cargo registry）通常不需要此项；`target/`、`build/` 等构建产物由测试命令重新生成。

### 只读引用：NODE_PATH

`node_modules` 很大、又不想用 `link` 时，可以不附加目录，而让 Node 从待审仓库读取已安装的依赖。命令参数和 `environment` 中的 `{repository}` 展开为待审仓库根目录（run.json 的 `repository`）：

```json
{
  "strategy": "dodgy-diff",
  "test_overlay": "/absolute/generated-tests",
  "test_command": ["node", "{repository}/node_modules/jest/bin/jest.js", "--runTestsByPath", "generated.test.js"],
  "environment": {"NODE_PATH": "{repository}/node_modules"},
  "timeout_seconds": 120
}
```

- 副本里没有 `node_modules`，Node 在副本中向上找不到依赖时才查 NODE_PATH，因此被测代码始终来自副本，依赖来自原仓库。工具在 `./node_modules/.cache` 写缓存时写入的是副本。
- 这是读取引用：不复制、不在副本中建链接。依赖文件本身仍是原仓库的文件，测试不应通过绝对路径写它们。
- 适用于 CommonJS `require` 和读取 NODE_PATH 的运行器（Node、Jest、Mocha）。原生 ESM `import`、Vitest/Vite、TypeScript 路径解析和打包器不读 NODE_PATH，这些项目改用 `copy` 或 `link`。
- 运行器和 CLI 用 `{repository}/node_modules/<包>/bin/...` 调用。Windows 上 `.bin` 里是 `.cmd` 包装，`shell=False` 不能直接启动，应调用包内的 JS 入口。
- 先确认真的跑到了生成测试：没设对 NODE_PATH 时常见结果是 P 上 `Cannot find module`，即 `invalid_parent_test`，`log_signals` 标出 `module_missing`。
- 与 `dependencies` 一样，所有副本共用当前安装的依赖。这种方式不生成 `manifest_changed`；先查看 `change.diff` 是否改了 `package.json` 或锁文件，有改动时按上一节分诊。
- 仓库内 `.venv` 的 Python 项目同理：用 `{repository}/.venv/Scripts/python.exe`（Windows）或 `{repository}/.venv/bin/python` 作为命令，同时检查 editable install 是否把导入指回原仓库。

## Dodgy diff

宿主先生成 t。示例配置（Python unittest；hypothesis 测试同样使用此配置，见下节）：

```json
{
  "strategy": "dodgy-diff",
  "test_overlay": "/absolute/generated-tests",
  "test_command": ["python", "-m", "unittest", "test_generated"],
  "timeout_seconds": 120
}
```

```text
python <skill>/scripts/review_run.py compare --run <run> --case <case.json> --id case-001
```

test_command 需要构建并执行新增测试，而不是仅编译或选择零个测试。脚本记录退出码，Agent 必须检查目标测试确实执行。

H 失败时脚本默认把 P 和 H 各重跑 1 次（`"flaky_reruns": 0-10`），任一次状态变化则结果为 `flaky`。依赖时间、随机数或外部状态的测试可调高次数。

## Hypothesis 测试

配置与 Dodgy diff 相同，`"strategy": "hypothesis"`。断言来自需求而非 P 的旧行为。P 不存在对应行为（新功能、新接口）时加 `"parent_applicable": false`，脚本只在 H 上运行，结果为 `head_only_failure` / `head_only_pass`。该选项只允许用于 hypothesis，避免把单版本失败冒充回归捕获。

## Intent-aware：先筛 M，再生成 t

先生成风险与基于 P 的 mutant patch，创建筛选配置：

```json
{
  "mutant_patch": "/absolute/mutant.patch",
  "baseline_command": ["python", "-m", "unittest", "test_existing"],
  "timeout_seconds": 120
}
```

baseline_command 应包含构建和已有相关测试 S。Python 项目导入/执行测试可承担构建有效性检查；编译语言选择真实的编译和测试入口。

```text
python <skill>/scripts/review_run.py screen-mutant --run <run> --case <screen.json> --id mutant-001
```

仅当结果为 `survived_existing_tests` 且日志确认 S 实际运行时，宿主才生成针对 M 的 t。比较配置引用筛选结果：

```json
{
  "strategy": "intent-aware",
  "screen_id": "mutant-001",
  "test_overlay": "/absolute/generated-tests",
  "test_command": ["python", "-m", "unittest", "test_generated"],
  "timeout_seconds": 120
}
```

```text
python <skill>/scripts/review_run.py compare --run <run> --case <compare.json> --id case-002
```

compare 使用筛选时保存的 patch，从干净的 P 重建 M；先执行 t(P)，通过后执行 t(M) 并记录 `mutant_killed`（true / false / 超时等为 null），然后总是执行 t(H)。S 和 t 是不同的测试。每个 case 保存配置、patch/overlay 和日志，输入后续变化不影响已记录证据。

## 结果解释

单次命令状态：`passed` / `nonzero_exit` / `timeout` / `launch_error`；超时和启动错误不当作测试失败参与捕获。

| outcome | 含义 |
| --- | --- |
| survived_existing_tests | P/M 的已有测试命令退出成功；仍需确认测试数量和构建日志 |
| invalid_baseline / mutant_rejected | P 基线无效，或 M 未通过筛选 |
| invalid_parent_test | t 在 P 未通过，不构成回归捕获 |
| differential_failure | P 退出成功、H 返回非零且重跑一致；分诊后才能称 weak catch |
| no_differential_failure | H 通过，未捕获当前提交 |
| flaky | 重跑时 P 或 H 状态变化，不构成捕获 |
| head_only_failure / head_only_pass | 仅 H 的 hypothesis 测试失败 / 通过 |
| inconclusive | 超时或执行能力不足 |

Intent-aware 的 `mutant_killed` 与 outcome 分开记录：未杀死 M 但 `differential_failure` 仍是 weak catch，只是与推断风险无关。

`log_signals` 按命令标签列出日志命中的规则线索（见策略文档），只作分诊提示。

退出码不能代替语义评估。将 raw outcome、有效 weak catch、确认的 bug 分别记录。

## 复审时的增量范围

修复后用新的 prepare 生成新 run。两个 run 的 H 之间的差异就是复审范围，也用来判断已关闭的 finding 是否需要重开：

```text
git diff --no-index --stat <old-run>/snapshots/H <new-run>/snapshots/H
```

## 状态与清理

```text
python <skill>/scripts/validate_review.py <run>/review.json
```

实验 ID 不可覆写；修改测试后使用新 ID，重新跑 P/M/H。默认保留 run 并报告路径，用户按需要保存或删除。
