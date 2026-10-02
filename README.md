# SciDemo：科学计算 Agent 教学工作流

SciDemo 是一个本地模型驱动的多 Agent 科学计算教学系统。默认连接本机 vLLM：多个职责隔离的 Agent 在共享任务账本上协作，SymPy/SciPy/NumPy 执行计算，独立验证 Agent 检查每次结果。网页可关闭“调用本地 Qwen 模型”使用离线 Mock。

## 架构

```text
backend/app/
  main.py       FastAPI、SSE、任务与人工审核 API
  workflow.py   模型驱动的 decide → tool → verify → observe 循环、interrupt/resume
  agents.py     Problem Analyst、Scientific Solver、Verification Critic、Report Writer
  science.py    AST 白名单解析、符号/数值工具、Plotly artifacts
  storage.py    SQLite 任务快照与有序事件历史
  llm.py        Mock 与 OpenAI-compatible/vLLM 模型适配器
frontend/src/   Vue 3 + TypeScript + Markdown/KaTeX + Plotly + 实时工作流图
backend/tests/  工具、安全、闭环、重试、人工恢复测试
```

每个任务拥有 UUID `thread_id`。LangGraph 使用 checkpointer 支持运行时中断；SQLite 保存可序列化任务快照和稳定递增的事件序号，浏览器刷新后通过 `GET /api/tasks/{id}` 恢复。页面展示决策摘要和结构化输入输出，不展示隐藏推理。

四个大模型 Agent 按 `分析 → 求解 → 独立审查 → 汇总` 协作。求解与审查之间可以循环多次；每次角色切换都会产生 `agent_handoff` 事件，并写入任务的 `active_agent` 与 `agent_handoffs` 字段。它们可以共用同一个本地模型服务，但使用独立角色提示和职责边界。Verification Critic 调用确定性验证工具取得残差或符号证据，再由模型审阅并决定接受或退回；模型不能推翻程序验证失败的硬性结论。

科学工具覆盖表达式化简/展开/因式分解、极限、导数/梯度/Hessian、不定积分/定积分/多重积分、单方程与方程组、矩阵和线性代数、解析与数值 ODE、数值求根、插值、拟合，以及二维函数、隐函数和三维曲面可视化。所有工具均经过服务器端 schema 和安全表达式校验，不执行模型生成的任意 Python。

## 安装与运行

要求 Python 3.11+、uv、Node.js 20+。

```bash
uv sync --inexact
npm --prefix frontend install
cp .env.example .env
chmod +x scripts/start.sh scripts/dev.sh scripts/serve-model.sh
./scripts/start.sh
```

打开 <http://127.0.0.1:5173>。API 文档位于 <http://127.0.0.1:8000/docs>。也可分别启动：

前端和 Agent API 默认监听所有网卡，局域网设备可通过 `http://服务器局域网IP:5173` 访问；vLLM 仍只监听本机回环地址，避免直接暴露模型端口。

```bash
uv run uvicorn app.main:app --app-dir backend --reload --port 8000
npm --prefix frontend run dev
```

## 模型模式

默认连接 `http://127.0.0.1:8001/v1`。vLLM 与后端统一安装在项目 `.venv`；安装时由 uv 根据显卡驱动选择 PyTorch/CUDA：

```bash
uv pip install --python .venv/bin/python "vllm[bench]==0.30.0" --torch-backend=auto
bash scripts/serve-model.sh
```

启动脚本使用现有 `models/Qwen3.5-9B`、两卡张量并行、16384 上下文，并启用 Qwen3 XML 工具调用解析。`scripts/start.sh` 会等待模型就绪后再启动后端与可视化前端；已有模型服务时会直接复用。工具调用配置参考 [vLLM 官方文档](https://docs.vllm.ai/en/stable/features/tool_calling/)。应用配置为：

```text
LLM_PROVIDER=openai-compatible
LLM_MODEL=Qwen3.5-9B
LLM_BASE_URL=http://127.0.0.1:8001/v1
LLM_API_KEY=local
```

`.env` 在后端启动时自动加载。模型不可用会明确失败；只有显式设置 `LLM_FALLBACK_TO_MOCK=true` 才允许回退，并产生回退事件。离线测试使用 Mock。密钥只放 `.env`，不要提交。

也可以配置任意兼容 OpenAI Chat Completions 和工具调用协议的外部模型，并在聊天输入框选择“外部 API”：

```text
EXTERNAL_LLM_MODEL=your-model
EXTERNAL_LLM_BASE_URL=https://provider.example/v1
EXTERNAL_LLM_API_KEY=secret
```

## 登录、游客与会话历史

首次访问会创建带 HttpOnly 会话 Cookie 的游客身份；游客和已登录用户的任务均按身份隔离，并可在左侧恢复多次历史会话。当前登录采用与 UBAA 类似的服务端中转模式：先读取统一认证登录上下文和验证码，再将用户本次输入的学号、密码、验证码提交至北航 SSO，最后通过 `uc.buaa.edu.cn/api/uc/status` 校验身份。密码不落库、不写日志，也不会进入任务状态。

```text
BUAA_CAS_BASE_URL=https://sso.buaa.edu.cn
BUAA_CAS_SERVICE_URL=https://your-domain.example/api/auth/cas/callback
FRONTEND_URL=https://your-domain.example
SESSION_COOKIE_SECURE=true
```

该模式不要求登记 CAS Service URL，但业务服务器会在认证期间接触用户密码，正式部署必须使用 HTTPS、限制日志和访问权限，并在隐私声明中明确说明。认证页面结构变化时，预登录解析器也需要同步维护。

当前开发环境继续使用 SQLite 保存身份、会话、任务和事件，便于单机启动。多实例生产部署建议把持久数据迁至 MySQL/PostgreSQL，把短期会话、SSE 分发和任务锁迁至 Redis；在完成数据库迁移和并发一致性测试前，不应只通过修改连接字符串宣称已支持多实例。

## 课堂演示

1. 启动 vLLM 和前后端，查看右上角模型连接状态。
2. 选择 Newton 示例并勾选教学审核模式，运行后观察模型理解事件。
3. 点击动态决策节点，检查模型生成的工具名、调用参数和调用 ID，批准首个动作。
4. 在聊天中的工具记录或右侧执行图点击节点，查看工具返回值、独立残差检查和模型解释。模型收到标准 `assistant.tool_calls → tool` 消息。
5. 选择失败重试示例，观察验证失败后重新规划；该例的第一次失败为显式教学注入。

“本地模型调用”面板展示调用阶段、模型、耗时与公开摘要。工具 schema、模型选择、执行结果和验证证据可从事件检查器查看。自由文本的表达式和参数由本地模型提取，Mock 的规则参数不会覆盖模型产生的参数。

## API

- `POST /api/tasks`：提交问题、教学模式、容差和最大重试次数。
- `GET /api/tasks/{task_id}`：任务状态及完整事件历史。
- `GET /api/tasks/{task_id}/events?after=N`：SSE 增量事件。
- `POST /api/tasks/{task_id}/review`：在同一任务上 `approve`、`modify` 或 `reject`。
- `GET /api/examples`：七个课堂案例。
- `GET /api/model/status`：检查本地模型服务与模型列表。

审核修改示例：`{"action":"modify","parameters":{"tolerance":1e-10},"comment":"提高精度"}`。

## 验证

```bash
uv run pytest -q
npm --prefix frontend run build
```

测试覆盖表达式注入拒绝、Newton 求根、符号求导、插值、拟合、Mock 端到端、验证失败重试、人工暂停/恢复，以及模拟 OpenAI-compatible HTTP 的真实适配器协议测试：模型返回函数调用、工具执行、ToolMessage 回传。协议测试不等同于真实 GPU 推理测试。

## 安全边界与当前限制

- 不执行用户 Python，不调用字符串 `eval`/`exec`；表达式只允许 `x/y/z/t`、数值、基本运算及白名单数学函数。
- 表达式复杂度、数组长度、绘图点数、重试次数和求解迭代次数均有限制。
- 工具不访问网络或任意文件；图表以 JSON 数据返回，不生成任意路径文件。
- 本地模型可在受控循环中连续调用注册工具，并根据每轮工具观察与验证证据更换工具、修改参数或结束；单轮仅允许一个工具调用，总工具步数受 `MAX_TOOL_STEPS` 限制。Mock 使用确定性决策器。
- LangGraph checkpointer 当前在内存中，后端重启后未完成的人工中断不能继续；SQLite 历史及完成结果仍可读取。
- 工具执行目前在后端进程中；表达式限制并不等同于进程隔离，适用于可信本地课堂环境。
- Plotly 完整包令生产 bundle 较大；正式部署可按需加载图表模块。
