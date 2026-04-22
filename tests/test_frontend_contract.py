import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
    def test_use_project_state_polls_actively_while_starting(self) -> None:
        content = (REPO_ROOT / "frontend" / "src" / "lib" / "useProjectState.ts").read_text(encoding="utf-8")
        self.assertRegex(content, r'status === "starting"\s*\|\|\s*status === "running"')

    def test_project_phase_aliases_cover_start_flow_stages(self) -> None:
        content = (REPO_ROOT / "frontend" / "src" / "lib" / "projectPhase.ts").read_text(encoding="utf-8")
        self.assertRegex(content, r'check_config:\s*"checking_inputs"')
        self.assertRegex(content, r'launch_runtime:\s*"starting_runtime"')
        self.assertRegex(content, r'confirm_output:\s*"confirm_output"')
        self.assertRegex(content, r'id:\s*"checking_inputs"')
