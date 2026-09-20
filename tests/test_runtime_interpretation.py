import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dtf_test_gen.engine import Engine
from dtf_test_gen.loaders.dtf import load_dtf_text
from dtf_test_gen.loaders.discovery import LoadError
from dtf_test_gen.loaders.skills import Skill
from dtf_test_gen.models.schema import DDLSet, Table, Column
from dtf_test_gen.llm.client import OllamaClient, OllamaError
from dtf_test_gen.llm.interpret import InterpretationError
from dtf_test_gen.workflow import download_bundle
from dtf_test_gen.sql.writer import render_all

RAW = {"stages": [{"readFrom": "customers"}, {"customKeep": {"attribute": "status", "equals": "ACTIVE"}}]}
RESPONSE = {
    "dtf": {"name": "custom_job", "source_tables": ["customers"],
            "predicates": [{"expression": "customers.status = 'ACTIVE'", "origin": "filter"}]},
    "evidence": [{"field": "source_tables/0", "pointer": "/stages/0/readFrom"},
                 {"field": "predicates/0", "pointer": "/stages/1/customKeep"}],
    "unresolved": [], "notes": ["customKeep translated using supplied framework documentation."],
}


class RuntimeInterpretationTests(unittest.TestCase):
    def setUp(self):
        self.dtf = load_dtf_text(json.dumps(RAW), "private.json", allow_runtime=True)
        self.ddl = DDLSet(tables=[Table(name="customers", columns=[Column(name="status")])])
        self.skills = [Skill(name="framework.md", path=None,
                             text="customKeep keeps matching rows.\n" + "framework " * 500 + "END_OF_KNOWLEDGE", selected=True)]

    def test_unknown_json_reaches_model_with_full_knowledge_then_generates(self):
        self.assertTrue(self.dtf.needs_interpretation)
        with patch.object(OllamaClient, "generate", return_value=json.dumps(RESPONSE)) as call:
            outcome = Engine().analyse(self.dtf, self.ddl, self.skills, "local-test")
        call.assert_called_once()
        sent = json.loads(call.call_args.kwargs["prompt"])
        self.assertEqual(sent["raw_dtf"], RAW)
        self.assertIn("END_OF_KNOWLEDGE", next(iter(sent["knowledge"].values())))
        self.assertTrue(sent["schemas"]["tables"])
        self.assertEqual(outcome.analysis.source, "llm-interpreted")
        result = Engine().generate(outcome.analysis, self.ddl, include_all_sources=True, include_all_columns=True)
        self.assertEqual(result.coverage.total, 2)
        self.assertFalse(result.coverage.missing)
        sql = render_all(result, self.ddl)
        self.assertIn("'ACTIVE'", sql)
        self.assertIn("'NOT_ACTIVE'", sql)
        with zipfile.ZipFile(io.BytesIO(download_bundle(sql, outcome.analysis, result, outcome))) as archive:
            self.assertEqual(json.loads(archive.read("interpretation.json")), RESPONSE)

    def test_custom_multistep_and_list_layout_are_accepted_for_runtime(self):
        for raw in ({"transformations": [{"source": "customers"}, {"customKeep": "ACTIVE"}]}, RAW["stages"]):
            self.assertTrue(load_dtf_text(json.dumps(raw), "private.json", allow_runtime=True).needs_interpretation)

    def test_malformed_json_is_still_rejected(self):
        with self.assertRaises(LoadError):
            load_dtf_text('{bad', "private.json", allow_runtime=True)

    def test_unknown_layout_without_model_does_not_generate_baseline(self):
        with patch.object(OllamaClient, "generate") as call, self.assertRaisesRegex(InterpretationError, "Enable Use Ollama"):
            Engine().analyse(self.dtf, self.ddl, self.skills, "local-test", use_llm=False)
        call.assert_not_called()

    def test_model_failure_has_no_static_fallback(self):
        with patch.object(OllamaClient, "generate", side_effect=OllamaError("offline")), self.assertRaisesRegex(InterpretationError, "No static fallback"):
            Engine().analyse(self.dtf, self.ddl, self.skills, "local-test")

    def test_bad_evidence_unknown_column_unresolved_and_empty_are_rejected(self):
        cases = []
        for kind in ("evidence", "column", "unresolved", "empty"):
            payload = json.loads(json.dumps(RESPONSE))
            if kind == "evidence":
                payload["evidence"][1]["pointer"] = "/stages/999"
            elif kind == "column":
                payload["dtf"]["predicates"][0]["expression"] = "customers.nonexistent = 'ACTIVE'"
            elif kind == "unresolved":
                payload["unresolved"] = ["Cannot interpret /stages/1"]
            else:
                payload["dtf"]["predicates"] = []
            cases.append(payload)
        for payload in cases:
            with self.subTest(payload=payload), patch.object(OllamaClient, "generate", return_value=json.dumps(payload)) as call:
                with self.assertRaises(InterpretationError):
                    Engine().analyse(self.dtf, self.ddl, self.skills, "local-test")
                self.assertEqual(call.call_count, 2)

    def test_interpret_even_partially_recognized_json_when_requested(self):
        dtf = load_dtf_text(json.dumps({"source": "customers", **RAW}), "private.json", allow_runtime=True)
        self.assertFalse(dtf.needs_interpretation)
        with patch.object(OllamaClient, "generate", return_value=json.dumps(RESPONSE)) as call:
            outcome = Engine().analyse(dtf, self.ddl, self.skills, "local-test", runtime_interpretation=True)
        call.assert_called_once()
        self.assertEqual(len(outcome.analysis.all_paths), 2)


if __name__ == "__main__":
    unittest.main()
