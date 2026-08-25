# L2 架构：模型理解，确定性代码验证

LoomQ 的 L2 同时提供官方客观评测接口和面向初学者的本地 Web 产品。两者共享
同一套模型调用、QASM 校验和后端能力表，但 UI 的任务拆解与图文解释不会进入
`agent_chat(prompt) -> str` 的客观评分路径。

## 1. 评分约束

| 规则 | 实现 |
|---|---|
| 每个 case 至少调用一次组委会模型 | `agent_chat()` 始终先调用 `chat_completion()` |
| 配置只能来自环境变量 | 只读取 `LOOMQ_LLM_BASE_URL`、`LOOMQ_LLM_API_KEY`、`LOOMQ_LLM_MODEL` |
| thinking 关闭、temperature 0 | `llm_client.py` 统一构造请求 |
| 只能输出 OpenQASM 2.0 和 12 门白名单 | Prompt 声明约束，Parser 和 verifier 确定性拒绝违规产物 |
| 每 case 最长 120 秒 | 总预算 115 秒、单次调用最多 35 秒、最多 3 次候选 |
| 后端选择使用规范 ID | 从本地 `backend_capabilities.json` 确定性筛选，只返回一个最佳 ID |
| 正式评测除模型服务外默认无网络 | 后端表、模拟器、UI、字体和静态资源全部在 `starter_kit/` 内 |

## 2. 客观评测路径

```text
adapter.agent_chat(prompt)
        ↓
agent.core.agent_result()
        ↓ 至少一次真实模型调用
结构化计划 JSON
        ├─ qasm ─────────→ L2 verifier ─→ 通过后返回完整 QASM
        ├─ select_backend → 本地能力表 ─→ 返回一个规范后端 ID
        └─ explain ───────→ 返回模型的大白话答案
```

模型负责理解自然语言并提出候选；是否接受候选由本地代码决定。代码不根据公开
prompt 关键词返回预制答案。

### 2.1 QASM 生成和修复

系统 Prompt 要求模型返回一个 JSON 对象，包含任务类型、目标态、比特数、目标测量
分布、QASM、附加解释和后端约束。QASM 候选按以下顺序校验：

1. 前两条有效语句必须是 `OPENQASM 2.0;` 和
   `include "qelib1.inc";`，且不能出现其他 `include`。
2. `qasm_parser.py` 解析唯一的 `qreg`、`creg`、门和测量，拒绝白名单外门。
3. 声明的目标比特数必须与 `Circuit.qubit_count` 一致；不一致不能降级为
   `syntax_only`。
4. `simulator.py` 执行 Circuit。非法角度、除零和不支持的表达式会转换为重试反馈，
   不会让 `agent_chat()` 崩溃。
5. 用户明确指定测量结果时，按 Hellinger fidelity 校验分布；GHZ、Bell、W 和均匀
   叠加等已知目标态按 statevector fidelity 校验。阈值均为 0.97。
6. 只有无法识别语义的自定义目标才标记为 `syntax_only`。UI 会明确显示“只通过语法
   检查”，不会宣称目标语义已经通过。

候选失败时，Parser 报错、角度错误、比特数差异或实际/目标分布会回灌给同一模型，
最多重试 3 次。非法门不在本地猜测性改写；模型必须输出已经等价分解到以下白名单的
最终程序：

```text
h x s sdg t tdg rz ry cx cu1 swap ccx
```

对于模型明确给出的、小规模目标测量分布，预算耗尽后的兜底合成器只根据已经锁定的
分布构造电路，不读取电路名称或公开样例。无法确定语义时不伪造通过结果。

### 2.2 后端选择

模型只提取 `min_qubits`、`no_queue`、`free_only`、`no_account` 和
`prefer_hardware`。`agent/backends.py` 再读取官方能力表快照并完成确定性过滤和排序。
最终回复只保留一个最佳规范 ID，避免机器评测把多个候选理解为含糊答案。

能力表是比赛提供的版本化快照，不冒充平台实时排队或价格查询。Web 界面会从 JSON
动态展示 `source`、`version`、`status` 和 `realtime` 声明。

## 3. Web 产品路径

```text
POST /api/build
      ↓
agent_result()：生成、修复、解释或选后端
      ↓
decompose-user-request skill：拆成 1～3 个独立任务及跨任务关系
      ├─ circuit_build：逐个生成并通过同一个 verifier
      ├─ backend_select：单独的后端请求显示确定性选择结果
      └─ knowledge task：生成严格校验的图文解释 JSON
```

UI 支持一个请求同时包含知识问题和多个电路任务。拆解结果必须通过字段、任务数量、
任务 ID、类型和关系的确定性校验。若用户要求多个电路“不同”，关系会被表示为
`pairwise_distinct`，并按 `quantum_state`、`measurement_distribution` 或
`circuit_structure` 之一比较已经解析的 Circuit；候选不满足关系时会重试，而不是
复制同一电路。

电路解释只接收已经验证的 Circuit IR、测量映射和概率。Skill 必须为每个
`operation_id` 返回一条对应说明，结构、顺序、长度和枚举值均由 `explainer.py`
校验。概念解释和任务拆解也使用独立 Skill 与严格 JSON 契约。它们负责表达，不参与
QASM 是否正确的判定。

## 4. 模块边界

```text
starter_kit/
├── adapter.py                         官方 L1/L2/L3 入口
├── llm_client.py                      OpenAI-compatible 环境变量传输层
├── qasm_parser.py / simulator.py      L1 与 L2 共享 Circuit IR 和模拟
├── backend_capabilities.json          官方后端能力快照
└── agent/
    ├── core.py                        客观 L2 编排、预算、重试、兜底
    ├── prompts.py                     客观 L2 结构化 Prompt
    ├── verifier.py                    外壳、语法、维度和语义校验
    ├── backends.py                    确定性后端选择
    ├── server.py                      本地 Web API 与多任务执行
    ├── explainer.py                   Skill 调用和严格输出校验
    ├── presenter.py                   可信 Circuit 图数据
    ├── skills/                        任务拆解、概念解释、电路解释
    └── ui.html                        零 CDN 的中英双语 Web UI
```

## 5. 配置和启动

完整 Agent 的客观接口和 UI 使用同一套配置：

```bash
export LOOMQ_LLM_BASE_URL="..."
export LOOMQ_LLM_API_KEY="..."
export LOOMQ_LLM_MODEL="deepseek-v4-flash"
```

密钥不会写入代码、Docker 镜像或提交。评委可从 fork 根目录一键启动：

```bash
./starter_kit/run_demo.sh
```

脚本只要求宿主机安装 Docker，不要求宿主机 Python。它会加载已被 Git 忽略且配置
完整的本地 `.env`（如存在）、构建 Linux/amd64 镜像、自动选择可用端口并启动页面。
没有模型配置时不会退出：教程和 `/api/demo` 离线 Bell 示例仍可用；正常
`/api/build` 请求会明确提示配置，不会返回伪造 Agent 答案。离线示例来自公开
`circuits/bell.qasm`，并通过与 L2 相同的 Parser、模拟器和保真度校验链路。

本地开发者已有 Python 环境时也可直接运行：

```bash
python3 -m starter_kit.agent.server --open
```

## 6. 验证

离线回归不需要模型 Key，HTTP 测试服务器会检查真实请求载荷、模型调用次数、重试
反馈和最终产物：

```bash
python3 -m unittest tests.test_l2_agent.L2AgentTests
```

配置真实模型环境后再运行官方公开 L2 evaluator：

```bash
python3 starter_kit/evaluator.py --level l2
```

2026-08-24 提交前使用 `.env` 注入的真实模型额外执行了四个非公开措辞变体：

| 变体 | 结果 |
|---|---|
| 5 比特“猫态”生成 | 5 比特 GHZ，分布保真度约 1.0 |
| 含大写门和缺逗号的 Bell 代码修复 | 修复为 `h` + `cx`，分布保真度约 1.0 |
| 10 比特、免费、免账号、零排队 | `braket_local_simulator` |
| 至少 50 比特真实 QPU | `originq_wukong` |

该验证只记录结果，不保存 API Key、请求凭证或模型服务地址。

## 7. 已知边界

- `custom` 且没有明确目标分布的电路只能做语法和可执行性检查，不能声称语义正确。
- 本地代码不自动分解任意第三方门；最终输出必须已经落入 12 门白名单，违规候选会反馈
  模型重试。
- 后端选择依据官方静态快照，不查询比赛模型服务之外的公网 API。
- UI 会增加解释类模型调用，但官方 `agent_chat()` 客观路径不会为界面功能付出额外调用。

这些边界会在返回状态和 UI 中明确呈现，不用 `is_mock`、预制 counts 或关键词打表掩盖。
