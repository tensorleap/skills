"""The published report: one self-contained HTML page from report.md, with collapsed appendices
and the run's files."""
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


class AppendixAndContentsTest(unittest.TestCase):

    def test_appendix_is_collapsed_and_contents_nest_subsections(self):
        md = ("# T\n\n## Part 1 — Offline\n\n### Summary\n\ntext\n\n### Appendix 1 — offline details\n\n"
              "**Environment**\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n## Part 2 — Online\n\n### Summary\n\nmore\n")
        page = report_html.markdown_to_html(md, "T")
        self.assertEqual(page.count("<details"), 1)
        self.assertEqual(page.count("</details>"), 1)
        self.assertLess(page.index("<details"), page.index("<table>"))
        self.assertLess(page.index("</details>"), page.index('id="part-2-online"'))
        self.assertIn("<summary>Appendix 1 — offline details</summary>", page)
        self.assertIn('<a href="#summary-2">Summary</a>', page)


class RunFilesTest(unittest.TestCase):

    def test_run_files_listed_from_the_artifacts_present(self):
        out = tempfile.mkdtemp()
        for name in ("preflight.json", "floor.json", "score.json"):
            with open(os.path.join(out, name), "w") as fh:
                json.dump({}, fh)
        os.makedirs(os.path.join(out, "runs", "001"))
        line = report_html.run_files_line(out)
        self.assertIn("`preflight.json` · `floor.json` · `runs/` · `score.json`", line)
        self.assertNotIn("online", line)
        self.assertEqual(report_html.run_files_line(tempfile.mkdtemp()), "")


def _profile(footprint):
    return {"dataset": {"state_lengths": {"training": 100}}, "startup": {"stats": {"mean": 2.0}},
            "generation": {"per_sample_seconds": {"mean": 0.004}, "handlers": {}},
            "inference": {"per_sample_mean_seconds": 0.001}, "metrics": {"handlers": {}},
            "visualizers": {"per_sample_seconds": {"mean": 0.010}, "handlers": {}},
            "user_memory": {"footprint_gb": footprint, "breakdown_gb": {"preprocess": footprint / 2}}}


class PriorityInTheReportTest(unittest.TestCase):

    def setUp(self):
        import tl_perf
        self.tl = tl_perf
        self.out = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.out, "baseline"))
        with open(os.path.join(self.out, "baseline", "profile.json"), "w") as fh:
            json.dump(_profile(2.0), fh)
        self.doc = {"title": "T", "summary": "s", "environment": {}, "optimizations": [], "remaining_bottleneck":
                    {"component": "c", "share": "s", "evidence": "e"},
                    "memory": {"status": "AMBER", "reasons": [], "remaining_holder": {"target": "h", "evidence": "1 GB"}},
                    "tensorleap_actions": [], "lossy_options": [], "remaining_integration_issues": [],
                    "coverage_caveats": []}

    def test_memory_is_the_default_and_leads(self):
        md = self.tl.render_report(self.doc, self.out)
        self.assertIn("**Priority: memory first** — the default", md)
        self.assertLess(md.index("### Where the memory goes"), md.index("### Where the time goes"))
        self.assertLess(md.index("**Memory per worker process:**"), md.index("**Expected runtime:**"))
        self.assertLess(md.index("**1. memory: h**"), md.index("**2. c**"))
        self.assertIn("### Trade-offs taken", md)
        self.assertIn("_None recorded", md)
        self.assertIn("## Part 2 — Online diagnostics: not run", md)

    def test_memory_leads_only_under_pressure(self):
        doc = dict(self.doc, memory={"status": "GREEN", "reasons": [],
                                     "remaining_holder": {"target": "h", "evidence": "1 GB"}})
        md = self.tl.render_report(doc, self.out)
        self.assertIn("**Priority: memory first**", md)
        self.assertIn("**No memory pressure** (memory status GREEN), so time leads this part.", md)
        self.assertLess(md.index("### Where the time goes"), md.index("### Where the memory goes"))
        self.assertLess(md.index("**1. c**"), md.index("**2. memory: h**"))
        doc["memory"] = dict(doc["memory"], status="RED", reasons=["RED: the user reports out-of-memory failures"])
        md = self.tl.render_report(doc, self.out)
        self.assertIn("**Memory leads this part:** memory status RED — the user reports out-of-memory failures.", md)
        self.assertLess(md.index("### Where the memory goes"), md.index("### Where the time goes"))

    def test_runtime_priority_puts_time_first(self):
        md = self.tl.render_report(dict(self.doc, priority="runtime", priority_reason="the user: \"speed\""), self.out)
        self.assertIn("**Priority: runtime first** — the user: \"speed\"", md)
        self.assertLess(md.index("### Where the time goes"), md.index("### Where the memory goes"))
        self.assertLess(md.index("**1. c**"), md.index("**2. memory: h**"))
        self.assertNotIn("### Trade-offs taken", md)
        self.assertTrue(any("priority" in e for e in self.tl.validate_report(dict(self.doc, priority="speed"))))

    def test_time_table_shares_and_floor_ratio(self):
        md = self.tl.render_report(self.doc, self.out)
        self.assertIn("| generation | 4.000 ms | - | 11% | 4.0× |", md)
        self.assertIn("| **expected total** | 4 s | - | 100% |  |", md)


if __name__ == "__main__":
    unittest.main()
