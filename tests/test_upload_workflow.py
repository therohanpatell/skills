import io
import json
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dtf_test_gen.engine import Engine
from dtf_test_gen.loaders.ddl import load_ddl_text
from dtf_test_gen.loaders.dtf import load_dtf_text
from dtf_test_gen.loaders.discovery import LoadError
from dtf_test_gen.loaders.skills import Skill
from dtf_test_gen.models.schema import DDLSet, Table, Column
from dtf_test_gen.analysis.static import analyse_static
from dtf_test_gen.llm.prompt import build_prompt
from dtf_test_gen.sql.writer import render_all, literal
from dtf_test_gen.workflow import decode_upload, validate_inputs, validate_generation, run_fingerprint, download_bundle


class UploadWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.ddl = DDLSet(tables=load_ddl_text('{"table":"customers","columns":[{"name":"id","type":"INT64"},{"name":"status","type":"STRING"}]}', 'customers.json'))
        self.dtf = load_dtf_text('{"source":"customers","filters":["status = \'ACTIVE\'"]}', 'job.json')

    def test_all_sources_and_all_columns(self):
        self.ddl.tables.append(Table(name="extra", columns=[Column(name="id", data_type="INT64")]))
        analysis = analyse_static(self.dtf, self.ddl)
        result = Engine().generate(analysis, self.ddl, include_all_sources=True, include_all_columns=True)
        self.assertEqual({t.table for t in result.tables}, {"customers", "extra"})
        self.assertEqual(result.table("customers").columns, ["id", "status"])
        self.assertEqual(result.table("extra").row_count, 1)
        self.assertTrue(any("baseline" in w for w in result.warnings))
        self.assertFalse(validate_generation(result, self.ddl))
        sql = render_all(result, self.ddl)
        self.assertEqual(sql.count("INSERT INTO"), 2)
        with zipfile.ZipFile(io.BytesIO(download_bundle(sql, analysis, result, None))) as archive:
            self.assertEqual(set(archive.namelist()), {"inserts.sql", "coverage.json", "analysis.json", "run.json"})

    def test_passthrough_source_gets_baseline_not_fake_coverage(self):
        dtf = load_dtf_text('{"source":"customers","mappings":{"id":"id"}}', 'job.json')
        result = Engine().generate(analyse_static(dtf, self.ddl), self.ddl, include_all_sources=True, include_all_columns=True)
        self.assertEqual(result.table("customers").row_count, 1)
        self.assertEqual(result.coverage.total, 0)
        self.assertTrue(any("not verified" in w for w in result.warnings))

    def test_missing_duplicate_and_unsupported_schema(self):
        self.assertTrue(validate_inputs(self.dtf, DDLSet()))
        self.ddl.tables.append(self.ddl.tables[0])
        self.assertTrue(any("Multiple schemas" in e for e in validate_inputs(self.dtf, self.ddl)))
        self.ddl.tables = self.ddl.tables[:1]
        self.ddl.tables[0].columns[0].mode = "REPEATED"
        self.assertTrue(any("Unsupported column" in e for e in validate_inputs(self.dtf, self.ddl)))

    def test_uploaded_bom_and_invalid_encoding(self):
        self.assertEqual(decode_upload(b'\xef\xbb\xbf{}', 'job.json'), '{}')
        with self.assertRaises(LoadError):
            decode_upload(b'\xff', 'job.json')

    def test_no_silent_first_step_only(self):
        with self.assertRaises(LoadError):
            load_dtf_text('{"transformations":[{"source":"a"},{"source":"b"}]}', 'job.json')

    def test_full_knowledge_and_invalidation(self):
        skills = [Skill(name="rules.md", path=None, text="# Unusual operation\n" + "reference " * 500, selected=True)]
        analysis = analyse_static(self.dtf, self.ddl)
        bundle = build_prompt(self.dtf, self.ddl, analysis, skills, full_knowledge=True)
        self.assertEqual(bundle.payload["knowledge"]["rules.md"], skills[0].text)
        before = run_fingerprint(self.dtf, self.ddl, skills, {"max_rows": 5})
        self.assertNotEqual(before, run_fingerprint(self.dtf, self.ddl, skills, {"max_rows": 6}))
        skills[0].text += "changed"
        self.assertNotEqual(before, run_fingerprint(self.dtf, self.ddl, skills, {"max_rows": 5}))

    def test_structured_source_qualification_and_scalar_literals(self):
        dtf = load_dtf_text('{"source":{"project":"p","dataset":"d","table":"customers"}}', 'job.json')
        self.assertEqual(dtf.source_table_fq, {"customers": "p.d.customers"})
        self.assertEqual(literal("false", "BOOL"), "FALSE")
        self.assertEqual(literal('12345678901234567890.123456789', 'NUMERIC'), "NUMERIC '12345678901234567890.123456789'")
        self.assertEqual(literal('{"x":1}', 'JSON'), 'JSON \'{"x":1}\'')
        with self.assertRaises(ValueError):
            literal('1.2', 'INT64')

    def test_citation_keeps_operator_and_literal(self):
        from dtf_test_gen.analysis.merge import _citation_matches
        self.assertEqual(_citation_matches(" `amount` > 100 ", ["amount > 100"]), "amount > 100")
        self.assertIsNone(_citation_matches("amount < 100", ["amount > 100"]))
        self.assertIsNone(_citation_matches("amount", ["amount > 100"]))
        self.assertIsNone(_citation_matches("status = 'active'", ["status = 'ACTIVE'"]))

    def test_required_null_blocks_sql_and_group_row_cap(self):
        dtf = load_dtf_text('{"source":"customers","filters":["status IS NULL"]}', 'job.json')
        self.ddl.tables[0].columns[1].mode = "REQUIRED"
        result = Engine().generate(analyse_static(dtf, self.ddl), self.ddl, include_all_sources=True)
        self.assertTrue(any("NULL violates REQUIRED" in error for error in validate_generation(result, self.ddl)))
        dtf = load_dtf_text('{"source":"customers","group_by":["id"]}', 'job.json')
        result = Engine().generate(analyse_static(dtf, self.ddl), self.ddl, max_rows=1)
        self.assertTrue(all(t.row_count <= 1 for t in result.tables))
        self.assertTrue(result.coverage.missing)


if __name__ == '__main__':
    unittest.main()
