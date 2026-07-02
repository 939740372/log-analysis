# 日志分析工具

这是一个面向常见 Java 后端日志的两阶段分析 CLI。

1. 第一阶段：仅基于日志做结构化分析，输出中文总结。
2. 第二阶段：结合本地 Java 源码目录，做日志到代码的关联定位，并给出以 LLM 输出为主的修复建议。

项目重点覆盖：

- Spring Boot / Spring Cloud 风格日志
- Java 异常栈、异步异常、线程池日志
- Feign / HTTP 调用日志
- Druid / JDBC / MyBatis 相关日志
- 带时间、级别、线程名、类名、traceId 的后端服务日志

不建议把当前版本理解成“所有日志都能分析”。更准确地说，它是一个“面向常见 Java 后端日志”的分析工具；如果后续要扩展到 Nginx、Redis、Kafka、前端日志、系统日志等，需要继续增加对应解析器。

## 主要能力

- 流式读取大日志文件，避免一次性加载到内存
- 识别常见问题模式：超时、连接池告警、慢请求、Fallback、未处理异常
- 输出中文 Markdown 总结和结构化 JSON
- 第二阶段支持从日志中提取类名、方法名、接口 URI，并关联到 Java 源码
- 第二阶段按问题分开并发请求 LLM
- LLM 请求失败支持自动重试

## 运行环境

### 必需环境

- Python `3.11+`
- `pip`

### 可选环境

- 可访问的 OpenAI 兼容 LLM 服务
- 本项目默认使用：
  - 基础地址：`http://10.130.61.232:8002`
  - 模型名：`InstructModelQwen3`

### 操作系统支持

- macOS
- Linux
- Windows

项目主体仅依赖 Python 标准库，路径处理使用 `pathlib`，因此支持 Windows 盘符路径和反斜杠路径。

## 安装

### 方式一：安装为命令行工具

#### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

#### Windows PowerShell

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

#### Windows CMD

```bat
py -3 -m venv .venv
.venv\Scripts\activate.bat
pip install -e .
```

安装后可直接使用：

```bash
analyze-logs --help
```

### 方式二：不安装，直接在源码目录运行

#### macOS / Linux

```bash
PYTHONPATH=src python3 -m log_analysis --help
```

#### Windows PowerShell

```powershell
$env:PYTHONPATH="src"
py -3 -m log_analysis --help
```

#### Windows CMD

```bat
set PYTHONPATH=src
py -3 -m log_analysis --help
```

## 使用说明

### 查看帮助

```bash
analyze-logs --help
```

### 只运行第一阶段

```bash
analyze-logs esg-system.log esg-hazsub.log --output-dir out
```

### 运行完整两阶段分析

```bash
analyze-logs esg-system.log esg-hazsub.log --source-root /path/to/java/project --output-dir out
```

### Windows 路径示例

```powershell
analyze-logs .\esg-system.log .\esg-hazsub.log --source-root D:\workspace\ums-esgserver --output-dir .\out
```

### 禁用 LLM，仅做规则分析

```bash
analyze-logs esg-system.log --disable-llm
```

### 自定义慢请求阈值

```bash
analyze-logs esg-hazsub.log --slow-threshold-ms 2000 --output-dir out
```

### 指定输出目录

```bash
analyze-logs esg-system.log esg-hazsub.log --output-dir ./analysis-result
```

## 第二阶段行为说明

第二阶段需要传入源码目录：

```bash
analyze-logs esg-system.log esg-hazsub.log --source-root /path/to/java/project --output-dir out
```

当前第二阶段行为如下：

- 先从第一阶段识别出高优先级问题
- 从日志证据中提取：
  - 类名
  - 方法名
  - 接口 URI
  - 关键异常词
- 在源码目录中做静态关联
- 对每个问题单独向 LLM 发起请求
- 并发执行多个问题分析
- 每个问题的 LLM 请求失败时会自动重试
- 输出结果以 LLM 为主，不再用规则建议自动补齐缺失项

这意味着：

- 如果某个问题的 LLM 请求成功，它会出现在 `fix_suggestions.*` 中
- 如果某个问题多次重试后仍失败，会在 `llm_error` 中体现，但不会强行补一条规则建议

## 输出文件

### 第一阶段输出

- `summary.json`
  第一阶段结构化分析结果
- `summary.md`
  第一阶段中文 Markdown 总结

### 第二阶段输出

- `fix_suggestions.json`
  第二阶段结构化修复建议
- `fix_suggestions.md`
  第二阶段中文 Markdown 建议报告

## JSON 输出说明

### `summary.json`

通常包含：

- `overview`
- `files`
- `endpoints`
- `issues`
- `trace_samples`
- `llm_summary`
- `llm_error`

### `fix_suggestions.json`

通常包含：

- `source_root`
- `code_matches`
- `suggestions`
- `llm_error`

## 当前已知限制

- 当前解析器优先支持 Java 后端日志，不保证适配任意日志格式
- 第二阶段依赖日志里能提取出足够强的线索，如类名、方法名、URI、异常文本
- LLM 结果质量依赖模型返回稳定性与证据完整性
- 对于完全非结构化日志，分析效果会明显下降

## Windows 兼容说明

- 路径处理使用 `pathlib`
- 输出文件编码为 `UTF-8`
- 运行主程序不依赖 `bash`、`sed`、`grep` 等 Unix 外部工具
- 推荐通过 `analyze-logs` 或 `python -m log_analysis` 启动

## 开发与验证

### 运行单元测试

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Windows PowerShell：

```powershell
$env:PYTHONPATH="src"
py -3 -m unittest discover -s tests -v
```

### 语法检查

```bash
python3 -m compileall src
```

## 典型场景

- 生产故障初筛
- 大体量 Java 服务日志摘要
- Java 微服务超时 / 连接池 / 异步异常排查
- 从日志快速定位到源码类和方法
- 结合 LLM 生成第一版修复方向
