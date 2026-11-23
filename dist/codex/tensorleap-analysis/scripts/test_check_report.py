import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
# Every payload the export holds: (group, its parent, sample, data type, view, failed render).
# Group 7 is a sub-group of 2. The "image" view of test_41 has an overlay that failed beside
# the picture itself, as a crashed heatmap leaves it; its "overlay" view has nothing else.
PAYLOADS = ((1, None, "test_41", "image", "image", False), (1, None, "test_41", "text", "image", True),
            (1, None, "test_41", "video", "clip", False), (1, None, "test_41", "text", "overlay", True),
            (1, None, "test_87", "image", "gt", False), (1, None, "test_87", "image", "pred", False),
            (2, None, "test_50", "image", "image", False), (7, 2, "test_60", "image", "image", False))


def make_export():
    """An export directory as tl_export_analysis leaves it, as far as check-report reads it."""
    root = tempfile.mkdtemp()
    groups = {}
    for group, parent, sample, data_type, view, failed in PAYLOADS:
        base = (f"insight_{parent}_low_performance/sub_{group}_low_performance" if parent
                else f"insight_{group}_low_performance")
        rel = f"{base}/samples/{sample}/{data_type}/{view}/payload.json"
        os.makedirs(os.path.dirname(os.path.join(root, rel)))
        with open(os.path.join(root, rel), "w") as f:
            json.dump({"visualizer_name": view, "has_error": failed}, f)
        groups.setdefault((group, parent), {}).setdefault(sample, []).append(rel)
    manifest = {"projectId": "p", "versionId": "v", "version": "model",
                "visualizers": [{"name": "image"}, {"name": "frames_grid"}],
                "insights": [dict({"index": group, "type": "low_performance",
                                   "samples": [{"id": s, "rank": i, "files": f}
                                               for i, (s, f) in enumerate(samples.items(), 1)]
                                   + ([{"id": "test_99", "rank": 9, "files": []}] if group == 1 else [])},
                                  **({"parentIndex": parent} if parent else {}))
                             for (group, parent), samples in groups.items()]}
    with open(os.path.join(root, "manifest.json"), "w") as f:
        json.dump(manifest, f)
    return root


def make_finding(**over):
    finding = {"id": "insight-1", "severity": 3, "title": "Night-time dogs are labelled cat",
               "shortTitle": "Night dogs labelled cat", "sampleCount": "314",
               "firstAction": "Relabel the night scenes",
               "chips": ["314 samples", "accuracy 0.42 vs 0.87"],
               "bottomLine": "Every sample in this group shows a dog whose label says cat.",
               "rootCause": {"family": "Label quality", "caption": "the labels are noisy."},
               "analysis": ["Only <em>12</em> training samples are night scenes."],
               "split": [{"state": "training", "count": 12}, {"state": "test", "count": 302}],
               "contrast": [{"metric": "accuracy", "group": 0.42, "all": 0.87}],
               "observations": "Looking at the samples, the scenes are dark.",
               "samples": {"intro": "the camera frame", "layout": "wide", "viewLabels": [],
                           "items": [{"caption": "test_41 · loss 4.2", "sampleId": "test_41",
                                      "visualizers": ["image"]}]},
               "doNext": ["Relabel the 314 night scenes."],
               "test": {"hint": "accuracy stays above 0.8 on this group"}}
    finding.update(over)
    return {k: v for k, v in finding.items() if v is not None}


def make_report(findings=None, kind="low_performance", **over):
    report = {"schemaVersion": 1, "project": "pets", "version": "resnet", "evaluated": "2026-07-28",
              "coverage": "1 finding shown · 4 found", "modality": "visual",
              "tiles": [{"value": "0.87", "label": "accuracy, all data"},
                        {"value": "1", "label": "group where the model underperforms", "kind": kind}],
              "summary": 'The model fails on <a href="#insight-1">night scenes</a>.',
              "groups": [{"title": "Where the model underperforms", "kind": kind, "count": "1 of 4",
                          "meaning": "Groups of samples where the model underperforms.",
                          "findings": [make_finding()] if findings is None else findings}]}
    report.update(over)
    return {k: v for k, v in report.items() if v is not None}


def check(report, root=None):
    root = root or make_export()
    path = os.path.join(root, "report.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report if isinstance(report, str) else json.dumps(report))
    r = subprocess.run([sys.executable, os.path.join(HERE, "tl_api.py"), "check-report", root],
                       capture_output=True, text=True, env=dict(os.environ, TL_DISABLE_ANALYTICS="1"))
    txt_path = os.path.join(root, "report.txt")
    txt = open(txt_path, encoding="utf-8").read() if os.path.isfile(txt_path) else ""
    return r, txt, path


def refused(report, *said, root=None):
    r, txt, _ = check(report, root)
    assert r.returncode == 8, (r.returncode, r.stdout, r.stderr)
    assert not txt, "a refused report must leave no report.txt to read"
    for phrase in said:
        assert phrase in r.stderr, (phrase, r.stderr)
    return r.stderr


def check_good_report_passes_and_reads_as_the_reader_sees_it():
    r, txt, _ = check(make_report())
    assert r.returncode == 0, r.stderr
    for line in ("pets · resnet", "evaluated 2026-07-28 · 1 finding shown · 4 found",
                 "[tile] 0.87: accuracy, all data", "The model fails on night scenes.",
                 "#1 | Night dogs labelled cat | HIGH | 314 | Relabel the night scenes",
                 "== Where the model underperforms (1 of 4): Groups of samples where the model underperforms.",
                 "### Night-time dogs are labelled cat  [#1]",
                 "chips: Severity 3 of 3 · 314 samples · accuracy 0.42 vs 0.87",
                 "Root cause · Label quality: the labels are noisy.",
                 "Only 12 training samples are night scenes.", "split: training 12, test 302",
                 "accuracy: this group 0.42 vs all data 0.87",
                 "LLM visual analysis: Looking at the samples, the scenes are dark.",
                 'samples (collapsed "Samples (1)" fold, all shown on open): test_41 · loss 4.2',
                 "Do next: Relabel the 314 night scenes.",
                 "[button] Add the regression test (accuracy stays above 0.8 on this group)"):
        assert line in txt, (line, txt)


def check_only_the_required_fields_are_needed():
    bare = {"id": "insight-2-sub-7", "title": "t", "chips": [], "bottomLine": "b",
            "analysis": [], "split": [], "contrast": [], "doNext": []}
    r, txt, _ = check(make_report([bare], evaluated=None, coverage=None, tiles=[], summary="s"))
    assert r.returncode == 0, r.stderr
    assert "### t  [#2 · sub #7]" in txt, txt


def check_a_field_the_script_does_not_know_is_left_alone():
    r, _, _ = check(make_report([make_finding(reviewedBy="a later skill")], audience="a later skill"))
    assert r.returncode == 0, r.stderr


def check_em_dashes_are_rewritten_in_the_file_that_gets_published():
    r, txt, path = check(make_report([make_finding(bottomLine="Dogs — at night — are labelled cat.")]))
    assert r.returncode == 0, r.stderr
    saved = json.load(open(path, encoding="utf-8"))
    assert saved["groups"][0]["findings"][0]["bottomLine"] == "Dogs, at night, are labelled cat."
    assert "—" not in txt and "em-dashes rewritten" in r.stdout
    said = "Fine — until the last scene."
    r, _, path = check(make_report([samples_of({"caption": "c", "visualizers": [], "text": said})]))
    assert r.returncode == 0, r.stderr
    saved = json.load(open(path, encoding="utf-8"))
    assert saved["groups"][0]["findings"][0]["samples"]["items"][0]["text"] == said, "a quoted sample stays as written"


def check_a_directory_without_an_export_is_named():
    root = tempfile.mkdtemp()
    r, _, _ = check(make_report(), root)
    assert r.returncode == 5 and "manifest.json" in r.stderr, (r.returncode, r.stderr)


def check_unreadable_report_is_refused():
    refused("{not json", "cannot read")
    refused(json.dumps(["a list"]), "report.json must be an object")


def check_missing_and_mistyped_fields_are_named():
    refused(make_report(project=None, modality="video", schemaVersion=2, summary=""),
            "report.json: project is missing", "report.json: summary is empty")
    refused(make_report(modality="video", schemaVersion=2),
            "schemaVersion must be 1", "modality must be one of visual, textual, auditory")
    said = refused(make_report([make_finding(bottomLine=None, doNext="Relabel", severity="3")]),
                   "finding insight-1: bottomLine is missing", "finding insight-1: doNext must be a list",
                   "finding insight-1: severity must be a number")
    assert "not a finding in this report" not in said, "a refused finding is still one a link may point to"
    refused(make_report([make_finding(split=[{"state": "training", "count": "12"}],
                                      contrast=[{"metric": "accuracy", "group": 0.4}])]),
            "finding insight-1 split: count must be a number", "finding insight-1 contrast: all is missing")
    refused(make_report([make_finding(chips=["314 samples", 3])]), "chips must hold text only")
    refused(make_report(kind="failure_mode"), "tile 2: kind must be one of low_performance",
            "group 1: kind must be one of low_performance")
    numbered = make_report()
    numbered["groups"][0]["count"] = 4
    said = refused(numbered, "group 1: count must be text")
    assert "not a finding in this report" not in said, "a refused group still holds the findings links point to"


def check_finding_ids_and_severity():
    refused(make_report([make_finding(id="failure-mode-1")]),
            "finding failure-mode-1: id must be insight-<number> or insight-<number>-sub-<number>")
    refused(make_report([make_finding(), make_finding(id="insight-1-sub-2"), make_finding()],
                        summary="s"), "finding insight-1: this id appears more than once")
    refused(make_report([make_finding(severity=4)]), "finding insight-1: severity is 1, 2 or 3")


def samples_of(*items, **over):
    return make_finding(samples=dict({"layout": "default", "viewLabels": [], "items": list(items)}, **over))


def check_a_sample_must_be_one_the_export_downloaded():
    refused(make_report([samples_of({"caption": "c", "sampleId": "test_41", "visualizers": ["heatmap"]})]),
            'the export has no "heatmap" view of sample test_41')
    refused(make_report([samples_of({"caption": "c", "sampleId": "test_7", "visualizers": ["image"]})]),
            'the export has no "image" view of sample test_7')
    # listed in the export, but the platform had nothing rendered for it
    refused(make_report([samples_of({"caption": "c", "sampleId": "test_99", "visualizers": ["image"]})]),
            'the export has no "image" view of sample test_99')
    refused(make_report([samples_of({"caption": "no view", "sampleId": "test_41", "visualizers": []})]),
            'the sample "no view" must be a text, or a sampleId with one or two visualizers')
    refused(make_report([samples_of({"caption": "three", "sampleId": "test_87",
                                     "visualizers": ["gt", "pred", "image"]})]),
            'the sample "three" must be a text')
    refused(make_report([samples_of(layout="grid")]), "samples layout must be one of default, wide, solo")


def check_a_card_shows_its_own_group_under_its_own_number():
    refused(make_report([make_finding(id="insight-42")]),
            "finding insight-42: the export has no top-level group 42")
    refused(make_report([make_finding(id="insight-1-sub-7")]),
            "finding insight-1-sub-7: the export has no sub-group 7 under group 1")
    refused(make_report([make_finding(id="insight-7")]), "the export has no top-level group 7")
    other = {"caption": "c", "sampleId": "test_50", "visualizers": ["image"]}
    refused(make_report([samples_of(other)]), "sample test_50 was exported for another group")
    # a top-level card may show what was exported under its sub-groups, a sub-group only its own
    of_sub = {"caption": "c", "sampleId": "test_60", "visualizers": ["image"]}
    r, _, _ = check(make_report([dict(samples_of(other, of_sub), id="insight-2")], summary="s"))
    assert r.returncode == 0, r.stderr
    refused(make_report([dict(samples_of(other), id="insight-2-sub-7")], summary="s"),
            "sample test_50 was exported for another group")


def check_any_kind_of_view_can_be_shown_unless_it_failed_to_render():
    clip = {"caption": "the clip itself", "sampleId": "test_41", "visualizers": ["clip", "image"]}
    r, _, _ = check(make_report([samples_of(clip, viewLabels=["The clip", "Its first frame"])]))
    assert r.returncode == 0, r.stderr
    refused(make_report([samples_of({"caption": "c", "sampleId": "test_41", "visualizers": ["overlay"]})]),
            'the "overlay" view of sample test_41 did not render')


def check_text_samples_and_pairs():
    pair = {"caption": "test_87 · loss 3.9", "sampleId": "test_87", "visualizers": ["gt", "pred"]}
    quote = {"caption": "test_12 · loss 3.7", "visualizers": [], "text": "the review's own words"}
    r, txt, _ = check(make_report([samples_of(pair, quote, viewLabels=["Label", "Model's answer"])]))
    assert r.returncode == 0, r.stderr
    assert "views: Label | Model's answer" in txt and 'Samples (2)' in txt, txt
    refused(make_report([samples_of(pair)]), "viewLabels must name the two views of a pair, in order")


def check_mislabeling_is_a_root_cause_never_a_chip():
    r, txt, _ = check(make_report([make_finding(
        rootCause={"family": "Mislabeling", "caption": "the labels are wrong, the model is right."},
        chips=["314 samples", "88 mislabeled candidates"])]))
    assert r.returncode == 0, r.stderr
    assert "chips: Severity 3 of 3 · 314 samples · 88 mislabeled candidates · Mislabeled" in txt, txt
    r, txt, _ = check(make_report())
    assert r.returncode == 0 and "Mislabeled" not in txt, (r.stderr, txt)
    for chip in ("Mislabeled", "mislabeling", "Coherently mislabeled", " Mis-labelled. "):
        refused(make_report([make_finding(chips=["314 samples", chip])]), "remove the hand-written")
    for family in ("mislabeling", "Mislabeled", "Mislabeling, Data gap"):
        refused(make_report([make_finding(rootCause={"family": family, "caption": "c"})]),
                'must be written exactly as "Mislabeling", alone')
    refused(make_report([make_finding(rootCause={"family": "Mislabeling", "caption": "c"})],
                        kind="duplication"), "only for a group of kind low_performance")
    refused(make_report([make_finding(rootCause="Label quality")]), "rootCause must be an object")


def check_root_cause_family_and_severity_order_are_fixed():
    r, _, _ = check(make_report([make_finding(rootCause={"family": "Label quality, Model behavior", "caption": "c"})]))
    assert r.returncode == 0, r.stderr
    for family in ("Taxonomy overlap", "label quality", "Label quality, Label quality",
                   "Data gap, Label quality, Model behavior"):
        refused(make_report([make_finding(rootCause={"family": family, "caption": "c"})]),
                "must be one of Data gap, Label quality, Mislabeling, Split problem, Model behavior")
    refused(make_report([make_finding(severity=1), make_finding(id="insight-2", severity=3, samples=None)],
                        summary="s"), "group 1: its findings must be ordered by severity, highest first")


def check_the_platform_vocabulary_stays_out_of_the_text():
    refused(make_report(coverage="3 findings shown · 9 found by Tensorleap"), 'coverage: "Tensorleap"')
    refused(make_report(tiles=[{"value": "3", "label": "Failure Mode insights"}]), 'tile 1: "Failure Mode"')
    refused(make_report([make_finding(analysis=["This insight sits in the image space."])]),
            'finding insight-1: "insight"')
    refused(make_report([make_finding(doNext=["Open the insights panel."])]), '"insights"')
    refused(make_report([samples_of({"caption": "test_41 · frames_grid of the clip", "sampleId": "test_41",
                                     "visualizers": ["image"]})]),
            '"frames_grid" is a name from the integration code')
    # a finding's id and the links to it are not text the reader sees
    r, _, _ = check(make_report([make_finding(observations='As in <a href="#insight-1">the group above</a>.')]))
    assert r.returncode == 0, r.stderr


def check_markup_only_where_the_report_shows_it():
    refused(make_report([make_finding(title="<em>Night</em> dogs")]), "must be plain text")
    refused(make_report([make_finding(doNext=["Relabel <b>314</b> scenes."])]), "must be plain text")
    refused(make_report([make_finding(analysis=["<p>Night scenes.</p><script>x()</script>"])]),
            "only em, strong, code, a markup is shown, remove p, script")
    refused(make_report(summary='See <a href="#insight-9">the other group</a>.'),
            'a link points to "#insight-9", which is not a finding in this report')
    r, _, _ = check(make_report([make_finding(
        analysis=['Loss &lt; 0.5 on <strong>12</strong> of <code>302</code>, see <a href="https://example.com/x">the paper</a>.'],
        chips=["loss < 0.5", "A & B"])]))
    assert r.returncode == 0, r.stderr


def check_a_report_over_the_size_limit_is_refused():
    refused(make_report([make_finding(analysis=["x" * (1024 * 1024)])]), "larger than 1 MB")


def main():
    failed = []
    for name, fn in list(globals().items()):
        if not name.startswith("check_"):
            continue
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:
            failed.append(name)
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    if failed:
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
