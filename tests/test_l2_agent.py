import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from starter_kit import adapter
from starter_kit.agent.backends import capability_basis
from starter_kit.agent.server import UI_PATH, handle_build
from starter_kit.evaluator import evaluate_l2


GHZ_QASM = '''OPENQASM 2.0;
include "qelib1.inc";
qreg q[3];
creg c[3];
h q[0];
cx q[0], q[1];
cx q[1], q[2];
measure q -> c;'''


def plan(**updates):
    value = {
        "task": "qasm",
        "target_state": "ghz",
        "num_qubits": 3,
        "qasm": GHZ_QASM,
        "answer": None,
        "constraints": {},
    }
    value.update(updates)
    return json.dumps(value, ensure_ascii=False)


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
        ModelHandler.responses = [plan()]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = handle_build("让三个量子比特纠缠在一起")

        self.assertTrue(result["ok"])
        self.assertEqual(result["circuit"]["qubit_count"], 3)
        self.assertEqual(result["circuit"]["operations"][0]["name"], "h")
        self.assertEqual(result["circuit"]["measurements"][-1], {"qubit": 2, "cbit": 2})

    def test_ui_concept_question_returns_plain_language_answer(self):
        ModelHandler.responses = [
            plan(
                task="explain",
                target_state="custom",
                num_qubits=None,
                qasm=None,
                answer="纠缠是多个量子比特形成一个不可分割的整体。",
            )
        ]
        with mock.patch.dict(os.environ, self.environment, clear=True):
            result = handle_build("什么是量子纠缠？")

        self.assertEqual(result["kind"], "explanation")
        self.assertTrue(result["ok"])
        self.assertIn("纠缠", result["explain"])
        self.assertEqual(len(ModelHandler.payloads), 1)

    def test_ui_without_model_config_does_not_fake_an_agent_answer(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            result = handle_build("什么是量子纠缠？")

        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "error")
        self.assertIn("LOOMQ_LLM_API_KEY", result["message"])
        self.assertEqual(ModelHandler.payloads, [])


if __name__ == "__main__":
    unittest.main()
