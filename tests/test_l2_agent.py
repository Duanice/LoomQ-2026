import json
import os
from pathlib import Path
import subprocess
import threading
import unittest
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from starter_kit import adapter
from starter_kit.agent.backends import capability_basis
from starter_kit.agent.core import _small_distribution_qasm
from starter_kit.agent.explainer import (
    DECOMPOSE_SKILL_PATH,
    QUESTION_SKILL_PATH,
    SKILL_PATH,
)
from starter_kit.agent.presenter import __file__ as presenter_path
from starter_kit.agent.server import Handler, UI_PATH, handle_build, offline_demo
from starter_kit.agent.verifier import circuits_are_distinct, extract_qasm, verify
from starter_kit.qasm_parser import GATES, parse_qasm
from starter_kit.evaluator import evaluate_l2


RUN_DEMO_PATH = UI_PATH.parents[1] / "run_demo.sh"


GHZ_QASM = '''OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
creg c[3];
h q[0];
cx q[0], q[1];
cx q[1], q[2];
measure q -> c;'''

GHZ5_QASM = '''OPENQASM 2.0;
include "qelib1.inc";
qreg q[5];
creg c[5];
h q[0];
cx q[0], q[1];
cx q[1], q[2];
cx q[2], q[3];
cx q[3], q[4];
measure q -> c;'''

BELL_QASM = '''OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
h q[0];
cx q[0], q[1];
measure q -> c;'''

REDUNDANT_BELL_QASM = '''OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
x q[1];
x q[1];
h q[0];
cx q[0], q[1];
measure q -> c;'''

SINGLE_QUBIT_PLUS_QASM = '''OPENQASM 2.0;
include "qelib1.inc";
qreg q[1];
creg c[1];
h q[0];
measure q -> c;'''

WRONG_TWO_OUTPUT_QASM = '''OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
creg c[3];
h q[0];
h q[1];
h q[2];
x q[0];
x q[1];
ccx q[0], q[1], q[2];
measure q -> c;'''

TWO_OUTPUT_QASM = '''OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
creg c[3];
h q[0];
cx q[0], q[1];
cx q[0], q[2];
x q[0];
measure q -> c;'''

REVERSED_BIT_ORDER_QASM = '''OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
creg c[3];
h q[0];
cx q[0], q[1];
cx q[0], q[2];
x q[2];
measure q -> c;'''

ALTERNATING_TWO_OUTPUT_QASM = '''OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
creg c[3];
x q[1];
h q[0];
cx q[0], q[1];
cx q[0], q[2];
measure q -> c;'''

WILDCARD_FOUR_OUTPUT_QASM = '''OPENQASM 2.0;
include "qelib1.inc";
qreg q[4];
creg c[4];
x q[2];
h q[3];
h q[0];
measure q -> c;'''

WRONG_WILDCARD_QASM = '''OPENQASM 2.0;
include "qelib1.inc";
qreg q[4];
creg c[4];
h q[0];
h q[1];
h q[2];
h q[3];
measure q -> c;'''

ALL_GATES_QASM = '''OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
creg c[3];
h q[0]; x q[0]; s q[0]; sdg q[0]; t q[0]; tdg q[0];
rz(pi/2) q[0]; ry(-pi/4) q[0];
cx q[0], q[1]; cu1(pi/3) q[0], q[1]; swap q[1], q[2];
ccx q[0], q[1], q[2];
measure q -> c;'''


def plan(**updates):
    value = {
        "task": "qasm",
        "goals": ["qasm"],
        "target_state": "ghz",
        "num_qubits": 3,
        "expected_probabilities": None,
        "qasm": GHZ_QASM,
        "answer": None,
        "constraints": {},
    }
    value.update(updates)
    if "goals" not in updates:
        value["goals"] = {
            "qasm": ["qasm"],
            "select_backend": ["select_backend"],
            "explain": ["explain"],
        }.get(value["task"], [])
    return json.dumps(value, ensure_ascii=False)


def beginner_explanation(operation_count=3):
    return json.dumps(
        {
            "overview": "这个电路把三位量子信息建立联系，并在最后读取它们。",
            "steps": [
                {
                    "operation_id": f"op_{index}",
                    "plain": f"这是根据完整电路生成的第 {index + 1} 步白话说明。",
                    "purpose": f"它为这个电路后续的第 {index + 1} 个环节做准备。",
                    "terms": [
                        {
                            "symbol": f"符号{index + 1}",
                            "meaning": "程序中这一步使用的标准记号。",
                        }
                    ],
                }
                for index in range(operation_count)
            ],
            "measurement": "最后把三位量子信息分别读到三位普通结果中。",
            "result": "本地验证给出了两种主要结果。",
        },
        ensure_ascii=False,
    )


def concept_explanation(
    primary_goal="quantum_concept",
    goals=None,
    label="你想理解量子纠缠",
    title="量子纠缠怎么理解",
    visual_type="flow",
):
    goals = goals or [primary_goal]
    return json.dumps(
        {
            "intent": {
                "primary_goal": primary_goal,
                "goals": goals,
                "label": label,
            },
            "title": title,
            "summary": "多个量子比特形成一个需要整体描述的关联状态。",
            "visual": {
                "type": visual_type,
                "caption": "从分别准备到建立关联，再到读取结果。",
                "items": ([
                    {"label": "分别准备", "detail": "先有两位量子信息。"},
                    {"label": "建立关联", "detail": "让它们成为一个整体状态。"},
                    {"label": "分别读取", "detail": "结果表现出特殊关联。"},
                ] if visual_type == "flow" else [
                    {"label": "贝尔态", "detail": "一种具体的两比特状态。"},
                    {"label": "量子纠缠", "detail": "一类必须整体描述的关联。"},
                ]),
                "link": "贝尔态是纠缠态的典型例子" if visual_type == "relation" else None,
            },
            "cards": [
                {"kind": "definition", "title": "先说结论", "body": "需要把多位量子信息作为整体描述。"},
                {"kind": "analogy", "title": "生活类比", "body": "可以把它想成成对准备的卡片，但类比不能描述全部量子性质。"},
                {"kind": "caution", "title": "常见误解", "body": "这种关联不能用来超光速传递消息。"},
            ],
            "takeaway": "纠缠的重点是整体状态，而不是两位各自藏着固定答案。",
        },
        ensure_ascii=False,
    )


def decomposition(
    tasks=None,
    label="你想构建一个三比特量子电路",
    relationships=None,
):
    tasks = tasks or [("circuit_build", "构建并运行一个三比特量子电路")]
    return json.dumps(
        {
            "label": label,
            "tasks": [
                {"id": f"task_{index}", "kind": kind, "request": request}
                for index, (kind, request) in enumerate(tasks, 1)
            ],
            "relationships": relationships or [],
        },
        ensure_ascii=False,
    )


class ModelHandler(BaseHTTPRequestHandler):
    responses = []
    payloads = []

    def log_message(self, *_args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        type(self).payloads.append(json.loads(self.rfile.read(length)))
        content = type(self).responses.pop(0)
        body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": content}}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class L2AgentTests(unittest.TestCase):
    def test_one_command_demo_launcher_is_valid_and_starts_the_real_ui(self):
        subprocess.run(["bash", "-n", str(RUN_DEMO_PATH)], check=True)
        source = RUN_DEMO_PATH.read_text(encoding="utf-8")

        self.assertTrue(os.access(RUN_DEMO_PATH, os.X_OK))
        self.assertIn("docker build --platform linux/amd64", source)
        self.assertIn("docker run --rm -d --platform linux/amd64", source)
        self.assertIn('docker exec "$CONTAINER_ID" python -c', source)
        self.assertIn("urllib.request.urlopen", source)
        self.assertIn("等待 Web 页面就绪", source)
        self.assertIn("wait_for_http", source)
        self.assertNotIn("command -v python3", source)
        self.assertNotIn("probe.bind", source)
        self.assertIn("自动改用", source)
        self.assertIn("open -a Docker", source)
        self.assertIn("不会擅自安装系统软件", source)
        self.assertIn("docs.docker.com/desktop/setup/install/mac-install/", source)
        self.assertIn("docs.docker.com/engine/install/", source)
        self.assertIn("docs.docker.com/desktop/setup/install/windows-install/", source)
        self.assertIn("离线体验模式", source)
        self.assertIn("python -m agent.server --host 0.0.0.0", source)
        self.assertIn("-e LOOMQ_LLM_API_KEY", source)
        self.assertNotIn("LOOMQ_LLM_API_KEY=", source)

    def test_server_imports_when_starter_kit_is_the_runtime_root(self):
        completed = subprocess.run(
            ["python3", "-c", "import agent.server"],
            cwd=UI_PATH.parents[1],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def setUp(self):
        ModelHandler.responses = []
        ModelHandler.payloads = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.environment = {
            "LOOMQ_LLM_BASE_URL": f"http://127.0.0.1:{self.server.server_port}",
            "LOOMQ_LLM_API_KEY": "local-test-key",
            "LOOMQ_LLM_MODEL": "deepseek-v4-flash",
            "LOOMQ_LLM_TIMEOUT_SECONDS": "2",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_public_evaluator_passes_with_a_valid_model_response(self):
        ModelHandler.responses = [plan()]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = evaluate_l2()

        self.assertEqual(result[0]["status"], "PASS")
        payload = ModelHandler.payloads[0]
        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertNotIn("request_timeout", payload)

    def test_invalid_qasm_is_retried_with_parser_feedback(self):
        invalid = GHZ_QASM.replace("h q[0];", "H q[0];")
        ModelHandler.responses = [plan(qasm=invalid), plan()]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            reply = adapter.agent_chat("生成三比特 GHZ 态并全测量")

        self.assertIn("OPENQASM 2.0", reply)
        self.assertEqual(len(ModelHandler.payloads), 2)
        retry_messages = ModelHandler.payloads[1]["messages"]
        self.assertIn("解析器报错", retry_messages[-1]["content"])

    def test_l2_verifier_rejects_missing_qelib1_include(self):
        qasm = BELL_QASM.replace('include "qelib1.inc";\n', "")

        result = verify(qasm, "bell", 2)

        self.assertFalse(result.ok)
        self.assertEqual(result.stage, "parse")
        self.assertIn('include "qelib1.inc"', result.message)

    def test_l2_verifier_rejects_other_include(self):
        candidates = (
            BELL_QASM.replace("qelib1.inc", "stdgates.inc"),
            BELL_QASM.replace(
                'include "qelib1.inc";',
                'include "qelib1.inc";\ninclude "stdgates.inc";',
            ),
        )

        for qasm in candidates:
            with self.subTest(qasm=qasm):
                result = verify(qasm, "bell", 2)
                self.assertFalse(result.ok)
                self.assertEqual(result.stage, "parse")
                self.assertIn('include "qelib1.inc"', result.message)

    def test_invalid_qasm2_envelope_is_retried(self):
        invalid = GHZ_QASM.replace("qelib1.inc", "stdgates.inc")
        ModelHandler.responses = [plan(qasm=invalid), plan()]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            reply = adapter.agent_chat("生成三比特 GHZ 态并全测量")

        self.assertIn('include "qelib1.inc"', reply)
        self.assertEqual(len(ModelHandler.payloads), 2)
        feedback = ModelHandler.payloads[1]["messages"][-1]["content"]
        self.assertIn('include "qelib1.inc"', feedback)

    def test_outside_whitelist_gate_is_retried(self):
        invalid_gates = ("z q[0];", "u3(pi/2,0,pi) q[0];")
        for gate in invalid_gates:
            with self.subTest(gate=gate):
                invalid = BELL_QASM.replace("h q[0];", gate)
                ModelHandler.responses = [
                    plan(target_state="bell", num_qubits=2, qasm=invalid),
                    plan(target_state="bell", num_qubits=2, qasm=BELL_QASM),
                ]
                ModelHandler.payloads = []
                with mock.patch.dict(os.environ, self.environment, clear=True):
                    reply = adapter.agent_chat("生成两比特 Bell 态并全测量")

                self.assertIn("h q[0]", reply)
                self.assertEqual(len(ModelHandler.payloads), 2)
                feedback = ModelHandler.payloads[1]["messages"][-1]["content"]
                self.assertIn("QASM 无法解析", feedback)

    def test_agent_final_qasm_contains_only_whitelist_gates(self):
        ModelHandler.responses = [
            plan(target_state="custom", num_qubits=3, qasm=ALL_GATES_QASM)
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            reply = adapter.agent_chat("生成一个覆盖白名单门的三比特电路")

        qasm = extract_qasm(reply)
        self.assertIsNotNone(qasm)
        names = {operation.name for operation in parse_qasm(qasm).operations}
        self.assertEqual(names, set(GATES))

    def test_invalid_angle_expression_is_retried_instead_of_crashing(self):
        invalid = BELL_QASM.replace("h q[0];", "rz(pi/0) q[0];")
        ModelHandler.responses = [
            plan(target_state="bell", num_qubits=2, qasm=invalid),
            plan(target_state="bell", num_qubits=2, qasm=BELL_QASM),
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            reply = adapter.agent_chat("生成两比特 Bell 态并全测量")

        self.assertIn("h q[0]", reply)
        self.assertEqual(len(ModelHandler.payloads), 2)
        feedback = ModelHandler.payloads[1]["messages"][-1]["content"]
        self.assertIn("非法角度表达式", feedback)

    def test_target_width_mismatch_is_retried_instead_of_passing_syntax_only(self):
        ModelHandler.responses = [
            plan(num_qubits=5, qasm=GHZ_QASM),
            plan(num_qubits=5, qasm=GHZ5_QASM),
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            reply = adapter.agent_chat("生成五比特 GHZ 态并全测量")

        self.assertIn("qreg q[5]", reply)
        self.assertEqual(len(ModelHandler.payloads), 2)
        feedback = ModelHandler.payloads[1]["messages"][-1]["content"]
        self.assertIn("目标要求 5 个量子比特", feedback)
        self.assertIn("生成电路却声明了 3 个", feedback)

    def test_unknown_target_remains_syntax_only_without_claiming_semantic_pass(self):
        result = verify(BELL_QASM, "custom", 2)
        ui_source = UI_PATH.read_text(encoding="utf-8")

        self.assertTrue(result.ok)
        self.assertEqual(result.stage, "syntax_only")
        self.assertIn("syntaxOnly", ui_source)
        self.assertIn('["fidelity", "distribution"].includes(item.validation_stage)', ui_source)

    def test_backend_constraints_are_resolved_from_the_official_table(self):
        ModelHandler.responses = [
            plan(
                task="select_backend",
                target_state="custom",
                num_qubits=None,
                qasm=None,
                constraints={
                    "min_qubits": 15,
                    "no_queue": True,
                    "free_only": True,
                    "no_account": True,
                    "prefer_hardware": False,
                },
            )
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            reply = adapter.agent_chat("15 比特、免费、零排队且不要账号")

        self.assertIn("braket_local_simulator", reply)
        self.assertNotIn("originq_local_simulator", reply)

    def test_real_hardware_constraint_excludes_cloud_and_simulators(self):
        ModelHandler.responses = [
            plan(
                task="select_backend",
                target_state="custom",
                num_qubits=None,
                qasm=None,
                constraints={
                    "min_qubits": 50,
                    "no_queue": False,
                    "free_only": False,
                    "no_account": False,
                    "prefer_hardware": True,
                },
            )
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            reply = adapter.agent_chat("必须使用至少 50 比特的真实量子计算机")

        self.assertIn("originq_wukong", reply)
        self.assertNotIn("braket_cloud", reply)

    def test_ui_backend_recommendation_includes_dynamic_source_basis(self):
        ModelHandler.responses = [
            plan(
                task="select_backend",
                target_state="custom",
                num_qubits=None,
                qasm=None,
                constraints={"min_qubits": 10, "no_queue": True},
            )
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = handle_build("10 比特且不排队，推荐哪个平台？")

        self.assertEqual(result["kind"], "backend")
        self.assertEqual(result["basis"], capability_basis())
        self.assertFalse(result["basis"]["realtime"])
        ui_source = UI_PATH.read_text(encoding="utf-8")
        self.assertNotIn(result["basis"]["version"], ui_source)
        self.assertNotIn(result["basis"]["status"], ui_source)

    def test_ui_response_contains_structured_circuit(self):
        ModelHandler.responses = [
            plan(),
            beginner_explanation(),
            decomposition(),
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = handle_build("让三个量子比特纠缠在一起")

        self.assertTrue(result["ok"])
        self.assertEqual(result["circuit"]["qubit_count"], 3)
        self.assertEqual(result["circuit"]["operations"][0]["name"], "h")
        self.assertEqual(result["circuit"]["measurements"][-1], {"qubit": 2, "cbit": 2})
        self.assertEqual(result["explanation"]["steps"][0]["operation_id"], "op_0")
        self.assertEqual(len(ModelHandler.payloads), 3)
        skill_call = ModelHandler.payloads[1]["messages"]
        self.assertIn("name: explain-quantum-circuit", skill_call[0]["content"])
        skill_input = json.loads(skill_call[1]["content"])
        self.assertEqual(
            [operation["operation_id"] for operation in skill_input["operations"]],
            ["op_0", "op_1", "op_2"],
        )
        self.assertEqual(skill_input["user_goal"], "让三个量子比特纠缠在一起")

    def test_invalid_beginner_explanation_is_retried_without_faking_content(self):
        ModelHandler.responses = [
            plan(),
            json.dumps({"overview": "缺少步骤"}, ensure_ascii=False),
            beginner_explanation(),
            decomposition(),
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = handle_build("让三个量子比特纠缠在一起")

        self.assertTrue(result["ok"])
        self.assertIsNotNone(result["explanation"])
        self.assertEqual(len(ModelHandler.payloads), 4)
        retry = ModelHandler.payloads[2]["messages"][-1]["content"]
        self.assertIn("deterministic validation", retry)

    def test_invalid_operation_mapping_is_not_shown_as_a_valid_explanation(self):
        invalid = json.loads(beginner_explanation())
        invalid["steps"][0]["operation_id"] = "op_2"
        ModelHandler.responses = [
            plan(),
            json.dumps(invalid),
            json.dumps(invalid),
            decomposition(),
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = handle_build("让三个量子比特纠缠在一起")

        self.assertTrue(result["ok"])
        self.assertIsNone(result["explanation"])
        self.assertIn("解释暂时不可用", result["explain"])

    def test_ui_concept_question_returns_plain_language_answer(self):
        ModelHandler.responses = [
            plan(
                task="explain",
                target_state="custom",
                num_qubits=None,
                qasm=None,
                answer="纠缠是多个量子比特形成一个不可分割的整体。",
            ),
            decomposition(
                [("quantum_concept", "解释什么是量子纠缠")],
                "你想理解量子纠缠",
            ),
            concept_explanation(),
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = handle_build("什么是量子纠缠？")

        self.assertEqual(result["kind"], "explanation")
        self.assertTrue(result["ok"])
        self.assertIn("纠缠", result["explain"])
        self.assertEqual(result["intent"]["primary_goal"], "quantum_concept")
        self.assertEqual(result["intent"]["goals"], ["quantum_concept"])
        self.assertEqual(result["intent"]["label"], "你想理解量子纠缠")
        self.assertEqual(result["concept"]["visual"]["type"], "flow")
        self.assertEqual(len(result["concept"]["cards"]), 3)
        self.assertEqual(len(ModelHandler.payloads), 3)
        skill_messages = ModelHandler.payloads[2]["messages"]
        self.assertIn("name: explain-user-question", skill_messages[0]["content"])
        skill_input = json.loads(skill_messages[1]["content"])
        self.assertEqual(skill_input["question"], "解释什么是量子纠缠")

    def test_product_question_uses_the_models_validated_dynamic_intent(self):
        label = "你想了解 LoomQ 能完成哪些工作"
        ModelHandler.responses = [
            plan(
                task="explain",
                target_state="custom",
                num_qubits=None,
                qasm=None,
                answer="LoomQ 可以把自然语言要求转成经过验证的量子电路。",
            ),
            decomposition(
                [("product_help", "解释 LoomQ 可以完成哪些工作")],
                label,
            ),
            concept_explanation(
                primary_goal="product_help",
                label=label,
                title="LoomQ 能帮你做什么",
            ),
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = handle_build("你们这个 LoomQ 工具能做什么？")

        self.assertEqual(
            result["intent"],
            {"primary_goal": "product_help", "goals": ["product_help"], "label": label},
        )
        self.assertEqual(result["concept"]["title"], "LoomQ 能帮你做什么")

    def test_compound_request_returns_both_a_real_circuit_and_relation_lesson(self):
        label = "你想理解贝尔态与纠缠的关系，并生成示例电路"
        draft = "贝尔态是两量子比特纠缠态的典型例子。"
        ModelHandler.responses = [
            plan(goals=["explain", "qasm"], answer=draft),
            beginner_explanation(),
            decomposition(
                [
                    ("quantum_concept", "解释贝尔态与量子纠缠的关系"),
                    ("circuit_build", "生成一个贝尔态示例电路图"),
                ],
                label,
            ),
            concept_explanation(
                label="你想理解贝尔态与量子纠缠的关系",
                title="贝尔态与纠缠的关系",
                visual_type="relation",
            ),
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = handle_build("贝尔态和纠缠什么关系？给我一个示例电路图")

        self.assertEqual(result["kind"], "circuit")
        self.assertTrue(result["ok"])
        self.assertIn("OPENQASM 2.0", result["qasm"])
        self.assertEqual(
            result["intent"]["goals"], ["quantum_concept", "circuit_build"]
        )
        self.assertEqual(result["concept"]["visual"]["type"], "relation")
        self.assertEqual(
            result["concept"]["visual"]["link"],
            "贝尔态是纠缠态的典型例子",
        )
        self.assertEqual(
            result["tasks"],
            [
                {
                    "id": "task_1",
                    "kind": "quantum_concept",
                    "request": "解释贝尔态与量子纠缠的关系",
                },
                {
                    "id": "task_2",
                    "kind": "circuit_build",
                    "request": "生成一个贝尔态示例电路图",
                },
            ],
        )
        decomposition_messages = ModelHandler.payloads[2]["messages"]
        self.assertIn(
            "name: decompose-user-request",
            decomposition_messages[0]["content"],
        )
        lesson_input = json.loads(ModelHandler.payloads[3]["messages"][1]["content"])
        self.assertEqual(
            lesson_input["question"], "解释贝尔态与量子纠缠的关系"
        )
        self.assertIn(draft, lesson_input["draft_answer"])
        self.assertNotIn("OPENQASM 2.0", lesson_input["draft_answer"])

    def test_bitstring_question_and_build_request_remain_two_locked_tasks(self):
        prompt = "运行结果是000和111都是啥意思？构建一个运行结果是101和010的电路。"
        expected = {"010": 0.5, "101": 0.5}
        ModelHandler.responses = [
            plan(
                target_state="custom",
                expected_probabilities=expected,
                qasm=ALTERNATING_TWO_OUTPUT_QASM,
                answer="000 表示三位测量都为 0，111 表示三位测量都为 1。",
                goals=["explain", "qasm"],
            ),
            beginner_explanation(4),
            decomposition(
                [
                    ("quantum_concept", "解释测量结果 000 和 111 的含义"),
                    ("circuit_build", "构建只输出 101 和 010 的三比特电路"),
                ],
                "你想理解三位测量结果并构建指定输出电路",
            ),
            concept_explanation(
                label="你想理解三位测量结果",
                title="三位测量结果怎么读",
            ),
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = handle_build(prompt)

        self.assertTrue(result["ok"])
        self.assertEqual(
            [(task["kind"], task["request"]) for task in result["tasks"]],
            [
                ("quantum_concept", "解释测量结果 000 和 111 的含义"),
                ("circuit_build", "构建只输出 101 和 010 的三比特电路"),
            ],
        )
        self.assertEqual(set(result["probabilities"]), set(expected))
        for outcome, probability in expected.items():
            self.assertAlmostEqual(result["probabilities"][outcome], probability)
        lesson_input = json.loads(ModelHandler.payloads[3]["messages"][1]["content"])
        self.assertEqual(
            lesson_input["question"], "解释测量结果 000 和 111 的含义"
        )
        self.assertNotIn("101", lesson_input["question"])
        self.assertNotIn("010", lesson_input["question"])

    def test_three_deliverables_keep_two_distinct_circuit_tasks(self):
        prompt = (
            "运行结果是000和111都是啥意思？构建一个运行结果是101和010的电路。"
            "再构建一个运行结果是110x和010x的电路，其中x是0或者1。"
        )
        first_expected = {"010": 0.5, "101": 0.5}
        second_expected = {
            "0100": 0.25,
            "0101": 0.25,
            "1100": 0.25,
            "1101": 0.25,
        }
        wrong_wildcard_plan = plan(
            target_state="custom",
            num_qubits=4,
            expected_probabilities=second_expected,
            qasm=WRONG_WILDCARD_QASM,
        )
        ModelHandler.responses = [
            plan(
                target_state="custom",
                expected_probabilities=first_expected,
                qasm=ALTERNATING_TWO_OUTPUT_QASM,
                answer="000 表示三位测量都为 0，111 表示三位测量都为 1。",
                goals=["explain", "qasm"],
            ),
            beginner_explanation(4),
            decomposition(
                [
                    ("quantum_concept", "解释测量结果 000 和 111 的含义"),
                    ("circuit_build", "构建只输出 101 和 010 的三比特电路"),
                    (
                        "circuit_build",
                        "构建输出匹配 110x 和 010x 的四比特电路，其中 x 为 0 或 1",
                    ),
                ],
                "你想理解测量结果并分别构建两个指定输出电路",
            ),
            concept_explanation(
                label="你想理解三位测量结果",
                title="三位测量结果怎么读",
            ),
            wrong_wildcard_plan,
            wrong_wildcard_plan,
            wrong_wildcard_plan,
            beginner_explanation(3),
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = handle_build(prompt)

        self.assertTrue(result["ok"])
        self.assertTrue(result["all_tasks_ok"])
        self.assertEqual(
            [task["kind"] for task in result["tasks"]],
            ["quantum_concept", "circuit_build", "circuit_build"],
        )
        self.assertEqual(len(result["circuits"]), 2)
        self.assertEqual(
            [item["task_id"] for item in result["circuits"]],
            ["task_2", "task_3"],
        )
        self.assertEqual(
            result["circuits"][0]["expected_probabilities"], first_expected
        )
        self.assertEqual(
            result["circuits"][1]["expected_probabilities"], second_expected
        )
        self.assertEqual(
            set(result["circuits"][1]["probabilities"]), set(second_expected)
        )
        self.assertNotEqual(result["circuits"][1]["qasm"], WRONG_WILDCARD_QASM)
        self.assertEqual(len(ModelHandler.payloads), 8)

    def test_related_circuit_tasks_receive_context_and_retry_duplicates(self):
        prompt = "构建两个不同的量子电路"
        relationship = {
            "type": "pairwise_distinct",
            "task_ids": ["task_1", "task_2"],
            "basis": "quantum_state",
        }
        ModelHandler.responses = [
            plan(
                target_state="bell",
                num_qubits=2,
                qasm=BELL_QASM,
            ),
            beginner_explanation(2),
            decomposition(
                [
                    ("circuit_build", "构建一个两比特关联电路"),
                    ("circuit_build", "构建一个单比特叠加电路"),
                ],
                prompt,
                [relationship],
            ),
            plan(
                target_state="bell",
                num_qubits=2,
                qasm=BELL_QASM,
            ),
            plan(
                target_state="uniform",
                num_qubits=1,
                qasm=SINGLE_QUBIT_PLUS_QASM,
            ),
            beginner_explanation(1),
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = handle_build(prompt)

        self.assertTrue(result["ok"])
        self.assertTrue(result["all_tasks_ok"])
        self.assertEqual(result["relationships"], [relationship])
        self.assertEqual(len(result["circuits"]), 2)
        self.assertNotEqual(
            result["circuits"][0]["qasm"], result["circuits"][1]["qasm"]
        )
        first_attempt = ModelHandler.payloads[3]["messages"][1]["content"]
        retry_attempt = ModelHandler.payloads[4]["messages"][1]["content"]
        self.assertIn('"relationships"', first_attempt)
        self.assertIn("OPENQASM 2.0", first_attempt)
        self.assertIn('"validation_feedback"', retry_attempt)

    def test_unresolved_cross_task_duplicate_is_not_reported_as_passing(self):
        prompt = "构建两个不同的量子电路"
        relationship = {
            "type": "pairwise_distinct",
            "task_ids": ["task_1", "task_2"],
            "basis": "quantum_state",
        }
        duplicate = plan(
            target_state="bell",
            num_qubits=2,
            qasm=BELL_QASM,
        )
        ModelHandler.responses = [
            duplicate,
            beginner_explanation(2),
            decomposition(
                [
                    ("circuit_build", "构建第一个量子电路"),
                    ("circuit_build", "构建第二个量子电路"),
                ],
                prompt,
                [relationship],
            ),
            duplicate,
            duplicate,
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = handle_build(prompt)

        self.assertTrue(result["ok"])
        self.assertFalse(result["all_tasks_ok"])
        self.assertTrue(result["circuits"][0]["ok"])
        self.assertFalse(result["circuits"][1]["ok"])
        self.assertIn("pairwise_distinct", result["circuits"][1]["message"])

    def test_relation_basis_is_checked_on_shared_circuit_ir(self):
        bell = parse_qasm(BELL_QASM)
        redundant_bell = parse_qasm(REDUNDANT_BELL_QASM)

        self.assertTrue(
            circuits_are_distinct(bell, redundant_bell, "circuit_structure")
        )
        self.assertFalse(
            circuits_are_distinct(bell, redundant_bell, "quantum_state")
        )
        self.assertFalse(
            circuits_are_distinct(
                bell, redundant_bell, "measurement_distribution"
            )
        )

    def test_custom_output_distribution_rejects_uniform_circuit(self):
        expected = {"001": 0.5, "110": 0.5}

        wrong = verify(WRONG_TWO_OUTPUT_QASM, "custom", 3, expected)
        correct = verify(TWO_OUTPUT_QASM, "custom", 3, expected)

        self.assertFalse(wrong.ok)
        self.assertEqual(wrong.stage, "distribution")
        self.assertAlmostEqual(wrong.fidelity, 0.25)
        self.assertTrue(correct.ok)
        self.assertEqual(set(correct.probabilities), set(expected))
        for key in expected:
            self.assertAlmostEqual(correct.probabilities[key], expected[key])
        self.assertAlmostEqual(correct.fidelity, 1.0)

    def test_local_distribution_synthesis_is_not_tied_to_example_bitstrings(self):
        expected = {"0101": 0.25, "1110": 0.75}
        qasm = _small_distribution_qasm(expected)

        result = verify(qasm, "custom", 4, expected)

        self.assertTrue(result.ok)
        self.assertEqual(set(result.probabilities), set(expected))
        for key in expected:
            self.assertAlmostEqual(result.probabilities[key], expected[key])

    def test_local_distribution_synthesis_supports_uniform_affine_results(self):
        expected = {
            "0100": 0.25,
            "0101": 0.25,
            "1100": 0.25,
            "1101": 0.25,
        }
        qasm = _small_distribution_qasm(expected)

        result = verify(qasm, "custom", 4, expected)

        self.assertTrue(result.ok)
        self.assertEqual(set(result.probabilities), set(expected))
        for key in expected:
            self.assertAlmostEqual(result.probabilities[key], expected[key])
        self.assertAlmostEqual(result.fidelity, 1.0)

    def test_local_distribution_synthesis_rejects_non_affine_uniform_results(self):
        self.assertIsNone(
            _small_distribution_qasm(
                {"000": 0.25, "001": 0.25, "010": 0.25, "111": 0.25}
            )
        )

    def test_bad_custom_distribution_is_retried_before_reaching_ui(self):
        expected = {"001": 0.5, "110": 0.5}
        ModelHandler.responses = [
            plan(
                target_state="custom",
                expected_probabilities=expected,
                qasm=WRONG_TWO_OUTPUT_QASM,
                answer="000 和 111 是三位测量结果；下面另做目标电路。",
                goals=["explain", "qasm"],
            ),
            plan(
                target_state="custom",
                expected_probabilities=None,
                qasm=TWO_OUTPUT_QASM,
                answer="000 和 111 是三位测量结果；下面另做目标电路。",
                goals=["explain", "qasm"],
            ),
            beginner_explanation(4),
            decomposition(
                [
                    ("quantum_concept", "解释测量结果 000 和 111 的含义"),
                    ("circuit_build", "构建只输出 110 和 001 的三比特电路"),
                ],
                "你想理解测量结果并构建指定输出电路",
            ),
            concept_explanation(
                label="你想理解三位测量结果",
            ),
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = handle_build(
                "运行结果 000 和 111 是什么意思？构建一个结果为 110 和 001 的电路。"
            )

        self.assertTrue(result["ok"])
        self.assertEqual(set(result["probabilities"]), set(expected))
        for key in expected:
            self.assertAlmostEqual(result["probabilities"][key], expected[key])
        self.assertEqual(result["expected_probabilities"], expected)
        self.assertEqual(result["validation_stage"], "distribution")
        self.assertAlmostEqual(result["fidelity"], 1.0)
        retry = ModelHandler.payloads[1]["messages"][-1]["content"]
        self.assertIn("保真度只有 0.250", retry)
        self.assertIn("目标测量分布为：001=0.500, 110=0.500", retry)
        self.assertIn("最右位对应 q[0]", retry)

    def test_repeated_bit_order_errors_use_generic_local_distribution_synthesis(self):
        expected = {"001": 0.5, "110": 0.5}
        wrong_plan = plan(
            target_state="custom",
            expected_probabilities=expected,
            qasm=REVERSED_BIT_ORDER_QASM,
            answer="000 表示三位都是 0，111 表示三位都是 1。",
            goals=["explain", "qasm"],
        )
        ModelHandler.responses = [
            wrong_plan,
            wrong_plan,
            wrong_plan,
            beginner_explanation(4),
            decomposition(
                [
                    ("quantum_concept", "解释测量结果 000 和 111 的含义"),
                    ("circuit_build", "构建只输出 110 和 001 的三比特电路"),
                ],
                "你想理解测量结果并构建指定输出电路",
            ),
            concept_explanation(
                label="你想理解三位测量结果",
            ),
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = handle_build(
                "运行结果 000 和 111 是什么意思？构建一个结果为 110 和 001 的电路。"
            )

        self.assertTrue(result["ok"])
        self.assertNotEqual(result["qasm"], REVERSED_BIT_ORDER_QASM)
        self.assertEqual(set(result["probabilities"]), set(expected))
        for key in expected:
            self.assertAlmostEqual(result["probabilities"][key], expected[key])
        self.assertAlmostEqual(result["fidelity"], 1.0)
        self.assertEqual(len(ModelHandler.payloads), 6)

    def test_failed_complex_circuit_does_not_hide_completed_explanation_task(self):
        answer = "000 表示三位都是 0，111 表示三位都是 1。"
        wrong_plan = plan(
            target_state="custom",
            expected_probabilities={"001": 0.4, "010": 0.3, "100": 0.3},
            qasm=GHZ_QASM,
            answer=answer,
            goals=["explain", "qasm"],
        )
        ModelHandler.responses = [
            wrong_plan,
            wrong_plan,
            wrong_plan,
            decomposition(
                [
                    ("quantum_concept", "解释三位测量结果"),
                    ("circuit_build", "生成一个三结果电路"),
                ],
                "你想理解测量结果并生成三结果电路",
            ),
            concept_explanation(),
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = handle_build("先解释测量，再生成一个三结果电路")

        self.assertEqual(result["kind"], "partial")
        self.assertFalse(result["ok"])
        self.assertEqual(result["explain"], answer)
        self.assertIsNotNone(result["concept"])

    def test_validated_multi_goal_recovers_a_circuit_after_initial_misrouting(self):
        label = "你想理解贝尔态与纠缠的关系，并生成示例电路"
        ModelHandler.responses = [
            plan(
                task="explain",
                goals=["explain"],
                qasm=None,
                answer="贝尔态是两量子比特纠缠态的典型例子。",
            ),
            decomposition(
                [
                    ("quantum_concept", "解释贝尔态与纠缠的关系"),
                    ("circuit_build", "生成一个贝尔态示例电路"),
                ],
                label=label,
            ),
            plan(),
            beginner_explanation(),
            concept_explanation(visual_type="relation"),
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = handle_build("贝尔态和纠缠什么关系？给我一个示例电路图")

        self.assertEqual(result["kind"], "circuit")
        self.assertTrue(result["ok"])
        self.assertEqual(result["intent"]["goals"], ["quantum_concept", "circuit_build"])
        self.assertEqual(result["concept"]["visual"]["type"], "relation")
        self.assertEqual(len(ModelHandler.payloads), 5)
        recovery_prompt = ModelHandler.payloads[2]["messages"][1]["content"]
        self.assertIn("validated intent includes circuit_build", recovery_prompt)

    def test_invalid_visual_lesson_falls_back_to_the_original_answer(self):
        reply = "这是仍然可以展示的原始概念回答。"
        invalid = json.loads(concept_explanation())
        invalid["intent"]["primary_goal"] = "made_up_goal"
        ModelHandler.responses = [
            plan(
                task="explain",
                target_state="custom",
                num_qubits=None,
                qasm=None,
                answer=reply,
            ),
            decomposition(
                [("quantum_concept", "解释一个量子概念")],
                "你想理解一个量子概念",
            ),
            json.dumps(invalid, ensure_ascii=False),
            json.dumps(invalid, ensure_ascii=False),
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = handle_build("解释一个量子概念")

        self.assertTrue(result["ok"])
        self.assertIsNone(result["concept"])
        self.assertEqual(
            result["intent"],
            {
                "primary_goal": "quantum_concept",
                "goals": ["quantum_concept"],
                "label": "你想理解一个量子概念",
            },
        )
        self.assertEqual(result["explain"], reply)

    def test_ui_without_model_config_does_not_fake_an_agent_answer(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            result = handle_build("什么是量子纠缠？")

        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "error")
        self.assertIn("LOOMQ_LLM_API_KEY", result["message"])
        self.assertEqual(ModelHandler.payloads, [])

    def test_offline_bell_demo_needs_no_model_and_is_locally_verified(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            result = offline_demo()

        self.assertTrue(result["ok"])
        self.assertTrue(result["offline_demo"])
        self.assertEqual(result["source"], "starter_kit/circuits/bell.qasm")
        self.assertEqual(result["validation_stage"], "fidelity")
        self.assertGreaterEqual(result["fidelity"], 0.97)
        self.assertEqual(set(result["probabilities"]), {"00", "11"})
        self.assertAlmostEqual(result["probabilities"]["00"], 0.5)
        self.assertAlmostEqual(result["probabilities"]["11"], 0.5)
        self.assertEqual(ModelHandler.payloads, [])

    def test_offline_demo_http_endpoint_does_not_require_model_config(self):
        ui_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=ui_server.serve_forever, daemon=True)
        thread.start()
        try:
            with mock.patch.dict(os.environ, {}, clear=True):
                connection = HTTPConnection("127.0.0.1", ui_server.server_port)
                body = json.dumps({"language": "zh"}).encode()
                connection.request(
                    "POST",
                    "/api/demo",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                result = json.loads(response.read())
                connection.close()
            self.assertEqual(response.status, 200)
            self.assertTrue(result["ok"])
            self.assertTrue(result["offline_demo"])
            self.assertEqual(ModelHandler.payloads, [])
        finally:
            ui_server.shutdown()
            ui_server.server_close()
            thread.join(timeout=2)

    def test_landing_page_routes_only_ctas_to_the_existing_builder(self):
        ui_source = UI_PATH.read_text(encoding="utf-8")

        self.assertIn('id="launch-demo"', ui_source)
        self.assertIn('id="get-started"', ui_source)
        self.assertIn('id="app-view" class="app-view"', ui_source)
        self.assertNotIn('id="app-view" class="app-view" hidden', ui_source)
        self.assertIn('expandBuilder(true)', ui_source)
        self.assertIn('expandBuilder(false, true)', ui_source)
        self.assertIn('build("/api/demo")', ui_source)
        self.assertIn('async function build(endpoint="/api/build")', ui_source)
        self.assertIn("no language model was called", ui_source)
        self.assertIn('history.pushState({}, "", route)', ui_source)
        self.assertIn('$("go").addEventListener("click", () => build())', ui_source)
        self.assertNotIn('$("app-view").addEventListener', ui_source)
        self.assertNotIn('class="preview-window"', ui_source)
        self.assertNotIn("产品能力", ui_source)
        self.assertNotIn("工作原理", ui_source)
        self.assertNotIn("关于比赛", ui_source)

    def test_builder_window_controls_return_home_and_toggle_fullscreen(self):
        ui_source = UI_PATH.read_text(encoding="utf-8")

        self.assertIn('id="window-minimize"', ui_source)
        self.assertIn('id="window-maximize"', ui_source)
        self.assertIn('id="window-close"', ui_source)
        self.assertIn('function returnHome(){ location.assign("/"); }', ui_source)
        self.assertIn('await appWindow.requestFullscreen()', ui_source)
        self.assertIn('await document.exitFullscreen()', ui_source)
        self.assertIn('document.addEventListener("fullscreenchange", updateWindowControls)', ui_source)
        self.assertIn("font-size:clamp(16px,.62vw,18px)", ui_source)
        self.assertIn(".window.window-maximized :is(.titlebar,.menubar", ui_source)

    def test_tutorial_dialog_is_viewport_centered_with_safe_scroll(self):
        ui_source = UI_PATH.read_text(encoding="utf-8")

        self.assertIn("display:flex; align-items:center", ui_source)
        self.assertIn("max-height:calc(100vh - 64px)", ui_source)
        self.assertIn("transform:translateY(-3vh)", ui_source)
        self.assertIn(".tutorial-body{padding:18px 20px 22px; overflow:auto}", ui_source)

    def test_edit_menu_is_current_and_help_opens_the_tutorial(self):
        ui_source = UI_PATH.read_text(encoding="utf-8")

        self.assertIn('class="menu-current" aria-current="page" data-i18n="mEdit"', ui_source)
        self.assertIn('id="help-menu" role="button"', ui_source)
        self.assertIn('$("help-menu").addEventListener("click", open)', ui_source)

    def test_file_menu_persists_and_restores_recent_local_conversations(self):
        ui_source = UI_PATH.read_text(encoding="utf-8")

        self.assertIn('id="file-menu" role="button"', ui_source)
        self.assertIn('id="recent-list"', ui_source)
        self.assertIn('const HISTORY_KEY = "loomq-recent-v1"', ui_source)
        self.assertIn("const HISTORY_LIMIT = 8", ui_source)
        self.assertIn("function saveConversation(prompt)", ui_source)
        self.assertIn("function restoreConversation(id)", ui_source)
        self.assertIn('if(data.ok || data.kind === "partial") saveConversation(prompt)', ui_source)
        self.assertIn("localStorage.setItem(HISTORY_KEY", ui_source)
        self.assertIn("localStorage.removeItem(HISTORY_KEY)", ui_source)
        self.assertIn("new Blob([state.qasm]", ui_source)

    def test_view_menu_reuses_tabs_fullscreen_and_beginner_tip(self):
        ui_source = UI_PATH.read_text(encoding="utf-8")

        self.assertIn('id="view-menu" role="button"', ui_source)
        for tab in ("circuit", "result", "qasm", "explain"):
            self.assertIn(f'data-view="{tab}"', ui_source)
        self.assertIn("selectTab(button.dataset.view)", ui_source)
        self.assertIn("await toggleWindowFullscreen()", ui_source)
        self.assertIn('const TIPS_KEY = "loomq-hide-tips"', ui_source)
        self.assertIn('id="help-rail" class="help-rail"', ui_source)

    def test_landing_builder_and_demo_urls_serve_the_ui(self):
        ui_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=ui_server.serve_forever, daemon=True)
        thread.start()
        try:
            for path in ("/", "/builder", "/builder?demo=bell"):
                connection = HTTPConnection("127.0.0.1", ui_server.server_port)
                connection.request("GET", path)
                response = connection.getresponse()
                body = response.read().decode("utf-8")
                connection.close()
                self.assertEqual(response.status, 200)
                self.assertIn('id="app-view"', body)
        finally:
            ui_server.shutdown()
            ui_server.server_close()
            thread.join(timeout=2)

    def test_circuit_diagram_uses_skill_output_instead_of_gate_answer_table(self):
        ui_source = UI_PATH.read_text(encoding="utf-8")
        skill_source = SKILL_PATH.read_text(encoding="utf-8")
        presenter_source = Path(presenter_path).read_text(encoding="utf-8")

        self.assertNotIn("GATE_HELP", ui_source)
        self.assertNotIn("PLAIN_WORDS", presenter_source)
        self.assertIn("explanation?.steps?.find", ui_source)
        self.assertIn("item.operation_id === `op_${index}`", ui_source)
        self.assertIn('data-detail="${escAttr(title)}"', ui_source)
        self.assertIn("gate.dataset.detail", ui_source)
        self.assertNotIn("q[0]", skill_source)
        self.assertNotIn("H（", skill_source)
        self.assertIn("Do not rely on canned gate descriptions", skill_source)

    def test_ui_renders_validated_visual_lessons_without_keyword_branches(self):
        ui_source = UI_PATH.read_text(encoding="utf-8")
        skill_source = QUESTION_SKILL_PATH.read_text(encoding="utf-8")
        decompose_source = DECOMPOSE_SKILL_PATH.read_text(encoding="utf-8")
        server_source = (UI_PATH.parent / "server.py").read_text(encoding="utf-8")

        self.assertIn("function conceptLessonHtml(lesson)", ui_source)
        self.assertIn('visual-${visualType}', ui_source)
        self.assertIn("visual-link", ui_source)
        self.assertIn('visualType === "relation"', ui_source)
        self.assertIn("function taskBlockHtml(number, title, detail, body)", ui_source)
        self.assertIn("function taskNumber(kind)", ui_source)
        self.assertIn("function circuitItems()", ui_source)
        self.assertIn("circuitItems().map", ui_source)
        self.assertIn("circuitItems().forEach", ui_source)
        # 文案已改为 I18N 查表（中英切换），断言仍确保不是关键词分支渲染。
        self.assertIn(
            "taskBlockHtml(knowledgeNumber, I18N[LANG].understandQ",
            ui_source,
        )
        self.assertIn(
            "taskBlockHtml(taskIdNumber(item.task_id), I18N[LANG].genCircuit",
            ui_source,
        )
        self.assertIn("state.tasks", ui_source)
        self.assertIn("function expectedResultText(expected=state.expected)", ui_source)
        self.assertIn("lesson.cards.map", ui_source)
        self.assertIn("data.intent?.label", ui_source)
        self.assertNotIn("听懂了：你想了解一个量子概念", ui_source)
        self.assertIn("${esc(s.t)}", ui_source)
        self.assertNotIn('includes("纠缠")', ui_source)
        self.assertNotIn('includes("叠加")', ui_source)
        self.assertIn("This skill never decides how many tasks", skill_source)
        self.assertIn("already been completed by `decompose-user-request`", skill_source)
        self.assertIn("independently satisfiable", decompose_source)
        self.assertIn("Do not let a later construction request absorb", decompose_source)
        self.assertIn("task kinds may repeat", decompose_source)
        self.assertIn("multiple requested artifacts", decompose_source)
        self.assertIn("Cross-task Relationships", decompose_source)
        self.assertIn("pairwise_distinct", decompose_source)
        self.assertNotIn('if "不同"', server_source)
        self.assertNotIn('if "两个"', server_source)
        self.assertNotIn("000", decompose_source)
        self.assertNotIn("111", decompose_source)
        self.assertNotIn("101", decompose_source)
        self.assertNotIn("010", decompose_source)


if __name__ == "__main__":
    unittest.main()
