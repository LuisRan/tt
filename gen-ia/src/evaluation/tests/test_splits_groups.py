from __future__ import annotations

import pytest

from contracts import DocType
from contracts.evaluation import SampleRef, SplitConfig
from src.evaluation.splits import (
    SplitLeakageError,
    assign_splits,
    split_for_group,
    verify_no_leakage,
)


def test_splits_deterministic_and_seed_dependent():
    ids = [f"ine_2016-1-{i}" for i in range(200)]
    a = assign_splits(1, ids)
    assert a == assign_splits(1, ids)
    assert a != assign_splits(2, ids)


def test_splits_proportions():
    ids = [f"curp_2019-5-{i}" for i in range(4000)]
    counts = {"dev": 0, "val": 0, "test": 0}
    for s in assign_splits(5, ids).values():
        counts[s] += 1
    assert abs(counts["dev"] / 4000 - 0.6) < 0.04
    assert abs(counts["val"] / 4000 - 0.2) < 0.04
    assert abs(counts["test"] / 4000 - 0.2) < 0.04


def test_splits_degenerate_and_invalid():
    assert split_for_group(1, "g", SplitConfig(dev=1, val=0, test=0)) == "dev"
    assert split_for_group(1, "g", SplitConfig(dev=0, val=0, test=3)) == "test"
    with pytest.raises(ValueError):
        split_for_group(1, "g", SplitConfig(dev=0, val=0, test=0))


def _ref(sid, group, split):
    return SampleRef(sample_id=sid, group_id=group, split=split, doc_type=DocType.CURP_2019,
                     degradation_preset="none", pdf_path="a", ground_truth_path="b",
                     submitted_pdf_path="a", run_id="r")


def test_splits_no_leakage_detects_crossing():
    ok = [_ref("a-none", "g1", "dev"), _ref("a-heavy", "g1", "dev"), _ref("b-none", "g2", "test")]
    verify_no_leakage(ok)
    with pytest.raises(SplitLeakageError):
        verify_no_leakage(ok + [_ref("a-light", "g1", "val")])


def test_splits_variants_of_group_share_split():
    s = {split_for_group(9, "ine_2016-9-3") for _ in range(3)}
    assert len(s) == 1
