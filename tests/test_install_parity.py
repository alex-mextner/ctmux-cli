"""The macOS install path of the new install.sh + install.py must behave like
the original shell installer, replayed from tests/golden/install_trace.json
(recorded from `git show HEAD:install.sh` by tests/golden/generate_install.py).

Per scenario: same exit codes, same launchctl calls, same HOME tree except the
new agent-skill files (~/.agents/**), and every line the old script printed
appears in the new output in the same order (extra lines — notes, skill — are
allowed).

Run: python3 -m unittest discover -s tests -p 'test_install_parity.py' -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from typing import Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "golden"))

import generate_install as gen  # noqa: E402

with open(gen.TRACE_PATH, encoding="utf-8") as _fh:
    TRACE = json.load(_fh)


def build_new(src: str) -> None:
    gen.build_checkout(
        src,
        {
            "install.sh": os.path.join(gen.ROOT, "install.sh"),
            "install.py": os.path.join(gen.ROOT, "install.py"),
            "skill": os.path.join(gen.ROOT, "skill"),
        },
    )


def without_agents(tree: List[Dict[str, str]]) -> List[Dict[str, str]]:
    return [e for e in tree if e["path"] != ".agents" and not e["path"].startswith(".agents/")]


def is_subsequence(old: List[str], new: List[str]) -> bool:
    it = iter(new)
    return all(line in it for line in old)


class InstallParityTest(unittest.TestCase):
    def test_trace_covers_every_scenario(self) -> None:
        self.assertEqual(sorted(TRACE["scenarios"]), sorted(gen.SCENARIOS))


def _make_test(name: str):
    def test(self: InstallParityTest) -> None:
        want = TRACE["scenarios"][name]
        got = gen.run_scenario(gen.SCENARIOS[name], build_new)
        self.assertEqual(len(got["runs"]), len(want["runs"]))
        for i, (g, w) in enumerate(zip(got["runs"], want["runs"])):
            self.assertEqual(g["rc"], w["rc"], "run %d exit code" % i)
            self.assertEqual(g["calls"], w["calls"], "run %d launchctl calls" % i)
            self.assertTrue(
                is_subsequence(w["stdout"], g["stdout"]),
                "run %d: old stdout lines missing/out of order.\nold: %s\nnew: %s" % (i, w["stdout"], g["stdout"]),
            )
        self.assertEqual(without_agents(got["tree"]), without_agents(want["tree"]), "HOME tree")
        self.assertEqual(got["bin_ctmux_executable"], want["bin_ctmux_executable"], "bin/ctmux exec bit")

    test.__name__ = "test_" + name
    return test


for _name in gen.SCENARIOS:
    setattr(InstallParityTest, "test_" + _name, _make_test(_name))


if __name__ == "__main__":
    unittest.main()
