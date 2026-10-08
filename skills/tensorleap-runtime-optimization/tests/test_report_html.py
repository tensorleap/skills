"""The published report: one self-contained HTML page from report.md, ending with every step the
skill ran."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import report_html  # noqa: E402


MD = """# Runtime optimization — demo

Summary with `code`, **bold** and _emphasis_ & <tags>.

## Online diagnostics

### Root causes

**1. visualizations — 100 s**

- **Pace set by:** the workers
- **Where the time went:**

  | part | time |
  |---|---|
  | rendering | 90 s |

- **Explained:** 90%
  wrapped continuation

| a | b \\| c |
|---|---|
| 1 | 2 |
"""


class MarkdownToHtmlTest(unittest.TestCase):

    def test_self_contained_page_with_contents_tables_and_lists(self):
        page = report_html.markdown_to_html(MD, "Runtime optimization — demo", "sub")
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertNotIn("<script", page)
        self.assertNotIn("http://", page.split("<main>")[0])            # no external assets
        self.assertIn('<a href="#online-diagnostics">Online diagnostics</a>', page)
        self.assertIn("<code>code</code>", page)
        self.assertIn("<strong>bold</strong>", page)
        self.assertIn("<em>emphasis</em>", page)
        self.assertIn("&amp; &lt;tags&gt;", page)
        self.assertEqual(page.count("<table>"), 2)
        self.assertIn("<td>rendering</td>", page)
        self.assertIn("<th>b | c</th>", page)                          # an escaped pipe stays in its cell
        self.assertEqual(page.count("<ul>"), page.count("</ul>"))
        self.assertIn("wrapped continuation", page)


class StepsSectionTest(unittest.TestCase):

    def test_steps_listed_from_the_artifacts_present(self):
        out = tempfile.mkdtemp()
        for name, data in (("preflight.json", {"exit_code": 0, "findings": []}),
                           ("floor.json", {"t_inf_per_sample_mean_seconds": 0.002, "recommended_batch_size": 32}),
                           ("static.json", [{"handler": "x"}])):
            with open(os.path.join(out, name), "w") as fh:
                json.dump(data, fh)
        os.makedirs(os.path.join(out, "runs", "001"))
        doc = {"optimizations": [{"title": "a"}], "server_validation": {"mode": "smoke", "status": "FINISHED"}}
        md = "\n".join(report_html.steps_section(doc, out))
        self.assertIn("## How this report was made", md)
        for step in ("Phase 0 — preflight", "Phase 1 — floor and fit", "Phase 2 — read the code",
                     "Phase 3 — baseline profile", "Phase 4 — lossless optimization loop",
                     "Phase 6A — server smoke validation", "Phase 7 — report"):
            self.assertIn(step, md)
        self.assertIn("floor 2.000 ms per sample; batch 32", md)
        self.assertNotIn("Phase 6B — collect", md)                      # no online artifacts


class PriorityInTheReportTest(unittest.TestCase):

    def test_memory_priority_leads_the_report(self):
        import tl_perf
        out = tempfile.mkdtemp()
        doc = {"title": "T", "summary": "s", "environment": {}, "optimizations": [], "remaining_bottleneck":
               {"component": "c", "share": "s", "evidence": "e", "why": "w"}, "priority": "memory",
               "memory": {"status": "GREEN", "reasons": [], "largest_remaining": "none"},
               "tensorleap_actions": [], "lossy_options": [], "remaining_integration_issues": [],
               "coverage_caveats": []}
        md = tl_perf.render_report(doc, out)
        self.assertIn("**Priority: memory.**", md)
        self.assertLess(md.index("## Memory"), md.index("## Runtime breakdown"))
        doc["priority"] = "runtime"
        md = tl_perf.render_report(doc, out)
        self.assertNotIn("Priority: memory", md)
        self.assertLess(md.index("## Runtime breakdown"), md.index("## Memory"))
        self.assertTrue(any("priority" in e for e in tl_perf.validate_report(dict(doc, priority="speed"))))


if __name__ == "__main__":
    unittest.main()
