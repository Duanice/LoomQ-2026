# LoomQ 人工评分证据

这份文件是人工评分材料的统一入口。请直接编辑它，只填写要申报的项目。截图、原始结果或图表统一放在 `starter_kit/evidence/files/`，也可以引用 `starter_kit/` 中已有的代码和文档。

证据包是可选的。没有申报某项人工分时，留空即可，不影响自动评分。

## 提交前填写

把要申报项目的方框改成 `[x]`，并填写对应内容：

- [x] L1 真机
- [x] L2 交互体验
- [x] 工程与产品化
- [ ] 自定义量子 RISC-V Bonus
- [x] 新手引导与视觉叙事 Bonus

## L1 真机

每个有效真机平台计 5 分，最多两个平台。模拟器不计真机分。每个平台复制并填写一次下面的信息：

```text
平台名称：量旋云 2Qubit 核磁量子计算机（gemini_vp）
平台 job ID：G-260810-0005
运行时间：2026-08-10T06:51:08.443204Z
shots：1024
实际执行的 QASM：starter_kit/circuits/bell.qasm
平台返回的原始结果：starter_kit/evidence/files/spinq-raw-result.json
统一结果：starter_kit/evidence/files/spinq-result.json
提交回执：starter_kit/evidence/files/spinq-submission.json
SDK 查询截图：starter_kit/evidence/files/spinq-sdk-task-G-260810-0005.png
```

```text
平台名称：本源量子云悟空真机（WK_C180）
平台 job ID：F016831B161D5F48125A7DAAA50DB335
运行时间：2026-08-10T05:56:39.299326Z
shots：1024
实际执行的 QASM：starter_kit/circuits/bell.qasm
平台返回的原始结果：starter_kit/evidence/files/originq-raw-result.json
统一结果：starter_kit/evidence/files/originq-result.json
提交回执：starter_kit/evidence/files/originq-submission.json
任务页截图：starter_kit/evidence/files/originq-task-F016831B161D5F48125A7DAAA50DB335.png
```

建议把文件放进 `evidence/files/`，比如：

```text
evidence/files/spinq-circuit.qasm
evidence/files/spinq-result.json
evidence/files/spinq-screenshot.png
```

工作人员会核对 job ID、运行时间、电路、shots 和原始结果。截图只能辅助说明，不能代替 job ID 和原始结果。

## L2 交互体验

请填写：

```text
启动界面或 CLI 的命令：python3 -m starter_kit.agent.server --open
测试入口或页面地址：http://127.0.0.1:8000
用于交互体验评测的 3 个用户任务：
1. 让三个量子比特纠缠在一起，并解释测量结果。
2. 我想制备贝尔态，请修复：H q[0]; CX q[0] q[1]
3. 我要运行 15 比特电路，不想排队且不要账号，应该选择哪个平台？
截图或演示视频：无（选填）；工作人员可直接运行上述入口
```

工作人员会在组委会统一模型环境中运行最终代码，测试新手是否看得懂、出错后能否得到有效帮助、结果是否清楚，以及多轮回答是否一致。选手自己的对话截图只用于说明产品流程，不直接证明得分。

## 工程与产品化

已有内容可以直接引用主 README 或其他项目文档，不必复制到本目录。

```text
干净环境中的构建和启动命令：从 fork 根目录执行 ./starter_kit/run_demo.sh；脚本会检查配置、自动选择可用端口、构建 Linux/amd64 Docker 镜像并启动 Web 产品。若只提取 starter_kit/，则在评测根目录执行 ./run_demo.sh
架构说明：starter_kit/README.md 的“统一 L1 架构”“L3 Hybrid-QASM 编译器”和“L2 统一模型与环境变量”章节；L2 的模型、确定性校验和多任务执行边界详见 starter_kit/L2_DESIGN.md
目标用户和使用场景：没有量子计算背景、希望用自然语言学习概念、生成或修复量子电路、查看模拟结果并确定性选择后端的初学者与开发者
完整使用流程：配置 LOOMQ_LLM_* 后执行 python3 -m starter_kit.agent.server --open；在欢迎页直接体验或进入专注模式，输入自然语言任务后依次查看电路图、运行结果、QASM 与白话解释；“文件”菜单可在本机恢复最近对话或导出 QASM，“帮助”菜单可随时打开入门教程
```

工作人员会按最终 commit 实际构建和启动，并检查文档与代码是否一致、产品是否真的降低了量子计算的使用门槛。

## 自定义量子 RISC-V Bonus

以下三项必须齐全且测试通过，才获得 8 分：

```text
指令编码规格：[填写文档路径]
模拟器扩展实现：[填写代码路径]
端到端测试命令：[填写命令或文档路径]
```

## 新手引导与视觉叙事 Bonus

请填写已有材料的路径，不要求为评分另写一套文档：

```text
零基础首次运行指南：starter_kit/agent/ui.html 中“帮助 → 入门教程”的五步交互引导，以及 starter_kit/QUANTUM_101.md
量子概念解释：starter_kit/agent/skills/explain-user-question/SKILL.md、starter_kit/agent/skills/explain-quantum-circuit/SKILL.md 与 starter_kit/agent/explainer.py
结果可视化：starter_kit/agent/ui.html 中可切换的电路图、测量概率柱状图、QASM 和图文解释面板
错误恢复或无障碍引导：starter_kit/agent/core.py 与 starter_kit/agent/verifier.py 会对解析、语法和目标测量语义做确定性自检并反馈重试；starter_kit/agent/ui.html 提供中英双语、键盘可达控件、错误提示和随时可重开的教程
```

以上四项各 1 分。普通项目 README 完整不代表自动获得 Bonus。

## 提交规则

- 所有材料都要在截止前进入最终提交的 commit，工作人员不接受截止后补交。
- 外部视频可以用稳定只读链接，源码、原始结果和复现命令应保存在仓库中。
- 整个 fork commit 的归档包不得超过 100 MiB。
- 不要提交 API Key、Token、Cookie、个人身份信息或平台账户隐私。
- 如申报 L1 真机分，在最终提交 Issue 的 `Hardware evidence` 中填写 `starter_kit/evidence/README.md`。
