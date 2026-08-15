import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "backlink-hunter" / "scripts" / "backlink_hunter.py"
DOMAIN = "target.example"


class BacklinkHunterCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def run_cli(self, *arguments, succeeds=True):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, arguments)],
            text=True,
            capture_output=True,
            check=False,
        )
        if succeeds and result.returncode != 0:
            self.fail(f"CLI failed ({result.returncode}):\nstdout={result.stdout}\nstderr={result.stderr}")
        if not succeeds and result.returncode == 0:
            self.fail(f"CLI unexpectedly succeeded:\nstdout={result.stdout}")
        stream = result.stdout if result.returncode == 0 else result.stderr
        return result, json.loads(stream)

    def state_args(self):
        return ("--workspace", self.workspace, "--domain", DOMAIN)

    def initialize(self, email_mode):
        _, payload = self.run_cli(
            "init",
            *self.state_args(),
            "--email-mode",
            email_mode,
            "--crawl-mode",
            "auto",
            "--run-mode",
            "goal",
        )
        self.assertTrue(payload["ok"])

    def add_opportunity(self, number=1, risk=1, status="qualified", target_url=None, succeeds=True):
        _, payload = self.run_cli(
            "opportunity-add",
            *self.state_args(),
            "--opportunity-domain",
            f"source{number}.example.org",
            "--source-url",
            f"https://source{number}.example.org/resources",
            "--target-url",
            target_url or f"https://{DOMAIN}/guide-{number}",
            "--category",
            "resource-page",
            "--status",
            status,
            "--relevance",
            "5",
            "--quality",
            "4",
            "--audience-value",
            "3",
            "--viability",
            "4",
            "--target-fit",
            "5",
            "--cost-efficiency",
            "5",
            "--difficulty",
            "2",
            "--risk",
            str(risk),
            "--cost",
            "free",
            "--evidence",
            "Relevant maintained resource page with comparable editorial links.",
            succeeds=succeeds,
        )
        return payload["opportunity"]["id"] if succeeds else payload

    def propose(self, opportunity_id, number=1, kind="initial"):
        body_file = self.workspace / f"message-{number}-{kind}.txt"
        body_file.write_text(
            "Hello,\n\n"
            "Your resource page is useful to teams working on accessible publishing. "
            f"Our concise guide may be a helpful addition: https://{DOMAIN}/guide-{number}\n\n"
            "If it is not a fit, no reply is needed.\n\nThanks,\nExample Team",
            encoding="utf-8",
        )
        _, payload = self.run_cli(
            "email-propose",
            *self.state_args(),
            "--opportunity-id",
            opportunity_id,
            "--recipient",
            f"editor{number}@source{number}.example.org",
            "--subject",
            "Relevant resource suggestion",
            "--body-file",
            body_file,
            "--kind",
            kind,
        )
        return payload["proposal"]["id"]

    def authorize(self, proposal_id, succeeds=True):
        return self.run_cli("email-authorize", *self.state_args(), "--id", proposal_id, succeeds=succeeds)

    def record_sent(self, proposal_id, token):
        return self.run_cli(
            "email-record-sent",
            *self.state_args(),
            "--id",
            proposal_id,
            "--token",
            token,
            "--provider-message-id",
            f"provider-{proposal_id}",
        )

    def test_read_only_never_authorizes_email(self):
        self.initialize("read-only")
        opportunity_id = self.add_opportunity()
        proposal_id = self.propose(opportunity_id)
        _, denial = self.authorize(proposal_id, succeeds=False)
        self.assertIn("never authorizes", denial["error"])

    def test_baseline_snapshot_is_runtime_data_for_the_supplied_domain(self):
        self.initialize("read-only")
        metrics_file = self.workspace / "baseline.json"
        metrics_file.write_text(
            json.dumps({"referring_domains": 42, "backlinks": 117}),
            encoding="utf-8",
        )
        self.run_cli(
            "baseline-record",
            *self.state_args(),
            "--source",
            "test SEO export",
            "--coverage",
            "provider-estimate",
            "--metrics-file",
            metrics_file,
            "--notes",
            "Synthetic test fixture.",
        )
        _, status = self.run_cli("status", *self.state_args())
        self.assertEqual(status["baseline_snapshots"], 1)
        self.assertEqual(status["latest_baseline"]["metrics"]["referring_domains"], 42)

    def test_disavow_candidates_store_evidence_but_offer_no_upload_action(self):
        self.initialize("read-only")
        self.run_cli(
            "disavow-candidate-add",
            *self.state_args(),
            "--source-url",
            "https://spam.example.org/links",
            "--status",
            "candidate",
            "--risk-evidence",
            "Synthetic artificial-link evidence for a test fixture.",
            "--pattern-evidence",
            "Synthetic considerable pattern across controlled test domains.",
            "--acquisition-history",
            "Synthetic prior-provider record.",
            "--removal-status",
            "not-attempted",
            "--manual-action-evidence",
            "Synthetic manual-action fixture.",
        )
        _, status = self.run_cli("status", *self.state_args())
        self.assertEqual(status["disavow_candidates"]["candidate"], 1)
        help_result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertNotIn("disavow-upload", help_result.stdout)

    def test_restricted_requires_exact_approval_and_one_time_token(self):
        self.initialize("restricted")
        opportunity_id = self.add_opportunity()
        proposal_id = self.propose(opportunity_id)

        _, denial = self.authorize(proposal_id, succeeds=False)
        self.assertIn("exact-message approval", denial["error"])

        self.run_cli(
            "email-approve",
            *self.state_args(),
            "--id",
            proposal_id,
            "--confirmed-by-user",
        )
        _, authorization = self.authorize(proposal_id)
        token = authorization["send_token"]
        self.record_sent(proposal_id, token)

        _, reused = self.run_cli(
            "email-record-sent",
            *self.state_args(),
            "--id",
            proposal_id,
            "--token",
            token,
            succeeds=False,
        )
        self.assertIn("already consumed", reused["error"])

    def test_restricted_approval_flag_is_mandatory(self):
        self.initialize("restricted")
        opportunity_id = self.add_opportunity()
        proposal_id = self.propose(opportunity_id)
        _, denial = self.run_cli(
            "email-approve",
            *self.state_args(),
            "--id",
            proposal_id,
            succeeds=False,
        )
        self.assertIn("confirmed-by-user", denial["error"])

    def test_suppression_blocks_unrestricted_send(self):
        self.initialize("unrestricted")
        opportunity_id = self.add_opportunity()
        proposal_id = self.propose(opportunity_id)
        self.run_cli(
            "suppress",
            *self.state_args(),
            "--recipient",
            "@source1.example.org",
            "--reason",
            "Organization requested no contact",
        )
        _, denial = self.authorize(proposal_id, succeeds=False)
        self.assertIn("suppressed", denial["error"])

    def test_unrestricted_rolling_24_hour_cap_is_enforced(self):
        self.initialize("unrestricted")
        for number in range(1, 6):
            opportunity_id = self.add_opportunity(number)
            proposal_id = self.propose(opportunity_id, number)
            _, authorization = self.authorize(proposal_id)
            self.record_sent(proposal_id, authorization["send_token"])

        sixth_opportunity = self.add_opportunity(6)
        sixth_proposal = self.propose(sixth_opportunity, 6)
        _, denial = self.authorize(sixth_proposal, succeeds=False)
        self.assertIn("24-hour outbound cap", denial["error"])

    def test_followup_cannot_be_sent_immediately(self):
        self.initialize("unrestricted")
        opportunity_id = self.add_opportunity()
        initial_id = self.propose(opportunity_id)
        _, authorization = self.authorize(initial_id)
        self.record_sent(initial_id, authorization["send_token"])

        followup_id = self.propose(opportunity_id, kind="followup")
        _, denial = self.authorize(followup_id, succeeds=False)
        self.assertIn("too early", denial["error"])

    def test_recorded_reply_blocks_followup_proposal(self):
        self.initialize("unrestricted")
        opportunity_id = self.add_opportunity()
        initial_id = self.propose(opportunity_id)
        _, authorization = self.authorize(initial_id)
        self.record_sent(initial_id, authorization["send_token"])
        self.run_cli(
            "attempt-log",
            *self.state_args(),
            "--opportunity-id",
            opportunity_id,
            "--actor",
            "agent",
            "--action-type",
            "inbox-review",
            "--channel",
            "email",
            "--outcome",
            "reply-received",
            "--detail",
            "Recipient replied to the outreach thread.",
        )
        body_file = self.workspace / "blocked-followup.txt"
        body_file.write_text("Thank you for taking a look. No further action is needed.", encoding="utf-8")
        _, denial = self.run_cli(
            "email-propose",
            *self.state_args(),
            "--opportunity-id",
            opportunity_id,
            "--recipient",
            "editor1@source1.example.org",
            "--subject",
            "Following up",
            "--body-file",
            body_file,
            "--kind",
            "followup",
            succeeds=False,
        )
        self.assertIn("response-received", denial["error"])

    def test_decline_closes_opportunity_and_suppresses_recipient(self):
        self.initialize("unrestricted")
        opportunity_id = self.add_opportunity()
        initial_id = self.propose(opportunity_id)
        _, authorization = self.authorize(initial_id)
        self.record_sent(initial_id, authorization["send_token"])
        self.run_cli(
            "attempt-log",
            *self.state_args(),
            "--opportunity-id",
            opportunity_id,
            "--actor",
            "agent",
            "--action-type",
            "inbox-review",
            "--channel",
            "email",
            "--outcome",
            "declined",
            "--detail",
            "Recipient declined the request.",
        )
        _, status = self.run_cli("status", *self.state_args())
        self.assertEqual(status["opportunities"]["declined"], 1)
        self.assertEqual(status["suppressions"], 1)

    def test_risk_three_requires_restricted_human_review(self):
        self.initialize("unrestricted")
        opportunity_id = self.add_opportunity(risk=3, status="needs-review")
        proposal_id = self.propose(opportunity_id)
        _, denial = self.authorize(proposal_id, succeeds=False)
        self.assertIn("exact human review", denial["error"])

    def test_runtime_state_is_isolated_by_supplied_target_domain(self):
        self.initialize("read-only")
        self.add_opportunity()
        other_domain = "another-target.example"
        other_args = ("--workspace", self.workspace, "--domain", other_domain)
        self.run_cli(
            "init",
            *other_args,
            "--email-mode",
            "read-only",
            "--crawl-mode",
            "standard-web",
            "--run-mode",
            "loop",
        )
        _, first = self.run_cli("status", *self.state_args())
        _, second = self.run_cli("status", *other_args)
        self.assertEqual(first["config"]["target_domain"], DOMAIN)
        self.assertEqual(second["config"]["target_domain"], other_domain)
        self.assertEqual(sum(first["opportunities"].values()), 1)
        self.assertEqual(sum(second["opportunities"].values()), 0)

    def test_opportunity_target_cannot_escape_configured_domain(self):
        self.initialize("read-only")
        denial = self.add_opportunity(
            target_url="https://different-target.example/guide",
            succeeds=False,
        )
        self.assertIn("authorized target domain", denial["error"])

    def test_target_subdomains_are_allowed(self):
        self.initialize("read-only")
        opportunity_id = self.add_opportunity(target_url=f"https://docs.{DOMAIN}/guide")
        _, listing = self.run_cli("opportunity-list", *self.state_args())
        self.assertEqual(listing["opportunities"][0]["id"], opportunity_id)
        self.assertEqual(listing["opportunities"][0]["target_url"], f"https://docs.{DOMAIN}/guide")

    def test_initial_email_must_link_to_configured_target(self):
        self.initialize("restricted")
        opportunity_id = self.add_opportunity()
        body_file = self.workspace / "wrong-target.txt"
        body_file.write_text(
            "Hello, this unrelated resource might be useful: https://unrelated.example/resource",
            encoding="utf-8",
        )
        _, denial = self.run_cli(
            "email-propose",
            *self.state_args(),
            "--opportunity-id",
            opportunity_id,
            "--recipient",
            "editor1@source1.example.org",
            "--subject",
            "Resource suggestion",
            "--body-file",
            body_file,
            "--kind",
            "initial",
            succeeds=False,
        )
        self.assertIn("authorized target domain", denial["error"])

    def test_backlink_target_cannot_escape_configured_domain(self):
        self.initialize("read-only")
        opportunity_id = self.add_opportunity()
        _, denial = self.run_cli(
            "backlink-record",
            *self.state_args(),
            "--opportunity-id",
            opportunity_id,
            "--source-url",
            "https://source1.example.org/resources",
            "--target-url",
            "https://different-target.example/guide",
            "--http-status",
            "200",
            "--status",
            "verified",
            "--evidence",
            "Synthetic cross-domain fixture.",
            succeeds=False,
        )
        self.assertIn("authorized target domain", denial["error"])

    def test_schedule_requires_explicit_cadence_and_timezone(self):
        schedule_domain = "scheduled-target.example"
        schedule_args = ("--workspace", self.workspace, "--domain", schedule_domain)
        _, denial = self.run_cli(
            "init",
            *schedule_args,
            "--email-mode",
            "read-only",
            "--crawl-mode",
            "auto",
            "--run-mode",
            "schedule",
            succeeds=False,
        )
        self.assertIn("requires --cadence and --timezone", denial["error"])
        _, initialized = self.run_cli(
            "init",
            *schedule_args,
            "--email-mode",
            "read-only",
            "--crawl-mode",
            "auto",
            "--run-mode",
            "schedule",
            "--cadence",
            "weekly Monday 09:00",
            "--timezone",
            "Etc/UTC",
        )
        self.assertEqual(initialized["config"]["execution"]["timezone"], "Etc/UTC")

    def test_web_verified_backlink_is_reflected_in_status(self):
        self.initialize("read-only")
        opportunity_id = self.add_opportunity()
        self.run_cli(
            "backlink-record",
            *self.state_args(),
            "--opportunity-id",
            opportunity_id,
            "--source-url",
            "https://source1.example.org/resources",
            "--target-url",
            f"https://{DOMAIN}/guide-1",
            "--anchor-text",
            "accessible publishing guide",
            "--rel-values",
            "nofollow",
            "--http-status",
            "200",
            "--status",
            "verified",
            "--evidence",
            "Live navigable link observed in the rendered resource list.",
        )
        _, status = self.run_cli("status", *self.state_args())
        self.assertEqual(status["backlinks"]["verified"], 1)
        self.assertEqual(status["opportunities"]["verified"], 1)


if __name__ == "__main__":
    unittest.main()
