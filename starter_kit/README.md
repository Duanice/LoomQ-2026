# LoomQ Starter Kit v1.1.0

本工具包定义参赛提交协议，并提供公开自测。它不包含正式评分器、隐藏答案、Mock 得分路径或任何 Level 的参考解答。

## 一键启动 Web 产品

LoomQ 面向会描述需求、但不会写 OpenQASM，也不了解量子门和平台 SDK 的普通
学习者。他们只需输入一句自然语言，就能得到经过本地验证的量子电路、运行结果、
QASM 和逐步解释。

评委或用户只需准备 Docker 和 Python 3，并由环境注入 `LOOMQ_LLM_BASE_URL`、
`LOOMQ_LLM_API_KEY` 和 `LOOMQ_LLM_MODEL`。本地开发也可把这三个值写进仓库根目录
下已被 Git 忽略的 `.env`。从 fork 根目录执行一条命令：

```bash
./starter_kit/run_demo.sh
```

脚本会检查配置、构建 Linux/amd64 镜像并启动 Web 界面。默认浏览器地址为
<http://127.0.0.1:8000>；端口被占用时会自动选择下一个可用端口，也可用
`LOOMQ_PORT` 指定起始端口。若组委会只提取了
`starter_kit/`，则在该目录执行 `./run_demo.sh` 即可。API Key 只通过 Docker 环境
转发，不写入镜像、命令参数或日志。

## 提交结构

```text
starter_kit/
├── __init__.py
├── VERSION
├── CHANGELOG.md
├── submission.yaml
├── adapter.py
├── hardware_runner.py
├── platform_runners.py
├── spinqit_worker.py
├── originq_cloud_worker.py
├── hybrid_compiler.py
├── agent/
│   ├── core.py
│   ├── prompts.py
│   ├── verifier.py
│   ├── backends.py
│   ├── presenter.py
│   ├── server.py
│   └── ui.html
├── llm_client.py
├── l2_policy.json
├── L2_DESIGN.md
├── evaluator.py
├── prepare_submission.py
├── riscv_emulator.py
├── backend_capabilities.md
├── backend_capabilities.json
├── QUANTUM_101.md
├── gate_identities.md
├── target_ir_contract.md
├── run_demo.sh
├── requirements.txt
├── requirements-spinq.txt
├── requirements-originq-cloud.txt
├── Dockerfile
├── evidence/
│   ├── README.md
│   └── files/                # 可选附件
├── circuits/
│   ├── bell.qasm
│   └── ghz3.qasm
└── examples/
```

在正式 fork 中，本 `starter_kit/` 目录就是构建与评测根目录，必须保留并填写 `submission.yaml`，同时提供 `adapter.py`。非 Python 项目可以在 `adapter.py` 中通过 `subprocess` 调用自己的 CLI 或二进制。

目录名使用下划线，因此从 fork 根目录编写测试时可以按标准 Python 包导入：

```python
from starter_kit import adapter
```

## 环境

公开 evaluator 只使用 Python 标准库，无需安装依赖。推荐 Python 3.10，与官方基础镜像一致（spinqit 最高只提供 cp310 wheel）：

```bash
python3 evaluator.py --level l1 --target spinq,originq --json-out report.json
```

参赛项目使用第三方 SDK 时，必须把依赖写入 `requirements.txt` 并精确锁定版本，例如 `package==1.2.3`。不要提交 `package>=1.2`，正式评测不会替参赛队选择依赖版本。

### 用 uv 管理本地开发环境（可选）

仓库根目录提供 `pyproject.toml`，可用 [uv](https://docs.astral.sh/uv/) 一键创建 Python 3.10 环境：

```bash
uv sync                      # 仅核心工具（纯标准库）
uv run python -m unittest discover -s tests
```

三家平台 SDK 作为可选 extra 按需安装：

```bash
uv sync --extra originq      # pyqpanda 3.8.5
uv sync --extra braket       # amazon-braket-sdk 1.110.1（3.10 可用的最后一版）
uv sync --extra spinq        # spinqit 0.2.4
```

`braket` 与 `spinq` **不能共存**：spinqit 钉死 `antlr4-python3-runtime==4.9.2`，
而 braket 需要 `==4.13.2`。`pyproject.toml` 已把两者声明为 `tool.uv.conflicts`，
安装时二选一即可，`originq` 与任意一方都能共存。

macOS 安装 `spinq` 后需额外运行一次修复脚本（spinqit 的原生扩展只声明了
Linux 风格的 `$ORIGIN` rpath，macOS dyld 无法解析，会导致 `import spinqit` 失败）：

```bash
uv run --extra spinq python scripts/fix_spinqit_macos.py
```

注意：`pyproject.toml` 仅用于本地开发便利。正式评测仍以 `starter_kit/requirements.txt`
和 `Dockerfile` 为准，请务必同步维护 `requirements.txt`。

也可以先验证基础容器：

```bash
docker build --platform linux/amd64 -t loomq-submission .
docker run --rm loomq-submission
```

`pyqpanda` 的 Linux wheel 是 x86_64；Apple Silicon Mac 也应使用上述
`--platform linux/amd64` 参数构建。

SpinQit 0.2.4 固定依赖 `antlr4-python3-runtime==4.9.2`，Braket 本地模拟器
固定依赖 4.13.2，不能安全安装在同一个 Python 环境。Dockerfile 因此将
`requirements-spinq.txt` 精确安装到 `/opt/loomq-spinqit`，并由
`run_spinq()` 调用隔离 worker；这仍然使用同一个 QASM parser 和 Circuit IR。
如需在非 Docker 环境运行，可把 `LOOMQ_SPINQIT_PYTHON` 指向等价的 Python
3.10 虚拟环境。

## 统一 L1 架构

三平台共享同一个 OpenQASM 2.0 解析器和不可变 `Circuit` 中间表示。目标分支只
负责必要的代码生成、SDK 调用与位序归一化，不会根据电路名称或公开样例返回预制
结果：

```text
OpenQASM 2.0 → parse_qasm() → Circuit IR
                                  ├─ emit_spinq()   → OpenQASM 2.0
                                  ├─ emit_originq() → OriginIR
                                  ├─ emit_braket()  → OpenQASM 3.0
                                  ├─ run_spinq()    → SpinQit BasicSimulator
                                  ├─ run_originq()  → PyQPanda CPUQVM
                                  └─ run_braket()   → Braket LocalSimulator
                                                     ↓
                                      统一 counts 与 little bit order
```

`transpile()` 的输出严格遵循 `target_ir_contract.md`；执行路径则从同一个
`Circuit` 构造各 SDK 接受的对象或具体方言。例如 PyQPanda 接受的 OriginIR
写法与评分契约略有差异，因此规范 emitter 和运行时 emitter 分开，但二者没有
各自重新解析输入。SpinQit worker 仅用于隔离冲突的 ANTLR 依赖。`simulator.py`
只用于本地差分测试和 L2 自验，生产 `run()` 的 counts 全部来自对应平台 SDK。

## 真机证据

真机脚本同样从 OpenQASM 解析为共享 `Circuit` IR，但不会改变自动评测使用的
本地模拟器默认路径。SpinQit 需要 Python 3.10，本源云使用独立的 QPanda3：

```bash
python3.10 -m venv ~/.cache/loomq/spinqit-0.2.4
~/.cache/loomq/spinqit-0.2.4/bin/pip install spinqit==0.2.4

python3.12 -m venv ~/.cache/loomq/pyqpanda3-0.4.0
~/.cache/loomq/pyqpanda3-0.4.0/bin/pip install -r requirements-originq-cloud.txt
```

不带 `--submit` 只做登录和真机可用性检查，不消耗额度：

```bash
python3 examples/run_spinq_hardware.py --username <SPINQ_USERNAME>
python3 examples/run_originq_hardware.py
```

确认后添加 `--submit`，提交一个 1024-shot Bell 真机任务。脚本会隐藏输入本源
API Key，并把 task/job ID、原始结果和统一结果写入 `evidence/files/`：

```bash
python3 examples/run_spinq_hardware.py --username <SPINQ_USERNAME> --submit
python3 examples/run_originq_hardware.py --submit
```

也可使用 `LOOMQ_SPINQ_USERNAME`、`LOOMQ_ORIGINQ_API_KEY`、
`LOOMQ_SPINQIT_PYTHON` 和 `LOOMQ_ORIGINQ_PYTHON` 环境变量。任何 API Key、
Token 或私钥都不得放入仓库。

## Adapter 契约

L1 必须实现：

```python
def transpile(qasm_str: str, target: str) -> str: ...
def run(qasm_str: str, target: str, shots: int) -> dict: ...
```

`transpile()` 的三个目标格式不是任意字符串，规范子集见 `target_ir_contract.md`。正式评测会由组织方解析并模拟返回的目标 IR。

L2、L3 为可选接口：

```python
def agent_chat(prompt: str) -> str: ...
def compile_hybrid(hybrid_qasm_str: str) -> tuple[list, str]: ...
```

未参赛的 Level 保持 `NotImplementedError`，并在 `submission.yaml` 中标为 `false`。Starter Kit 原样运行会失败，这是预期行为，也确保原样提交不会获得功能分。

## L3 Hybrid-QASM 编译器

`compile_hybrid()` 先移除并解析唯一的 `classical { ... }` 块，同时按原始顺序
保留块前后的量子门与测量语句。经典部分使用 tokenizer、递归下降 parser 和 AST，
再编译为官方模拟器支持的 `li/add/sub/addi/beq/bne/j` 指令：

量子操作返回值固定为 `list[str]`：每个元素是一条以分号结尾的完整 OpenQASM 2.0
门或测量语句；保持源码顺序，不包含头部、寄存器声明或 `classical` 内容。

```text
Hybrid-QASM
  ├─ 量子语句 → list[str]
  └─ classical → tokenizer → AST → RISC-V
                                  ├─ r1..r9 → x1..x9
                                  └─ c[k]   → x10+k
```

编译器支持整数字面量、负数、寄存器和测量位、括号、`+ - == !=`、顺序赋值与
嵌套 `if/else`。分支标签全局唯一，临时寄存器在离开表达式前清零。执行公开测试：

```bash
# 在 fork 根目录执行
python3 starter_kit/evaluator.py --level l3
python3 -m unittest tests.test_hybrid_compiler -v

# 更强的随机隐藏集防御测试
python3 starter_kit/l3_defense.py \
  --seed 17001 --programs 1000 --max-cbits 5 --max-depth 5 \
  --json-out starter_kit/l3-defense-report.json
```

## 公开自测

```bash
# 默认只测试 submission.yaml 中声明为 true 的 Level
python3 evaluator.py --json-out report.json

# 单独测试
python3 evaluator.py --level l1 --target spinq,originq,braket
python3 evaluator.py --level l2
python3 evaluator.py --level l3
```

L1 隐藏题型防御测试会运行 GHZ-5、QFT-4、Grover-3、3 个固定深度随机电路，
再为 1～5 比特各生成 20 个随机电路；每个电路同时检查三种目标 IR，并在三个
真实本地 SDK 与参考状态向量之间比较保真度：

```bash
docker run --rm --platform linux/amd64 \
  -v "$PWD:/out" loomq-submission \
  python l1_defense.py --json-out /out/l1-defense-report.json
```

固定种子 `20260825` 的完整基线结果见 [`l1-defense-report.json`](l1-defense-report.json)。

退出码：全部公开测试通过为 `0`，存在失败为 `1`。`report.json` 只表示公开契约自测结果，不是正式分数。

正式评测由组织方在隔离环境运行：每个 case 使用独立进程、私有随机种子和私有期望值；提交进程不会获得理想分布文件。组织方还会分别验证目标原生 IR、真机证据、架构与交互体验。

## 最终提交

截止时间为 **2026-08-25 12:00 UTC+8**。先在 fork 根目录运行：

```bash
python3 starter_kit/prepare_submission.py --team-id <GITHUB_USERNAME>
```

当前不使用预登记队伍名单。每队指定一个 GitHub 提交账号，该账号的用户名就是 Team ID；fork 必须归该账号所有，并由同一账号创建最终提交 Issue。其他成员仍可作为协作者参与开发。预检通过后，在上游 `QAIDAO/LoomQ-2026` 的“LoomQ 最终提交” Issue Form 中填写输出的 fork 地址和 40 位 commit SHA。出现 `submission:accepted` 标签与归档哈希回执后才算提交成功。更新代码后必须新建 Issue，截止前最后一次有效提交生效。

如申报 L1 真机、L2 交互体验、工程与产品化或 Bonus，只需填写 [`evidence/README.md`](evidence/README.md)。截图、原始结果或图表可以统一放入 `evidence/files/`。证据必须随最终 commit 归档；未提交某项证据只影响对应人工分，不影响自动评分。

## L2 统一模型与环境变量

正式 L2 客观评测统一使用 DeepSeek `deepseek-v4-flash`，最终答案仍由确定性的官方测试判定，不使用 LLM 充当裁判。组委会在赛前**不提供 API 地址、API Key、代理或调用额度**。选手本地可使用自己的 DeepSeek API，也可使用其他 OpenAI-compatible 服务调试；组委会只保证正式 DeepSeek 环境下的结果。

`agent_chat(prompt: str) -> str` 接口不变。实现不得硬编码 URL、Key 或模型名，必须读取：

| 环境变量 | 含义 |
|---|---|
| `LOOMQ_LLM_BASE_URL` | OpenAI-compatible API 根地址 |
| `LOOMQ_LLM_API_KEY` | 当前运行凭证 |
| `LOOMQ_LLM_MODEL` | 当前模型；正式评测为 `deepseek-v4-flash` |
| `LOOMQ_LLM_TIMEOUT_SECONDS` | 单次请求超时 |

正式限制为每个 case 时限 120 秒；两组固定私有种子共 12 个 case。机器可读版本见 `l2_policy.json`。

`llm_client.py` 是可选的无依赖传输示例，不包含 Prompt、Agent 策略或参考答案。使用自己的 DeepSeek Key 调试时可设置：

```bash
export LOOMQ_LLM_BASE_URL=https://api.deepseek.com
export LOOMQ_LLM_API_KEY=<YOUR_OWN_KEY>
export LOOMQ_LLM_MODEL=deepseek-v4-flash
export LOOMQ_LLM_TIMEOUT_SECONDS=120
python3 evaluator.py --level l2
```

`agent_chat()` 先调用统一模型，把请求结构化为“生成/修复 QASM”“选择后端”
或“量子概念讲解”。
QASM 会复用 L1 parser 和状态向量模拟器自验，失败时把具体错误反馈给模型重试；
后端选择则只让模型提取约束，再由 `backend_capabilities.json` 确定性筛选。
完整结构与评分边界见 [`L2_DESIGN.md`](L2_DESIGN.md)。

交互界面与同一个 `agent_chat()` 入口相连。配置模型环境变量后运行：

```bash
python3 -m starter_kit.agent.server --open
```

未配置模型时，界面只提示补齐环境变量，不使用关键词规则或硬编码答案模拟 Agent。

缺少配置时应立即失败，错误信息不得包含任何 Key。正式评测时，组委会将统一注入 DeepSeek 模型服务及调用预算；评测环境不保证能够访问其他外部网络服务。若参加 L2，请把 `submission.yaml` 中的 `levels.l2` 与 `network.required_for_l2` 同时改为 `true`；`allowed_hosts` 不用于申请正式评测中的任意公网访问。

## 版本政策

合同版本为 `1.0`。开赛后，`1.x` 只允许增加向后兼容的文档、诊断信息和公开测试，不改变已有接口语义；破坏性修改必须发布新的合同版本并为旧版保留评测通道。
