import unittest

from repomedic.agent_graph import AgentRunResult
from repomedic.web_ui import render_control_panel


class WebUiTests(unittest.TestCase):
    def test_approval_page_escapes_untrusted_content(self) -> None:
        result = AgentRunResult(
            case_id="case_001",
            run_id="run_001",
            run_dir="C:/runs/run_001",
            status="awaiting_approval",
            awaiting_approval=True,
            proposal={"summary": "<script>alert(1)</script>", "edits": []},
            proposal_diff="+ <unsafe>",
            error=None,
        )

        page = render_control_panel(result, csrf_token="token")

        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn('value="approve"', page)
        self.assertIn('value="reject"', page)

    def test_completed_page_has_no_decision_form(self) -> None:
        result = AgentRunResult(
            case_id="case_001",
            run_id="run_001",
            run_dir="C:/runs/run_001",
            status="verified",
            awaiting_approval=False,
            proposal=None,
            proposal_diff=None,
            error=None,
        )

        page = render_control_panel(result, csrf_token="token")

        self.assertIn("verified", page)
        self.assertNotIn("Approve and continue", page)


if __name__ == "__main__":
    unittest.main()
