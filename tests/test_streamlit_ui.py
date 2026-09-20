"""Optional UI tests; skipped by python -S when Streamlit is unavailable."""
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from streamlit.testing.v1 import AppTest
except ImportError:
    AppTest = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dtf_test_gen.config import AppConfig
from dtf_test_gen.llm.client import OllamaClient, OllamaStatus, OllamaError


class Upload:
    def __init__(self, path):
        self.name = path.name
        self.data = path.read_bytes()

    def getvalue(self):
        return self.data


@unittest.skipIf(AppTest is None, "Streamlit is optional")
class StreamlitTests(unittest.TestCase):
    def test_runtime_custom_upload_reads_knowledge_before_framework_parse(self):
        raw = {"customSteps": [{"read": "customers"}, {"keep": "status = 'ACTIVE'"}]}
        response = {
            "dtf": {"name": "custom", "source_tables": ["customers"],
                    "predicates": [{"expression": "customers.status = 'ACTIVE'", "origin": "filter"}]},
            "evidence": [{"field": "source_tables/0", "pointer": "/customSteps/0/read"},
                         {"field": "predicates/0", "pointer": "/customSteps/1/keep"}],
            "unresolved": [], "notes": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "custom.json").write_text(json.dumps(raw))
            (folder / "customers.json").write_text('[{"name":"status","type":"STRING"}]')
            (folder / "framework.md").write_text("# Framework\ncustomSteps runs sequentially; read loads the source and keep filters rows.")
            dtf, schema = Upload(folder / "custom.json"), Upload(folder / "customers.json")

            def uploader(label, **kwargs):
                return [schema] if label == "Source DDL JSON files" else dtf if label == "DTF JSON configuration" else []

            config = AppConfig()
            config.ollama.model = "local-test"
            config.skills_directory = str(folder)
            config.cache.directory = str(folder / "cache")
            with patch.object(OllamaClient, "status", return_value=OllamaStatus(True, "localhost", ["local-test"])), patch("streamlit.file_uploader", side_effect=uploader), patch.object(OllamaClient, "generate", return_value=json.dumps(response)) as call:
                app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20)
                app.session_state["config"] = config
                app.run()
                self.assertFalse(app.exception)
                self.assertFalse(app.error)
                call.assert_not_called()
                button = next(b for b in app.button if b.label == "Analyze and generate INSERTs")
                self.assertFalse(button.disabled)
                button.click().run()
                self.assertFalse(app.exception)
                self.assertFalse(app.error)
                call.assert_called_once()
                prompt = json.loads(call.call_args.kwargs["prompt"])
                self.assertEqual(prompt["raw_dtf"], raw)
                self.assertIn("keep filters rows", next(iter(prompt["knowledge"].values())))
                self.assertEqual(app.session_state["analysis"].source, "llm-interpreted")
                self.assertTrue(app.code)
                call.side_effect = OllamaError("test runtime failure")
                next(b for b in app.button if b.label == "Analyze and generate INSERTs").click().run()
                self.assertFalse(app.exception)
                self.assertTrue(any("Runtime interpretation failed" in error.value for error in app.error))
                self.assertIsNone(app.session_state["analysis"])
                self.assertFalse(app.code)

    def test_upload_generate_invalidate_and_missing_source(self):
        schemas = [Upload(p) for p in (ROOT / "examples/simple_customer/ddl").glob("*.json")]
        dtf = Upload(ROOT / "examples/simple_customer/dtf/customer_transform.json")

        def uploader(label, **kwargs):
            return schemas if label == "Source DDL JSON files" else dtf if label == "DTF JSON configuration" else []

        with tempfile.TemporaryDirectory() as folder:
            config = AppConfig()
            config.cache.directory = folder
            with patch.object(OllamaClient, "status", return_value=OllamaStatus(False, "localhost", error="Test offline")), patch("streamlit.file_uploader", side_effect=uploader):
                app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20)
                app.session_state["config"] = config
                app.run()
                self.assertFalse(app.exception)
                next(b for b in app.button if b.label == "Analyze and generate INSERTs").click().run()
                self.assertFalse(app.exception)
                self.assertFalse(app.error)
                result = app.session_state["generation"]
                self.assertEqual({t.table for t in result.tables}, {"customer", "order", "address"})
                self.assertEqual(result.coverage.total, 8)
                self.assertEqual(result.coverage.missing, [])
                self.assertTrue(app.code)
                next(c for c in app.checkbox if c.label == "Include all source columns").uncheck().run()
                self.assertIsNone(app.session_state["analysis"])
                self.assertFalse(app.code)
                schemas[:] = [s for s in schemas if s.name != "order.json"]
                app.run()
                self.assertFalse(app.exception)
                self.assertTrue(next(b for b in app.button if b.label == "Analyze and generate INSERTs").disabled)
                self.assertTrue(any("Missing source DDL" in e.value for e in app.error))

    def test_native_upload_controls_exist(self):
        with patch.object(OllamaClient, "status", return_value=OllamaStatus(False, "localhost", error="Test offline")):
            app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.get("file_uploader")), 3)
            self.assertEqual(app.radio[0].value, "Upload files")


if __name__ == "__main__":
    unittest.main()
