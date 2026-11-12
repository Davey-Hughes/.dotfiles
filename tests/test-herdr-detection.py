#!/usr/bin/env python3
"""The local override of herdr's Claude detection rules, run through herdr itself.

config/herdr/agent-detection/claude.toml is herdr's own Claude manifest plus a
rule that keeps a Claude pane `working` while a background subagent it started
is still running. herdr's own rules call that pane idle as soon as the prompt
is back, so the sidebar shows it done while the work goes on (herdr #5004).

These hand screens to `herdr agent explain --file`, which classifies a saved
screen with the manifests it would load, without a server. XDG_CONFIG_HOME and
XDG_STATE_HOME point at an empty directory, so the stock run sees only the
rules built into herdr and the override run sees those plus this file, never
the user's own or any remote update. Needs a real herdr, so CI skips them.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
OVERRIDE = ROOT / 'config/herdr/agent-detection/claude.toml'
RULE = 'background_agent_tree_working'

# The case this is for, after a real pane: the parent's turn ended while a
# background subagent waits on work of its own. The subagent is the `◯` row
# under the footer.
AGENT_RUNNING = '''\
● Pushed the fix. The agent running the two small fixes and the
  targeted CI jobs hasn't reported yet.

✻ Sautéed for 50s · done 3:40 AM · 1 shell, 3 monitors still running

● Agent "two small fixes + targeted CI" is waiting on its own
background work · 13m 25s

──────────────────────────────────────────────────── thumbnails ─
❯
──────────────────────────────────────────────────────────────────
  -- INSERT -- ⏵⏵ auto mode on · 1 shell, 3 monitors · ← 2 agents

  ● main
  ◯ general-purpose  Writing preview-rest.sh … 17m 20s · ↓ 218.5k tokens
'''

# The same tree as Claude Code 2.1.310 draws it, from herdr #5004: `⏺` for the
# main row and a custom statusline above the footer.
AGENT_RUNNING_2_1_310 = '''\
● Started a background agent on the flaky test.

──────────────────────────────────────────────────────────────────
❯
──────────────────────────────────────────────────────────────────
  my-project (main) | Opus 5.5 | 12% context
  ⏵⏵ auto mode on · 1 shell

  ⏺ main
  ◯ general-purpose  Research the flaky test             1m 4s · ↓ 12.3k tokens
'''

# A dev server left running: background shells, but no agent. herdr calls this
# idle on purpose (#3468 took out the rule that kept it working forever).
SHELLS_ONLY = '''\
● The dev server is up on :5173.

✻ Baked for 12s · done 3:40 AM · 1 shell, 1 monitor still running

──────────────────────────────────────────────────────────────────
❯
──────────────────────────────────────────────────────────────────
  -- INSERT -- ⏵⏵ auto mode on · 1 shell, 1 monitor
'''

# The subagent has reported back: its row and the main row are gone.
AGENT_FINISHED = '''\
● Both fixes are in and the targeted CI jobs passed.

──────────────────────────────────────────────────────────────────
❯
──────────────────────────────────────────────────────────────────
  -- INSERT -- ⏵⏵ auto mode on · /tasks to see subagents · ← 1 agent
'''

# A tree row quoted in the transcript, above the prompt box, is not a tree.
ROW_IN_TRANSCRIPT = '''\
● The footer showed this while the agent ran:

  ◯ general-purpose  Writing preview-rest.sh … 17m 20s

──────────────────────────────────────────────────────────────────
❯
──────────────────────────────────────────────────────────────────
  -- INSERT -- ⏵⏵ auto mode on · ← 1 agent
'''

# A permission dialog with the tree still under it. The rule outranks herdr's
# permission rules, so only its guards keep this blocked.
DIALOG_OVER_TREE = '''\
● Running the targeted CI job.

──────────────────────────────────────────────────────────────────
 Bash command

   ./ci/run.sh thumbnail-preview
   Run the thumbnail preview job

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for ./ci/run.sh commands
   3. No

 Esc to cancel · Tab to amend · ctrl+e to explain

  ⏺ main
  ◯ general-purpose  Writing preview-rest.sh … 17m 20s · ↓ 218.5k tokens
'''


@unittest.skipUnless(shutil.which('herdr'), 'needs herdr')
class ClaudeDetectionTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='herdr-detection-'))
        self.stock = self.dir / 'stock'
        self.stock.mkdir()
        self.local = self.dir / 'local'
        (self.local / 'herdr/agent-detection').mkdir(parents=True)
        (self.local / 'herdr/agent-detection/claude.toml').symlink_to(OVERRIDE)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def explain(self, screen, config):
        """herdr's verdict on `screen` with the manifests under `config`."""
        path = self.dir / 'screen.txt'
        path.write_text(screen)
        env = {k: v for k, v in os.environ.items() if not k.startswith('HERDR_')}
        env['XDG_CONFIG_HOME'] = str(config)
        env['XDG_STATE_HOME'] = str(self.stock)
        out = subprocess.run(
            ['herdr', 'agent', 'explain', '--file', str(path), '--agent', 'claude', '--json'],
            env=env, capture_output=True, text=True, check=True, timeout=20).stdout
        return json.loads(out)

    def verdict(self, screen):
        """(state, matched rule) with the override, which herdr must have loaded:
        it ignores an override it cannot parse, with only a warning."""
        result = self.explain(screen, self.local)
        self.assertEqual(result['manifest_source'], str(self.local / 'herdr/agent-detection/claude.toml'),
                         result.get('warning'))
        return result['state'], (result['matched_rule'] or {}).get('id')

    # --- what the override decides ---------------------------------------------

    def test_a_background_agent_keeps_the_pane_working(self):
        self.assertEqual(self.verdict(AGENT_RUNNING), ('working', RULE))

    def test_the_tree_as_newer_claude_code_draws_it(self):
        self.assertEqual(self.verdict(AGENT_RUNNING_2_1_310), ('working', RULE))

    def test_background_shells_alone_leave_it_idle(self):
        self.assertEqual(self.verdict(SHELLS_ONLY), ('idle', 'live_prompt_box'))

    def test_the_pane_goes_idle_once_the_agent_reports_back(self):
        self.assertEqual(self.verdict(AGENT_FINISHED), ('idle', 'live_prompt_box'))

    def test_a_row_quoted_in_the_transcript_is_not_an_agent(self):
        self.assertEqual(self.verdict(ROW_IN_TRANSCRIPT), ('idle', 'live_prompt_box'))

    def test_a_permission_dialog_still_blocks(self):
        self.assertEqual(self.verdict(DIALOG_OVER_TREE)[0], 'blocked')

    # --- the override against herdr's own rules --------------------------------

    def test_every_rule_of_herdrs_own_is_kept_as_it_is(self):
        def rules(result):
            return {r['id']: (r['state'], r['priority'], r['region'], r['evidence'])
                    for r in result['evaluated_rules']}
        stock = rules(self.explain(AGENT_FINISHED, self.stock))
        local = rules(self.explain(AGENT_FINISHED, self.local))
        self.assertEqual({k: v for k, v in local.items() if k != RULE}, stock)

    def test_herdr_still_needs_the_override(self):
        # Fails once herdr's own rules see a background agent. Then the override
        # only freezes herdr's Claude rules: delete it.
        result = self.explain(AGENT_RUNNING, self.stock)
        self.assertEqual(result['state'], 'idle',
                         'herdr now keeps this pane working itself (#5004); '
                         f'delete {OVERRIDE.relative_to(ROOT)}')


if __name__ == '__main__':
    unittest.main()
