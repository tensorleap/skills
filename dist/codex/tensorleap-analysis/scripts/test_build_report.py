import copy
import html
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LINK1 = "http://ui/i1?dashboard=d&state=s&addCommentToInsight=c1&comment="


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


def make_digest(statuses):
    return {"projectId": "p", "api_base": "http://srv/api/v2",
            "insights": [{"index": i, "cid": f"c{i}", "status": status} if status else {"index": i}
                         for i, status in statuses.items()]}


def build(cards, group_type="Failure Mode", digest=None):
    root = tempfile.mkdtemp()
    with open(os.path.join(root, "report.json"), "w") as f:
        json.dump(make_report(cards, group_type), f)
    if digest:
        with open(os.path.join(root, "insights.json"), "w") as f:
            json.dump(digest, f)
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


ACTION = (r'<a class="button act" href="([^"]*)" target="_blank" rel="noopener" '
          r'data-tl-action="([^"]*)"[^>]*>(.*?)</a>')


def header_buttons(page, card_id):
    head = re.search(r'<div class="chead">(.*?)</div>', card_section(page, card_id), re.S).group(1)
    assert head.count("<a ") == len(re.findall(ACTION, head, re.S)), "every card button is an action anchor"
    return [(html.unescape(href), re.sub(r"<[^>]+>", "", inner).strip(), json.loads(html.unescape(action)))
            for href, action, inner in re.findall(ACTION, head, re.S)]


def labels(page, card_id):
    return [label for _, label, _ in header_buttons(page, card_id)]


def card_actions(page, card_id):
    return [(html.unescape(href), re.sub(r"<[^>]+>", "", inner).strip(), json.loads(html.unescape(action)))
            for href, action, inner in re.findall(ACTION, card_section(page, card_id), re.S)]


def review_state(page):
    return json.loads(re.search(r'<script type="application/json" id="tl-review">([^<]*)</script>', page).group(1))


def chips(page, card_id):
    block = re.search(r'<div class="chips">(.*?)</div>', card_section(page, card_id), re.S).group(1)
    return [html.unescape(c) for c in re.findall(r'<span class="chip">([^<]*)</span>', block)]


def check_add_comment_is_accepted_but_not_a_report_button():
    r, page, txt = build([make_card(add_comment={"link": LINK1})])
    assert r.returncode == 0, r.stderr
    assert labels(page, "insight-1") == ["Open proof"], labels(page, "insight-1")
    assert "Add comment" not in page and "Add comment" not in txt


def check_sub_insight_card_targets_its_own_insight():
    parent = make_card()
    sub = make_card(id="insight-1-sub-2", heading="Foggy dogs", add_test=None)
    r, page, _ = build([parent, sub], digest=make_digest({1: "InReview", 2: "InReview"}))
    assert r.returncode == 0, r.stderr
    for card_id, cid in (("insight-1", "c1"), ("insight-1-sub-2", "c2")):
        buttons = header_buttons(page, card_id)
        assert [label for _, label, _ in buttons] == ["Open proof", "Acknowledge"], (card_id, buttons)
        assert {action["insightId"] for _, _, action in buttons} == {cid}, (card_id, buttons)


def check_test_action_sits_with_the_action_items():
    r, page, txt = build([make_card(do_next=["Relabel the 2 samples."])], digest=make_digest({1: "InReview"}))
    assert r.returncode == 0, r.stderr
    donext = re.search(r'<div class="donext">.*?</div></div>', card_section(page, "insight-1"), re.S).group(0)
    assert "<li>Relabel the 2 samples.</li>" in donext
    assert [(label, action["type"]) for _, label, action in card_actions(page, "insight-1")] == [
        ("Open proof", "open"), ("Acknowledge", "decide"), ("Add the regression test", "test")]
    assert 'data-act="test"' in donext and "[Do next button] Add the regression test" in txt


def check_hand_edited_comment_link_rejected():
    r, _, _ = build([make_card(id="insight-7", add_comment={"link": LINK1 + "Night%20dogs"})])
    assert r.returncode == 8, (r.returncode, r.stderr)
    assert "insight-7" in r.stderr, r.stderr


def check_no_button_without_link():
    r, page, txt = build([make_card()])
    assert r.returncode == 0, r.stderr
    assert labels(page, "insight-1") == ["Open proof"], labels(page, "insight-1")
    assert [label for _, label, _ in card_actions(page, "insight-1")] == ["Open proof", "Add the regression test"]
    assert "Add comment" not in page and "Add comment" not in txt
    assert "[header button] Open proof" in txt and "Analyze insight" not in page + txt
    r, page, _ = build([make_card(add_test=None, explore=None)])
    assert r.returncode == 0 and card_actions(page, "insight-1") == [] and 'class="donext"' not in page


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
    assert card_section(page2, "insight-1") == card_section(page1, "insight-1") and txt2 == txt1


def check_state_is_v2_with_decisions():
    cards = [make_card(id=f"insight-{i}") for i in (1, 2, 3, 4)]
    r, page, _ = build(cards, digest=make_digest({1: "InReview", 2: "Approved", 3: "Archived", 4: None}))
    assert r.returncode == 0, r.stderr
    state = review_state(page)
    assert re.fullmatch(r"[0-9a-f]{32}", state.pop("reportId")), state
    assert state == {"v": 2, "readonly": False, "approved": ["insight-2"], "savedAt": "",
                     "decisions": {"insight-1": {"status": "InReview"}, "insight-2": {"status": "Approved"},
                                   "insight-3": {"status": "Archived"}},
                     "sync": {"apiBase": "http://srv/api/v2", "projectId": "p",
                              "insights": {"insight-1": "c1", "insight-2": "c2", "insight-3": "c3"}}}, state


def check_actions_carry_payload_and_fallback_link():
    heading = "Night-time dogs are labelled cat"
    link = "http://ui/i?version=v&insight=c1"
    card = make_card(explore={"link": link, "text": "open"})
    r, page, _ = build([card], digest=make_digest({1: "InReview"}))
    assert r.returncode == 0, r.stderr
    common = {"card": "insight-1", "insightId": "c1", "title": heading}
    assert header_buttons(page, "insight-1") == [
        (link, "Open proof", dict(common, type="open")),
        (link + "&decide=acknowledge&decideInsight=c1", "Acknowledge",
         dict(common, type="decide", decision="acknowledge")),
    ], header_buttons(page, "insight-1")
    assert card_actions(page, "insight-1")[2:] == [
        ("http://ui/i1?addTestFromInsight=c1", "Add the regression test", dict(common, type="test")),
    ], card_actions(page, "insight-1")


def check_decide_button_follows_status():
    cards = [make_card(id=f"insight-{i}") for i in (1, 2, 3)]
    r, page, _ = build(cards, digest=make_digest({1: "InReview", 2: "Approved", 3: "Archived"}))
    assert r.returncode == 0, r.stderr
    for card_id, label, decision in (("insight-1", "Acknowledge", "acknowledge"),
                                     ("insight-2", "Back to review", "reopen"),
                                     ("insight-3", "Back to review", "reopen")):
        decide = [b for b in header_buttons(page, card_id) if b[2]["type"] == "decide"]
        assert decide == [(f"http://ui/i1?decide={decision}&decideInsight=c{card_id[-1]}", label,
                           {"type": "decide", "card": card_id, "insightId": "c" + card_id[-1],
                            "title": "Night-time dogs are labelled cat", "decision": decision})], decide


def check_no_decide_without_insight_id_or_explore():
    cards = [make_card(id="insight-1", explore=None), make_card(id="insight-4")]
    r, page, _ = build(cards, digest=make_digest({1: "InReview", 4: None}))
    assert r.returncode == 0, r.stderr
    assert labels(page, "insight-4") == ["Open proof"], labels(page, "insight-4")
    assert not any("insightId" in action for _, _, action in card_actions(page, "insight-4"))
    assert [(label, action["insightId"]) for _, label, action in card_actions(page, "insight-1")] == [
        ("Add the regression test", "c1")]
    r, page, _ = build([make_card()])
    assert r.returncode == 0, r.stderr
    assert labels(page, "insight-1") == ["Open proof"], labels(page, "insight-1")
    state = review_state(page)
    assert state["v"] == 2 and state["decisions"] == {} and "sync" not in state, state


def check_heading_with_quotes_and_markup_is_escaped():
    heading = "Dogs \"at night\" <b>read</b> as 'cats' & <script>alert(1)</script>"
    r, page, _ = build([make_card(heading=heading)], digest=make_digest({1: "InReview"}))
    assert r.returncode == 0, r.stderr
    section = card_section(page, "insight-1")
    assert "<b>read" not in section and "<script" not in section, "the heading is text everywhere"
    raw = re.findall(r'data-tl-action="([^"]*)"', section)
    assert len(raw) == 3 and not any(c in value for value in raw for c in "<>'"), raw
    buttons = card_actions(page, "insight-1")
    assert [action["title"] for _, _, action in buttons] == [heading] * 3, buttons
    assert f"</span>{html.escape(heading)}</h3>" in section


def check_report_names_actions_and_does_nothing_itself():
    r, page, _ = build([make_card()], digest=make_digest({1: "InReview"}))
    assert r.returncode == 0, r.stderr
    body = page.split("<body>")[1]
    script = body.rsplit("<script>", 1)[1]
    for gone in ("fetch(", "XMLHttpRequest", "localStorage", "reportApproval"):
        assert gone not in script, gone
    for message in ("tl-report-ready", "tl-report-host", "tl-report-action", "tl-report-decisions"):
        assert f'"{message}"' in script, message
    assert not re.search(r"\bApprove\b", body), "no Approve toggle"
    assert body.count("<button") == 1 and '<button type="button" class="share"' in body, "Share is the only button"
    assert "readonly: true" in script and "sync" not in script.split("readonly: true")[1].split("};")[0], \
        "the shared copy is read-only and carries no server address"
    assert "html[data-ro] a.act, html[data-ro] button.share { display: none; }" in page
    assert '<div class="review" hidden>' in body and '<div class="topbar" hidden>' in body
    assert body.count('<td class="stcell"></td>') == 1, "each findings row has a status cell the script fills"
    assert body.index('id="tl-review">') < body.index('getElementById("tl-review")'), "state block precedes the script"


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
