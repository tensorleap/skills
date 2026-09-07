"""Checks for sample_ids_from_csv: the failing group is what gets ranked."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tl_api import prefer_rendered, sample_ids_from_csv, unzip_csv

AFF = "aggressor_affinity_score"


def csv_bytes(header, *rows):
    return ("\n".join([",".join(header)] + [",".join(map(str, r)) for r in rows])).encode()


def test_root_members_only():
    # highest affinity is a NON-member: it must not be selected at all
    blob = csv_bytes(["sample_id", AFF, "is_low_perf_root_member"],
                     ["neighbour_1", 0.99, "False"],
                     ["fail_1", 0.80, "True"],
                     ["fail_2", 0.70, "True"],
                     ["neighbour_2", 0.60, "False"])
    ids, cols, rows, ranks = sample_ids_from_csv(blob, None, False, 10)
    assert ids == ["fail_1", "fail_2"], ids
    assert len(rows) == 4, "all rows still returned, for population counts"
    assert AFF in cols
    assert ranks == {"fail_1": 0.80, "fail_2": 0.70}, ranks


def test_k_truncates_after_filtering():
    blob = csv_bytes(["sample_id", AFF, "is_low_perf_root_member"],
                     ["a", 0.9, "True"], ["b", 0.8, "True"], ["c", 0.7, "True"])
    assert sample_ids_from_csv(blob, None, False, 2)[0] == ["a", "b"]


def test_no_root_column_uses_every_row():
    # duplication / other insight types have no membership column
    blob = csv_bytes(["sample_id", AFF], ["a", 0.1], ["b", 0.9])
    assert sample_ids_from_csv(blob, None, False, 10)[0] == ["b", "a"]


def test_ascending_and_explicit_rank_column():
    blob = csv_bytes(["sample_id", "metrics.loss", "is_low_perf_root_member"],
                     ["hi", 9.0, "True"], ["lo", 1.0, "True"])
    assert sample_ids_from_csv(blob, "metrics.loss", False, 10)[0] == ["hi", "lo"]
    assert sample_ids_from_csv(blob, "metrics.loss", True, 10)[0] == ["lo", "hi"]


def test_no_sample_id_column():
    ids, _cols, _rows, _ranks = sample_ids_from_csv(
        csv_bytes([AFF], [0.5]), None, False, 10)
    assert ids is None


def test_missing_rank_values_sort_last_in_both_directions():
    blob = csv_bytes(["sample_id", "metrics.loss"],
                     ["hi", 9.0], ["blank", ""], ["lo", 1.0])
    assert sample_ids_from_csv(blob, None, False, 10)[0] == ["hi", "lo", "blank"]
    assert sample_ids_from_csv(blob, None, True, 10)[0] == ["lo", "hi", "blank"]


def test_unknown_rank_by_falls_back_to_auto():
    blob = csv_bytes(["sample_id", AFF], ["a", 0.1], ["b", 0.9])
    assert sample_ids_from_csv(blob, "metrics.typo", False, 10)[0] == ["b", "a"]


def test_corrupt_zip_returns_none():
    assert unzip_csv(b"not a zip", "x/samples.zip") is None
    assert unzip_csv(b"plain,csv", "x/samples.csv") == b"plain,csv"


def test_rendered_never_displaces_a_better_ranked_sample():
    ids = ["a", "b", "c"]
    ranks = {"a": 0.9, "b": 0.8, "c": 0.7}
    # b is the only rendered one, it must NOT jump over the better-ranked a
    assert prefer_rendered(ids, ranks, {"b"}) == ["a", "b", "c"]


def test_rendered_wins_only_within_a_tie():
    ids = ["a", "b", "c", "d"]
    ranks = {"a": 0.9, "b": 0.8, "c": 0.8, "d": 0.7}
    assert prefer_rendered(ids, ranks, {"c"}) == ["a", "c", "b", "d"]


def test_unranked_ids_prefer_rendered_globally():
    # cluster fallback / csv without a rank column: everything is tied
    assert prefer_rendered(["a", "b", "c"], {}, {"c"}) == ["c", "a", "b"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
    print("all checks passed")
