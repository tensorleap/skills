import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def make_export(root, relative=False):
    def w(rel, text):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return rel if relative else path

    pop = w("population.csv", "sample_id,metrics.loss,metadata.fog\ntraining_1,2.5,yes\ntraining_2,1.5,yes\ntraining_3,0.1,no\n")
    csv1 = w("insight_1_low_performance/samples.csv",
             "sample_id,metrics.loss,metadata.fog\ntraining_1,2.5,yes\ntraining_2,1.5,yes\n")
    csv2 = w("insight_1_low_performance/sub_2_low_performance/samples.csv",
             "sample_id,metrics.loss\ntraining_1,2.5\n")
    csv4 = w("insight_4_duplication/samples.csv", "sample_id,metrics.loss\ntraining_1,2.5\ntraining_9,0.2\n")
    fix = w("insight_1_low_performance/fixing_samples.csv", "sample_id\nunlabeled_7\n")
    payload = w("insight_1_low_performance/samples/training_1/image/vis/payload.json", "{}")
    code = w("integration/leap_integration.py", "x = 1\n")
    manifest = {
        "dir": root, "projectId": "p", "versionId": "v", "version": "model", "insightsPanelLink": "http://ui/p",
        "classLabels": {"classes": ["cat", "dog"]}, "visualizers": [{"name": "Image", "type": "Image", "argNames": ["data"]}],
        "populationCsv": pop, "integrationDir": os.path.join(root, "integration"), "entryFile": "leap_integration.py",
        "skipped": [], "notes": [],
        "insights": [
            {"index": 1, "type": "low_performance", "name": "Failure Mode", "status": "InReview", "dir": os.path.join(root, "insight_1_low_performance"),
             "samplesCsv": csv1, "fixingCsv": fix, "link": "http://ui/i1", "createTestLink": "http://ui/i1?addTestFromInsight=c1",
             "summary": {"groupSize": 2, "csvRows": 2, "rankedBy": "metrics.loss"}, "engine": {"n_samples": 2, "is_train_aggressor": True},
             "samples": [{"id": "training_1", "rank": 1, "rendered": True, "files": [payload]}, {"id": "training_2", "rank": 2, "rendered": False},
                         {"id": "training_3", "rank": 3, "rendered": True}]},
            {"index": 2, "type": "low_performance", "name": "Failure Mode", "parentIndex": 1, "dir": os.path.join(root, "insight_1_low_performance", "sub_2_low_performance"),
             "samplesCsv": csv2, "link": "http://ui/i2", "summary": {"groupSize": 1, "csvRows": 1}, "engine": {"n_samples": 1}, "samples": []},
            {"index": 4, "type": "duplication", "name": "Duplication", "dir": os.path.join(root, "insight_4_duplication"),
             "samplesCsv": csv4, "link": "http://ui/i4", "summary": {"groupSize": 2, "csvRows": 2}, "engine": {"n_samples": 2}, "samples": []},
        ],
    }
    w("manifest.json", json.dumps(manifest))


def main():
    root = tempfile.mkdtemp()
    make_export(root)
    out = subprocess.run([sys.executable, os.path.join(HERE, "tl_api.py"), "digest", root], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    d = json.load(open(os.path.join(root, "insights.json")))
    assert d["links"]["insights_panel"] == "http://ui/p" and d["prediction_labels"]["classes"] == ["cat", "dog"]
    assert d["integration"] == {"dir": "integration", "entry_file": "leap_integration.py"}
    assert d["population_metrics"] == {"metrics.loss": 1.3667} and d["population_metadata"]["metadata.fog"] == {"yes": 0.6667, "no": 0.3333}
    assert [i["index"] for i in d["insights"]] == [1, 4], "sub-insight must nest under its parent"
    p = d["insights"][0]
    assert p["subinsights"][0]["index"] == 2 and p["dir"] == "insight_1_low_performance"
    assert p["files"] == {"csv": "insight_1_low_performance/samples.csv", "fixing_csv": "insight_1_low_performance/fixing_samples.csv"}
    assert p["add_test_link"].endswith("addTestFromInsight=c1") and p["deep_link"] == "http://ui/i1"
    assert p["insightType"] == {"n_samples": 2, "is_train_aggressor": True, "type": "low_performance"}
    assert p["population"] == {"samples": 2, "csv_rows": 2} and p["csv_columns"][0] == "sample_id"
    assert p["samples"]["training_1"]["files"] == ["insight_1_low_performance/samples/training_1/image/vis/payload.json"]
    assert p["samples"]["training_1"]["rank"] == 1
    assert p["samples"]["training_2"] == {"rank": 2, "files": [], "missing_visualization": True}
    assert p["samples"]["training_3"]["missing_visualization"], "a sample whose downloads failed has nothing to show"
    assert p["overlaps"] == [{"insight": 4, "shared": 1, "of_this": 0.5}]
    assert d["counts"] == {"total": 3, "parents": 2, "subinsights": 1, "samples_with_visualizations": 1, "samples_missing_visualizations": 2}
    summ = subprocess.run([sys.executable, os.path.join(HERE, "tl_api.py"), "summarize", root], capture_output=True, text=True)
    rows = json.loads(summ.stdout)
    assert [(r["insight"], r["parent_index"], r["group_rows"]) for r in rows] == [(1, None, 2), (2, 1, 1), (4, None, 2)], rows
    assert rows[0]["metrics"]["metrics.loss"] == {"group_mean": 2.0, "all_data_mean": 1.3667}
    moved_from = os.path.join(tempfile.mkdtemp(), "export")
    make_export(moved_from, relative=True)
    moved = moved_from + "-moved"
    os.rename(moved_from, moved)
    assert subprocess.run([sys.executable, os.path.join(HERE, "tl_api.py"), "digest", moved], capture_output=True).returncode == 0
    m = json.load(open(os.path.join(moved, "insights.json")))
    assert m["insights"][0]["csv_columns"][0] == "sample_id" and m["population_metrics"], "a moved export must still resolve"
    assert m["insights"][0]["errors"] == []
    os.remove(os.path.join(moved, "insight_4_duplication", "samples.csv"))
    subprocess.run([sys.executable, os.path.join(HERE, "tl_api.py"), "digest", moved], capture_output=True)
    m = json.load(open(os.path.join(moved, "insights.json")))
    assert "missing" in m["insights"][1]["errors"][0]
    empty = tempfile.mkdtemp()
    assert subprocess.run([sys.executable, os.path.join(HERE, "tl_api.py"), "digest", empty], capture_output=True).returncode == 5
    print("all checks passed")


if __name__ == "__main__":
    main()
