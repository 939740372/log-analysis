# ReAct Agent 设计草案

## 目标

将当前“两阶段日志分析工具”升级为一个以 LLM 为主导的 ReAct Agent：

- LLM 负责决定下一步查什么
- 工具层负责执行日志检索、聚合、源码定位、上下文截取
- 系统层负责约束步数、上下文大小、重试、超时与并发

该版本仍聚焦于常见 Java 后端日志，不扩展为“所有日志类型通用平台”。

## 总体思路

ReAct 执行循环分为四类消息：

1. `Thought`
   LLM 根据当前证据判断问题、缺口与下一步动作。
2. `Action`
   LLM 选择一个工具并给出参数。
3. `Observation`
   工具返回结构化结果，回喂给 LLM。
4. `Answer`
   当证据充分时，LLM 输出最终分析结论或修复建议。

与当前版本相比，最大的变化是：

- 当前版本：先规则聚合，再让 LLM 总结
- ReAct 版本：LLM 可以自主控制调查路径，但不能直接自由读全量日志

## 角色分工

### 1. Agent Controller

负责：

- 管理会话状态
- 控制单轮最大步数
- 控制单次工具返回大小
- 记录中间轨迹
- 判断是否继续循环

### 2. Planner / Reasoner LLM

负责：

- 识别当前问题假设
- 选择下一步工具
- 判断是否要继续查日志或查代码
- 汇总最终结果

### 3. Tool Layer

仅提供受控工具，不允许 LLM 任意读文件。

建议工具集合：

- `list_log_files`
- `sample_log_head_tail`
- `search_logs`
- `get_trace_context`
- `get_endpoint_profile`
- `find_exception_clusters`
- `get_issue_candidates`
- `search_code_symbols`
- `open_code_context`
- `get_config_matches`
- `finalize_report`

## 推荐状态机

### A. Stage 1: 纯日志调查

1. 初始化：
   加载日志文件元信息、大小、时间范围、可用工具说明
2. 问题发现：
   使用 `get_issue_candidates`、`search_logs`、`get_endpoint_profile`
3. 证据钻取：
   使用 `get_trace_context`、`find_exception_clusters`
4. 结论输出：
   输出 `Observed` 级别总结

### B. Stage 2: 源码联动调查

1. 继承第一阶段高优先级问题
2. 对每个问题独立启动一个 ReAct 子流程
3. 使用：
   - `search_code_symbols`
   - `open_code_context`
   - `get_config_matches`
4. 输出：
   - `Observed`
   - `Linked`
   - `Inferred`

## 数据模型建议

### AgentState

建议包含：

- `run_id`
- `stage`
- `goal`
- `current_issue`
- `steps`
- `observations`
- `tool_budget`
- `token_budget`
- `source_root`
- `log_files`

### ToolCall

- `tool_name`
- `arguments`
- `issued_at`
- `status`
- `result_excerpt`

### AgentIssue

- `issue_id`
- `title`
- `priority`
- `observed_facts`
- `linked_code`
- `open_questions`
- `final_recommendation`

## Prompt 结构建议

### System Prompt

明确要求：

- 只能调用给定工具
- 先证据，后结论
- 不能编造缺失代码或配置
- 如果证据不足必须声明
- 输出必须区分 `Observed`、`Linked`、`Inferred`

### Planner Prompt

输入：

- 当前阶段
- 当前问题
- 已知证据
- 最近 3 次 observation
- 可用工具清单

输出 JSON：

```json
{
  "thought": "一句话说明下一步为什么这样查",
  "action": {
    "tool": "search_logs",
    "arguments": {
      "query": "Read timed out"
    }
  }
}
```

### Final Answer Prompt

输出结构建议：

```json
{
  "title": "...",
  "summary": "...",
  "observed_facts": [],
  "linked_code": [],
  "fix_direction": "...",
  "llm_suggestion": "...",
  "risks": []
}
```

## 工具接口建议

### 日志工具

- `search_logs(query, files, limit, context_lines)`
- `get_trace_context(trace_id, files, limit)`
- `get_endpoint_profile(endpoint, files)`
- `find_exception_clusters(keyword, files)`

### 源码工具

- `search_code_symbols(symbol, source_root, limit)`
- `open_code_context(file_path, line_number, before, after)`
- `get_config_matches(keyword, source_root, limit)`

### 聚合工具

- `get_issue_candidates(files)`
- `get_log_overview(files)`

## 关键约束

### 为什么不能让 LLM 直接读全量日志

- 日志体量太大，token 成本不可控
- 容易在噪声里迷路
- 可重复性差
- 同一问题每次路径可能不一致

### 因此推荐策略

- LLM 决策
- 工具执行
- Observation 保持结构化

## 当前版本到 ReAct 版本的迁移顺序

### 第一步

保留现有第一阶段聚合器，先把它包装成工具：

- `get_log_overview`
- `get_issue_candidates`
- `get_endpoint_profile`

### 第二步

把现有第二阶段源码关联器包装成工具：

- `search_code_symbols`
- `open_code_context`

### 第三步

新增 Agent Controller：

- 单问题循环
- 最大步数限制
- 工具白名单

### 第四步

把“按 issue 并发调用 LLM”升级成：

- 每个 issue 一个 ReAct 子 Agent
- 并发执行多个子 Agent
- 汇总最终报告

## 成功标准

ReAct 版本达到可用，至少应满足：

- 能对单个 issue 连续做 3 到 8 步调查
- 能根据 observation 改变下一步工具选择
- 能输出清晰的 `Observed` / `Linked` / `Inferred`
- 对同一 issue 的报告质量不低于当前规则+LLM版本
- 对大日志场景仍保持可控的运行时间和 token 消耗

## 当前建议

优先实现“受控 ReAct”而不是“完全自由 Agent”：

- LLM 可决定查什么
- 但只能通过受控工具查
- 所有工具返回都做大小限制
- 所有问题都保留执行轨迹

这是把当前项目平滑升级为 ReAct 版本的最稳路径。
