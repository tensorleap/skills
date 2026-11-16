import copy
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, quote, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
LINK1 = "http://ui/i1?dashboard=d&state=s&addCommentToInsight=c1&comment="
LINK2 = "http://ui/i2?dashboard=d&state=s&addCommentToInsight=c2&comment="


def make_card(**over):
    card = {"id": "insight-1", "severity": 3, "heading": "Night-time dogs are labelled cat",
            "chips": ["2 samples", "image-non-semantic space"],
            "lede": "Every sample in this group shows a dog whose label says cat.",
            "root_cause": {"family": "Label quality", "caption": "the labels are noisy."},
            "add_test": {"link": "http://ui/i1?addTestFromInsight=c1", "label": "Add the suggested test"},
            "explore": {"link": "http://ui/i1", "text": "open"}}
    card.update(over)
    return {k: v for k, v in card.items() if v is not None}


def make_report(cards, group_type="Failure Mode"):
    return {"project": "p", "version": "v", "meta": "1 insight", "insights_link": "http://ui/p",
            "executive_summary": "x",
            "overview": [{"type": group_type, "rows": [{"anchor": c["id"], "num": "#1", "issue": "x",
                                                            "severity": "3 of 3", "samples": "2", "first_action": "x"}
                                                           for c in cards]}],
            "groups": [{"type": group_type, "count": str(len(cards)), "meaning": "m", "cards": cards}]}


def build(cards, group_type="Failure Mode"):
    root = tempfile.mkdtemp()
    with open(os.path.join(root, "report.json"), "w") as f:
        json.dump(make_report(cards, group_type), f)
    r = subprocess.run([sys.executable, os.path.join(HERE, "tl_api.py"), "build-report", root],
                       capture_output=True, text=True)
    page = txt = ""
    if os.path.isfile(os.path.join(root, "report.html")):
        page = open(os.path.join(root, "report.html"), encoding="utf-8").read()
        txt = open(os.path.join(root, "report.txt"), encoding="utf-8").read()
    return r, page, txt


def card_section(page, card_id):
    m = re.search(r'<section class="card" id="%s".*?</section>' % re.escape(card_id), page, re.S)
    assert m, f"card {card_id} not rendered"
    return m.group(0)


def header_buttons(page, card_id):
    head = re.search(r'<div class="chead">(.*?)</div>', card_section(page, card_id), re.S).group(1)
    return [(html.unescape(href), re.sub(r"<[^>]+>", "", inner).strip())
            for href, inner in re.findall(r'<a class="button" href="([^"]*)"[^>]*>(.*?)</a>', head, re.S)]


def chips(page, card_id):
    block = re.search(r'<div class="chips">(.*?)</div>', card_section(page, card_id), re.S).group(1)
    return [html.unescape(c) for c in re.findall(r'<span class="chip">([^<]*)</span>', block)]


def check_add_comment_button_carries_heading():
    r, page, _ = build([make_card(heading="Night dogs & cats — #1 + 50%", add_comment={"link": LINK1})])
    assert r.returncode == 0, r.stderr
    btns = header_buttons(page, "insight-1")
    labels = [label for _, label in btns]
    assert "Add comment" in labels, labels
    href = btns[labels.index("Add comment")][0]
    title = "Night dogs & cats, #1 + 50%"
    assert href == LINK1 + quote(title, safe=""), href
    assert parse_qs(urlsplit(href).query)["comment"] == [title], href
    assert parse_qs(urlsplit(href).query)["addCommentToInsight"] == ["c1"], href


def check_sub_insight_card_targets_its_own_insight():
    parent = make_card(add_comment={"link": LINK1})
    sub = make_card(id="insight-1-sub-2", heading="Foggy dogs", add_test=None, add_comment={"link": LINK2})
    r, page, _ = build([parent, sub])
    assert r.returncode == 0, r.stderr
    for card_id, cid in (("insight-1", "c1"), ("insight-1-sub-2", "c2")):
        hrefs = [href for href, label in header_buttons(page, card_id) if label == "Add comment"]
        assert len(hrefs) == 1, (card_id, header_buttons(page, card_id))
        assert parse_qs(urlsplit(hrefs[0]).query)["addCommentToInsight"] == [cid], hrefs


def check_report_txt_lists_add_comment():
    r, _, txt = build([make_card(add_comment={"link": LINK1})])
    assert r.returncode == 0, r.stderr
    assert "[header button] Add comment" in txt, [l for l in txt.splitlines() if "header button" in l]


def check_hand_edited_comment_link_rejected():
    r, _, _ = build([make_card(id="insight-7", add_comment={"link": LINK1 + "Night%20dogs"})])
    assert r.returncode == 8, (r.returncode, r.stderr)
    assert "insight-7" in r.stderr, r.stderr


def check_no_button_without_link():
    r, page, txt = build([make_card()])
    assert r.returncode == 0, r.stderr
    assert [label for _, label in header_buttons(page, "insight-1")] == ["Test", "Analyze insight"]
    assert "Add comment" not in page and "Add comment" not in txt


MISLABELING = {"family": "Mislabeling", "caption": "the model is right, the ground truth is wrong."}


def check_mislabeling_root_cause_adds_chip():
    r, page, txt = build([make_card(root_cause=MISLABELING)])
    assert r.returncode == 0, r.stderr
    assert chips(page, "insight-1")[-1] == "Mislabeled", chips(page, "insight-1")
    assert "Root cause · Mislabeling:" in card_section(page, "insight-1")
    chip_line = next(line for line in txt.splitlines() if line.startswith("chips: "))
    assert chip_line.endswith(" · Mislabeled"), chip_line


def check_label_quality_gets_no_chip():
    r, page, txt = build([make_card()])
    assert r.returncode == 0, r.stderr
    assert "Mislabeled" not in chips(page, "insight-1"), chips(page, "insight-1")
    assert "Mislabeled" not in txt


def check_hand_written_verdict_chip_rejected():
    variants = ("Mislabeled", "Mislabelled", "misslabelled", " mis-labeled ", "Mislabeling",
                "Coherent mislabeling", "Coherently mislabeled", "Mis labeled", "Mislabeled \u2713")
    bad = []
    for rc in (MISLABELING, {"family": "Label quality", "caption": "c"}, None):
        for chip in variants:
            card = make_card(id="insight-6", chips=["2 samples", chip], root_cause=rc)
            r, _, _ = build([card])
            if r.returncode != 8 or "insight-6" not in r.stderr or "Traceback" in r.stderr:
                bad.append((chip, rc and rc["family"], r.returncode, r.stderr.strip()[-200:]))
    assert not bad, bad


def check_chip_that_only_mentions_mislabeling_accepted():
    cases = [("88 mislabeled candidates", None),
             ("mislabeled-sample check: clean", {"family": "Data gap", "caption": "few night samples."}),
             ("12 mislabels", {"family": "Model behavior", "caption": "c"})]
    bad = []
    for group_type in ("Failure Mode", "Mislabeled", "Duplication"):
        for chip, rc in cases:
            card = make_card(chips=["2 samples", chip], root_cause=rc)
            r, page, _ = build([card], group_type)
            if r.returncode != 0 or chip not in chips(page, "insight-1"):
                bad.append((group_type, chip, rc and rc["family"], r.returncode, r.stderr.strip()[:200]))
    assert not bad, bad


def check_mislabeling_outside_failure_mode_rejected():
    bad = []
    for group_type in ("Mislabeled", "Duplication", "Out of Distribution"):
        r, _, _ = build([make_card(id="insight-5", root_cause=MISLABELING)], group_type)
        if r.returncode != 8 or "insight-5" not in r.stderr or "Traceback" in r.stderr:
            bad.append((group_type, r.returncode, r.stderr.strip()[-200:]))
    assert not bad, bad


def check_mislabeling_must_be_exact_and_alone():
    families = ("Mislabeling, Model behavior", "Label quality, Mislabeling", "mislabeling", " Mislabeling",
                "Mislabeled", "Mislabelling", ["Mislabeling"], ["Mislabeling", "Data gap"])
    bad = []
    for family in families:
        card = make_card(id="insight-9", root_cause={"family": family, "caption": "x"})
        r, _, _ = build([card])
        if r.returncode != 8 or "insight-9" not in r.stderr or "Traceback" in r.stderr:
            bad.append((family, r.returncode, r.stderr.strip()[-200:]))
    assert not bad, bad


def check_string_root_cause_or_add_comment_rejected():
    bad = []
    for field, value in (("root_cause", "Mislabeling"), ("add_comment", "http://x?comment=")):
        r, _, _ = build([make_card(**{field: value})])
        if r.returncode != 8 or "Traceback" in r.stderr:
            bad.append((field, r.returncode, r.stderr.strip()[-200:]))
    assert not bad, bad


def check_list_family_builds():
    r, _, _ = build([make_card(root_cause={"family": ["Label quality"], "caption": "x"})])
    assert r.returncode == 0 and "Traceback" not in r.stderr, (r.returncode, r.stderr.strip()[-300:])


def check_cards_without_chip_unchanged():
    for family in ("Label quality", "Data gap", "Model behavior", "Split problem"):
        card = make_card(root_cause={"family": family, "caption": "c"}, chips=["2 samples", "label noise"])
        r, _, _ = build([card])
        assert r.returncode == 0, (family, r.stderr)
    r, _, _ = build([make_card(root_cause=None)])
    assert r.returncode == 0, r.stderr
    plain = make_card()
    with_link = copy.deepcopy(plain)
    with_link["add_comment"] = {"link": LINK1}
    r1, page1, txt1 = build([plain])
    r2, page2, txt2 = build([with_link])
    assert r1.returncode == 0 and r2.returncode == 0, (r1.stderr, r2.stderr)
    stripped = re.sub(r'<a class="button"[^>]*>(?:(?!</a>).)*Add comment</a>', "",
                      card_section(page2, "insight-1"), flags=re.S)
    assert stripped == card_section(page1, "insight-1")
    assert [line for line in txt2.splitlines() if "Add comment" not in line] == txt1.splitlines()


def main():
    failed = []
    for name, check in list(globals().items()):
        if not name.startswith("check_"):
            continue
        try:
            check()
            print(f"PASS {name}")
        except Exception as e:
            failed.append(name)
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    if failed:
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
