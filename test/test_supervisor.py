"""Decision parsing and response publication, without network calls."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('supervisor', Path(__file__).resolve().parents[1] / 'agent/supervisor.py')
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


class InvestigationTests(unittest.TestCase):
    def answer(self, payload):
        with patch.object(supervisor, 'chat', return_value=payload):
            return supervisor.investigate({'kind': 'incident'})

    def test_repair_json_fence_and_smalltalk_fence(self):
        data = {'decision': 'repair', 'reason': 'Contract violated.',
                'source': "```smalltalk\nVendingMachine compile: 'dispense ^ 1'\n```"}
        answer = self.answer('```json\n' + json.dumps(data) + '\n```')
        self.assertEqual(answer['source'], "VendingMachine compile: 'dispense ^ 1'")

    def test_declined_investigation_discards_any_source(self):
        for decision in ('expected', 'insufficient-evidence'):
            with self.subTest(decision=decision):
                result = self.answer(json.dumps({'decision': decision, 'reason': 'No change justified.', 'source': 'bad code'}))
                self.assertNotIn('source', result)

    def test_invalid_decisions_and_missing_evidence_fail(self):
        cases = [[], {'decision': 'deploy', 'reason': 'Bad.'},
                 {'decision': 'repair', 'reason': 'Fix.'},
                 {'decision': 'repair', 'reason': 'Fix.', 'source': ''},
                 {'decision': 'expected', 'reason': ''}]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.answer(json.dumps(case))

    def test_incident_response_includes_decision_and_model(self):
        with tempfile.TemporaryDirectory() as directory:
            req = Path(directory) / 'req-9.json'
            req.write_text(json.dumps({'id': 9, 'kind': 'incident'}))
            with patch.object(supervisor, 'HEAL_DIR', directory), patch.object(supervisor, 'investigate', return_value={'decision': 'expected', 'reason': 'Normal rejection.'}):
                supervisor.process(str(req))
            response = json.loads((Path(directory) / 'resp-9.json').read_text())
            self.assertEqual(response['decision'], 'expected')
            self.assertEqual(response['id'], 9)
            self.assertEqual(response['model'], supervisor.LLM_MODEL)
            self.assertFalse((Path(directory) / 'resp-9.json.tmp').exists())

    def test_manual_heal_keeps_source_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            req = Path(directory) / 'req-10.json'
            req.write_text(json.dumps({'id': 10}))
            with patch.object(supervisor, 'HEAL_DIR', directory), patch.object(supervisor, 'call_llm', return_value='nil'):
                supervisor.process(str(req))
            response = json.loads((Path(directory) / 'resp-10.json').read_text())
            self.assertEqual(response['source'], 'nil')
            self.assertNotIn('decision', response)

    def test_failed_investigation_publishes_error_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            req = Path(directory) / 'req-11.json'
            req.write_text(json.dumps({'id': 11, 'kind': 'incident'}))
            with patch.object(supervisor, 'HEAL_DIR', directory), patch.object(supervisor, 'investigate', side_effect=ValueError('Invalid response')), patch.object(supervisor.time, 'sleep'):
                supervisor.process(str(req))
            response = json.loads((Path(directory) / 'resp-11.json').read_text())
            self.assertEqual(response['error'], 'Invalid response')
            self.assertNotIn('source', response)


if __name__ == '__main__':
    unittest.main()
