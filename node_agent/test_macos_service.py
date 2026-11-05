import unittest
from pathlib import Path

from node_agent.macos_service import LABEL, build_launch_agent_plist


class MacServiceTests(unittest.TestCase):
    def test_launch_agent_runs_persisted_virtualenv_at_login(self):
        plist = build_launch_agent_plist(
            "https://provisioning.example",
            "/Users/test/Library/Application Support/DistributedCompute/NodeAgent/venv/bin/python",
            "/Users/test/Library/Application Support/DistributedCompute/NodeAgent",
            "/Users/test/Library/Logs/DistributedCompute",
            60,
        )
        self.assertEqual(plist["Label"], LABEL)
        self.assertTrue(plist["RunAtLoad"])
        self.assertTrue(plist["KeepAlive"])
        self.assertEqual(plist["ProgramArguments"][2], "node_agent")
        self.assertEqual(plist["ProgramArguments"][4], "https://provisioning.example")
        self.assertEqual(
            plist["StandardOutPath"],
            str(Path("/Users/test/Library/Logs/DistributedCompute/node-agent.log")),
        )


if __name__ == "__main__":
    unittest.main()