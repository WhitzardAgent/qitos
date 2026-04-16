import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cli
from submit_tool import SubmitPoCTool


class CliAndSubmitToolTests(unittest.TestCase):
    def test_infer_family_id_maps_glm_to_openai(self):
        self.assertEqual(cli.infer_family_id("GLM-5.1-sii"), "openai")
        self.assertEqual(cli.infer_family_id("zai-org/GLM-5.1-FP8"), "openai")
        self.assertIsNone(cli.infer_family_id("qwen-plus"))

    def test_submit_tool_maps_public_submit_response_to_safe_partial_verification(self):
        tool = SubmitPoCTool("http://server")
        with tempfile.TemporaryDirectory() as tmpdir:
            poc = Path(tmpdir) / "poc.bin"
            poc.write_bytes(b"poc")
            response = mock.Mock()
            response.status_code = 200
            response.json.return_value = {"exit_code": 77, "output": "boom", "poc_id": "p1"}
            with mock.patch("httpx.post", return_value=response):
                with mock.patch.dict("os.environ", {}, clear=True):
                    result = tool.execute(
                        {
                            "poc_path": str(poc),
                            "task_id": "arvo:1065",
                            "agent_id": "agent",
                            "checksum": "sum",
                        }
                    )

        self.assertEqual(result["vul_exit_code"], 77)
        self.assertEqual(result["fix_exit_code"], 77)
        self.assertEqual(result["poc_id"], "p1")
        self.assertEqual(result["raw_output"], "boom")
        self.assertEqual(result["verification_scope"], "vul_only")

    def test_submit_tool_runs_private_verify_when_api_key_present(self):
        tool = SubmitPoCTool("http://server")
        with tempfile.TemporaryDirectory() as tmpdir:
            poc = Path(tmpdir) / "poc.bin"
            poc.write_bytes(b"poc")
            submit_response = mock.Mock(status_code=200)
            submit_response.json.return_value = {
                "exit_code": 77,
                "output": "boom",
                "poc_id": "p1",
            }
            verify_response = mock.Mock(status_code=200)
            verify_response.json.return_value = {"poc_ids": ["p1"]}
            query_response = mock.Mock(status_code=200)
            query_response.json.return_value = [
                {"poc_id": "p1", "vul_exit_code": 77, "fix_exit_code": 0}
            ]
            with mock.patch(
                "httpx.post",
                side_effect=[submit_response, verify_response, query_response],
            ) as post:
                with mock.patch.dict("os.environ", {"CYBERGYM_API_KEY": "secret"}, clear=True):
                    result = tool.execute(
                        {
                            "poc_path": str(poc),
                            "task_id": "arvo:1065",
                            "agent_id": "agent",
                            "checksum": "sum",
                        }
                    )

        self.assertEqual(result["vul_exit_code"], 77)
        self.assertEqual(result["fix_exit_code"], 0)
        self.assertEqual(result["verification_scope"], "full")
        self.assertEqual(post.call_args_list[1].kwargs["headers"], {"X-API-Key": "secret"})


if __name__ == "__main__":
    unittest.main()
