"""Behavior checks: false accounting, changed sources and duplicate streams fail."""
import copy
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('cost_case_audit', Path(__file__).with_name('audit.py'))
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads((audit.HERE / 'usage-snapshot.json').read_text())
        self.root = Path(os.environ['COST_CASE_BASE'])
        self.expected = audit.extract(self.root, self.snapshot)

    def test_reconciles_one_result_without_competitor_task(self):
        self.assertEqual(self.expected['included_streams'], 7)
        self.assertEqual(self.expected['api_equivalent_usd'], '3.0331')
        self.assertEqual(self.expected['excluded_api_equivalent_usd'], '0.2020')
        self.assertEqual(self.expected['calendar']['seconds'], 7643)
        self.assertIsNone(self.expected['active_seconds'])
        self.assertEqual(audit.validate(self.expected, self.expected), [])

    def test_contaminated_total_rejected(self):
        candidate = copy.deepcopy(self.expected)
        candidate['api_equivalent_usd'] = candidate['window_api_equivalent_usd']
        self.assertTrue(audit.validate(candidate, self.expected))

    def test_six_negative_controls_rejected(self):
        self.assertTrue(all(x['rejected'] for x in audit.controls(self.expected).values()))

    def test_duplicate_stream_rejected(self):
        self.snapshot['records'].append(self.snapshot['records'][0])
        with self.assertRaisesRegex(ValueError, 'duplicate stream'):
            audit.extract(self.root, self.snapshot)

    def test_broken_memo_link_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'NOTE.md'
            self.assertTrue(audit.broken_links('[source](missing.md)', path))
            (path.parent / 'source.md').write_text('source')
            self.assertEqual(audit.broken_links('[source](source.md)', path), [])

    def test_modified_source_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in self.snapshot['sources']:
                dest = root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes((self.root / rel).read_bytes())
            (root / 'projects/ryadom/state/cost_report.md').write_text('Итого $0')
            with self.assertRaisesRegex(ValueError, 'source hash mismatch'):
                audit.extract(root, self.snapshot)


if __name__ == '__main__':
    unittest.main()
