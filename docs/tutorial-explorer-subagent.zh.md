# 教程：给 LangGraph agent 加一个 Explorer 子 agent

本文记录 `multi-agent` 分支上 Explorer 子 agent 的实现过程，按顺序做一遍即可复现。
每一步都能单独验证，建议每做完一步就跑一次测试。

## 0. 先想清楚：什么才算"真正的"子 agent

只把提示词换个名字不算 multi-agent。一个子 agent 至少要满足四点：

| 条件 | 本实现中的做法 |
| --- | --- |
| 独立上下文 | 独立的 `ExplorerState` 类型；输入只有 issue、question 和文件列表 |
| 独立权限 | 工具只有 `read_file`、只读 `bash`、`report`；只读由 Docker 挂载保证 |
| 结构化交接 | `ExplorerReport` schema；harness 校验每条引用的位置是否真实存在 |
| 用量可追踪 | token 和调用次数合并进主运行的总预算；trace 标注 `agent` 和 `delegation_id` |

选用的是 **agent-as-tool** 模式：子 agent 是一张独立编译的图，由主 agent 通过一个工具来调用。
另一种是 supervisor/handoff 模式，多个 agent 作为同一张图上的节点、共享同一份状态。选 agent-as-tool 的理由是：
上下文天然隔离，是否委派由模型自己决定，而且主图的结构完全不用改。

## 1. 沙箱支持只读执行（`sandbox.py`）

`exec_command` 新增 `readonly` 参数，并透传给 `_run`。`/workspace` 以只读方式挂载，`/scratch` 仍然可写。

原则：**权限由环境保证，而不是由提示词保证。**
验证方法：在 Docker 里分别以两种模式执行 `echo hi > /workspace/x`。只读模式下应该报 `Read-only file system`。

## 2. 抽出共用的模型调用防护（`model_call.py`）

主 agent 原来的 `_call` 包含了预算检查、错误处理、usage 累计和 trace 记录，把它整体移到 `invoke_model()`。
新增两个参数：`agent` 用来在 trace 里区分是哪个 agent 在调用，`max_tokens` 让子 agent 可以传入自己的子预算。
主 agent 的 `_call` 改成一个很薄的包装。这一步不改变任何行为，现有测试应该全部照常通过。

之所以放在单独的模块里，是为了避免 `graph.py` 和 `explorer.py` 互相导入。

## 3. 交接结构和证据校验（`explorer.py` 前半部分）

```python
class Finding(Contract):          # path + start_line + end_line + claim
class ExplorerReport(Contract):   # answer, findings, root_cause_hypothesis, confidence, open_questions
def verify_findings(report, state)  # 文件能否读取？行号是否在文件范围内？
def format_report(...)              # 生成主 agent 读到的纯文本，每条引用标 [verified] 或 [UNVERIFIED: 原因]
```

`verify_findings` 复用 `read_lines()`，和 `read_file` 工具走的是同一套路径与大小检查，所以路径逃逸之类的请求也会被标成 UNVERIFIED。
注意它的边界：它只能证明被引用的**位置存在**，无法证明对这段代码的**说法正确**。

## 4. Explorer 子图（`explorer.py` 中间部分）

结构和主 agent 相同：`explore`（调用模型）⇄ `tools`（ToolNode），调用 `report` 后结束。

要点：

- **没有 checkpoint**：Explorer 不能写工作区，委派中途崩溃后整次重跑没有副作用，所以不需要 checkpoint。
- **最后机会**：工具调用次数用完，或者 token 用到 75% 时，追加一条 `FINAL_NOTICE`，再给模型一次调用机会，要求它把已有的发现交上来。
  如果这次它仍然不调用 `report`，就判定为 `budget_exhausted`。token 的上限始终是硬上限。
- **递归上限**：设为 `4 * max_tool_calls + 10`，覆盖每次工具调用、中途的催促消息和最后一次通知。

## 5. 委派工具，以及接入主图

`make_delegate_tool(explorer)` 是两个 agent 之间唯一的连接点：

1. 检查委派次数是否超过 `max_delegations`，超过时抛出 `ToolDenied`，这是可恢复的错误。
2. 计算子预算：`min(explorer_max_tokens, 主运行的剩余 token)`。
3. 委派前记录工作区快照，调用 `explorer.run(issue, question, files)`。**不传 `state["messages"]`。**
4. 委派后再做一次快照对比；如果工作区有变化，抛出 `SafetyError`，最终记为 `policy_violation`。这是纵深防御。
5. 校验报告，把 usage 和 model_calls 合并进主运行，追加一条 `delegations` 记录。

接入 `graph.py` 时：

- `RunLimits` 新增 `max_delegations`、`explorer_max_tool_calls`、`explorer_max_tokens`。CLI 会自动生成对应参数。
- `AgentState` 新增 `delegations`。**LangGraph 要求更新里的每个 key 都在 state 类型中声明**，否则会报错。
- `AgentRunner(agents=...)`：只有 explorer 模式才会加入委派工具，并在提示词后面追加 `DELEGATION_PROMPT`。
  single 模式的提示词和原来逐字节相同，这样两种配置才能公平对比。
- `prepare_run` 把 `agents` 写进 `config.json`；`start()` 时检查 runner 的模式和 config 是否一致。

## 6. CLI 和 eval

`fix` 和 `eval` 新增 `--agents {single,explorer}`。`status` 和 `decide` 从 `config.json` 读取模式，
**不读当前的命令行参数**，这样一次运行前后始终使用同一种配置。

## 7. 测试（`tests/test_explorer.py`）

每个测试对应一条设计保证：上下文隔离、预算合并、子预算上限、证据校验、最后机会的两条路径、
只读参数、纵深防御、委派次数上限、single 模式没有委派工具、配置一致性、token 阈值、JSON 字符串参数。

写测试时要注意：**每一轮都要构造新的消息对象。** `[tool_turn(...)] * 3` 得到的是同一个对象。
`add_messages` 会给这个对象赋 id，之后再追加它时，会被当作同一条消息，于是替换掉旧的，而不是追加新的。

## 8. 真实模型运行中发现的两个问题（脚本化测试都没有发现）

1. **token 比工具调用次数先用完。** 每次调用模型都要重发全部历史，累计消耗大致按平方增长。
   最初"最后机会"只看工具调用次数，结果探索了 12 步，一份报告也没留下。修复方法是 token 用到 75% 时也触发。
2. **Qwen 把嵌套数组参数当成 JSON 字符串发送。** `findings` 收到的是 `'[{...}]'`，校验失败，模型重试了 5 次，直到预算耗尽。
   修复方法是用 `BeforeValidator` 先尝试解析 JSON，再做严格校验。对外公布的 schema 不变，仍然是数组。

原则：**接收时宽容，校验时严格；预算的每个维度都要有降级路径。**

## 验证命令

```powershell
python -m unittest discover -s tests
python -m repomedic fix benchmarks/cases/task_scheduler_008 --provider qwen --model qwen3.7-plus `
  --model-timeout 180 --max-tokens 80000 --agents explorer
```

## 当前的证据与限制

- 离线测试：75 个测试通过，2 个平台相关的跳过。
- 真实 Qwen `fix`（explorer 模式，task_scheduler_008）：主 agent **没有委派**，自己完成了修复，
  最终为 `awaiting_review`，消耗 38,562 token。这说明是否委派确实由模型决定。
- 单独运行真实 Qwen 的 Explorer，问一个宽泛的问题：两次修复之后状态为 `done`，10 次工具调用，34,196 token，7 条引用全部 verified。
- **还没有**做 single 和 explorer 在相同任务上的对比，所以不能声称 multi-agent 带来了提升。
  而且现有的 12 个用例对这个模型来说太简单，很可能测不出差别。
