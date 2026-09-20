"""Run with python -S -m unittest discover -s tests -v (no packages)."""
import json
import sys
import tempfile
import tomllib
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dtf_test_gen.config import AppConfig
from dtf_test_gen.engine import Engine
from dtf_test_gen.llm.client import OllamaClient, OllamaError
from dtf_test_gen.llm.parse import parse_llm_json, LLMParseError
from dtf_test_gen.llm.prompt import build_prompt
from dtf_test_gen.loaders import load_ddl, load_dtf
from dtf_test_gen.loaders.discovery import parse_text, LoadError
from dtf_test_gen.loaders.skills import load_skills, auto_select
from dtf_test_gen.models.analysis import AnalysisResult, ColumnRole
from dtf_test_gen.analysis.static import analyse_static
from dtf_test_gen.sql.writer import render_all

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples/simple_customer'

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = AppConfig()
        self.config.cache.directory = self.temp.name
        self.ddl, errors = load_ddl(list((EXAMPLE / 'ddl').glob('*.json')))
        self.assertFalse(errors)
        self.dtf = load_dtf(EXAMPLE / 'dtf/customer_transform.json')

    def test_full_json_workflow_and_cache_roundtrip(self):
        engine = Engine(self.config)
        first = engine.analyse(self.dtf, self.ddl, [], 'test', use_llm=False)
        cached = engine.analyse(self.dtf, self.ddl, [], 'test', use_llm=False)
        self.assertTrue(cached.from_cache)
        self.assertEqual(first.analysis.model_dump(), cached.analysis.model_dump())
        self.assertIsInstance(cached.analysis.required_columns[0].role, ColumnRole)
        result = engine.generate(cached.analysis, self.ddl)
        self.assertEqual(result.coverage.total, 8)
        self.assertEqual(result.coverage.missing, [])
        sql = render_all(result, self.ddl)
        self.assertIn('INSERT INTO `analytics_demo.raw.customer`', sql)
        self.assertIn('`customer_id`', sql)
        self.assertIn("'NOT_ACTIVE'", sql)
        path = engine.write_output(cached.analysis, result, self.ddl, self.dtf.name, self.temp.name)
        self.assertEqual(json.loads((path/'coverage.json').read_text())['covered'], 8)

    def test_static_cache_does_not_skip_model(self):
        engine = Engine(self.config)
        engine.analyse(self.dtf, self.ddl, [], 'test', use_llm=False)
        with patch.object(OllamaClient, 'generate', return_value='{"notes": []}') as call:
            outcome = engine.analyse(self.dtf, self.ddl, [], 'test')
        self.assertTrue(outcome.llm_called)
        call.assert_called_once()

    def test_failure_does_not_poison_cache(self):
        engine = Engine(self.config)
        with patch.object(OllamaClient, 'generate', side_effect=OllamaError('offline')):
            self.assertIsNotNone(engine.analyse(self.dtf, self.ddl, [], 'test').llm_error)
        with patch.object(OllamaClient, 'generate', return_value='{}') as call:
            engine.analyse(self.dtf, self.ddl, [], 'test')
        call.assert_called_once()

    def test_knowledge_and_original_json_reach_prompt(self):
        skills = auto_select(load_skills(list((EXAMPLE/'skills').glob('*.md'))), self.dtf)
        bundle = build_prompt(self.dtf, self.ddl, analyse_static(self.dtf, self.ddl), skills)
        self.assertTrue(bundle.payload['knowledge'])
        self.assertEqual(bundle.payload['raw_config'], self.dtf.raw)
        self.assertIn('source_expression', bundle.system)

    def test_model_validation(self):
        result = parse_llm_json('{"transformations":[{"kind":" FILTER ","table":"`customer`","operator":" EQ "}]}')
        self.assertEqual(result.transformations[0].table, 'customer')
        self.assertEqual(result.transformations[0].operator, 'eq')
        for raw in ('{"transformations": "wrong"}', '{"transformations": [3]}', '{"notes": [42]}'):
            with self.subTest(raw=raw), self.assertRaises(LLMParseError):
                parse_llm_json(raw)
        with self.assertRaises(ValueError):
            AnalysisResult.model_validate({'required_columns': [{'table': 'x', 'column': 'y', 'role': 'BAD'}]})

    def test_json_config_roundtrip(self):
        path = Path(self.temp.name)/'config.json'
        self.config.ollama.model = 'local-model'
        self.config.save(path)
        self.assertEqual(AppConfig.load(path).ollama.model, 'local-model')
        with self.assertRaises(LoadError):
            parse_text('{broken', 'dtf.json')

    def test_no_build_or_package_dependencies(self):
        metadata = tomllib.loads((ROOT / 'pyproject.toml').read_text())
        self.assertNotIn('build-system', metadata)
        self.assertEqual(metadata['project']['dependencies'], [])
        self.assertNotIn('optional-dependencies', metadata['project'])
        self.assertNotIn('setuptools', metadata.get('tool', {}))

    def test_yaml_unavailable_has_actionable_error(self):
        with patch.dict(sys.modules, {'yaml': None}):
            with self.assertRaises(LoadError) as caught:
                parse_text('name: example', 'dtf.yaml')
        self.assertIn('Use JSON instead', caught.exception.reason)

    def test_missing_dtf_is_not_silently_replaced(self):
        from dtf_test_gen.cli import main
        with patch('builtins.print'):
            self.assertEqual(main([str(EXAMPLE), '--dtf', 'missing.json', '--no-llm']), 1)

class HTTPTests(unittest.TestCase):
    def test_ollama_protocol_and_bad_response(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200); self.end_headers()
                self.wfile.write(b'{"models":[{"name":"local-test"}]}')
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(body)
                self.send_response(200); self.end_headers()
                self.wfile.write(b'{"response":"{}"}' if body['model'] != 'bad' else b'not json')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = OllamaClient(f'http://127.0.0.1:{server.server_port}')
            self.assertEqual(client.status().models, ['local-test'])
            self.assertEqual(client.generate('local-test', 'knowledge', system='rules'), '{}')
            self.assertEqual(requests[0]['format'], 'json')
            self.assertFalse(requests[0]['stream'])
            self.assertEqual(requests[0]['system'], 'rules')
            with self.assertRaises(OllamaError):
                client.generate('bad', 'test')
        finally:
            server.shutdown(); server.server_close(); thread.join()

if __name__ == '__main__':
    unittest.main()
