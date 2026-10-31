"""unittest entry point:  python3 -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from datasets import DEV, HELDOUT_A  # noqa: E402
from scenarios import ROBUSTNESS, SECURITY, TASK, TWO_AGENT, VOICE_LANGUAGE  # noqa: E402

from humain import nlu  # noqa: E402


class Regression(unittest.TestCase):
    """Regression gate: these must stay green when prompts, policies or tools change."""

    def test_intents_dev_and_heldout_a(self):
        for u, exp in DEV + HELDOUT_A:
            with self.subTest(u=u):
                self.assertEqual(nlu.classify(u).intent, exp)


def _make(name, fn):
    def test(self):
        ok, ev = fn()
        self.assertTrue(ok, f"{name}: {ev}")
    return test


for suite_name, cases in (("task", TASK), ("robust", ROBUSTNESS), ("security", SECURITY), ("voice", VOICE_LANGUAGE), ("twoagent", TWO_AGENT)):
    for i, (name, fn) in enumerate(cases.items()):
        setattr(Regression, f"test_{suite_name}_{i:02d}", _make(name, fn))

if __name__ == "__main__":
    unittest.main()
