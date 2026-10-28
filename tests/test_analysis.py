import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import analysis

ROOT = Path(__file__).resolve().parents[1]

class AnalysisTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / 'report.md'
        self.args = SimpleNamespace(retention=str(ROOT/'fixtures/retention.csv'), contract=str(ROOT/'fixtures/contract.json'), evaluation=str(ROOT/'fixtures/evaluation.csv'), mapping=str(ROOT/'fixtures/mapping.csv'), groups=str(ROOT/'fixtures/groups.csv'), start='2026-01-01', end='2026-01-02', output=str(self.out))

    def test_weighted_and_join(self):
        analysis.run(self.args)
        report = self.out.read_text()
        for text in ('0.2667 (8 / 30)', '2.8000 (14 / 5)', '0.1800 (0.9 / 5)', '연결 누락', '1/2개'):
            self.assertIn(text, report)

    def test_missing_and_unknown_observation(self):
        data = Path(self.args.retention).read_text().replace(',20,20,15,12,11,10,9,8,6', ',20,20,15,12,11,10,9,8')
        path = Path(self.tmp.name)/'retention.csv'; path.write_text(data)
        config = json.loads(Path(self.args.contract).read_text()); config['timezone'] = 'unknown'
        contract = Path(self.tmp.name)/'contract.json'; contract.write_text(json.dumps(config))
        self.args.retention = str(path); self.args.contract = str(contract); self.args.groups = None
        analysis.run(self.args)
        report = self.out.read_text()
        self.assertIn('1/2', report); self.assertIn('0.2000 (2 / 10)', report); self.assertIn('확정 D7 아님', report)

    def test_zero_and_invalid(self):
        self.assertIn('없음', analysis.ratio(0, 0))
        p = Path(self.tmp.name)/'bad.csv'; p.write_text('unknown\n')
        with self.assertRaises(ValueError): analysis.retention(p, {})
        p.write_text(Path(self.args.retention).read_text().replace(',8,6\n', ',8,21\n'))
        with self.assertRaises(ValueError): analysis.retention(p, json.loads(Path(self.args.contract).read_text()))

    def test_overlap_and_ambiguous_mapping(self):
        p = Path(self.tmp.name)/'bad.csv'; original = Path(self.args.evaluation).read_text(); p.write_text(original + original.splitlines()[1]+'\n')
        with self.assertRaises(ValueError): analysis.evaluation(p)
        original = Path(self.args.mapping).read_text(); p.write_text(original + original.splitlines()[1]+'\n')
        with self.assertRaises(ValueError): analysis.mappings(p)

    def test_output_cannot_overwrite_input(self):
        self.args.output = self.args.retention
        with self.assertRaises(ValueError): analysis.run(self.args)

if __name__ == '__main__': unittest.main()
