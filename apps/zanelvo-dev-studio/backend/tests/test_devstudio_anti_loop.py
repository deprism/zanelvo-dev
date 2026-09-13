"""Deterministic tests for the anti-loop engine's pure functions: failure normalization,
fingerprinting, classification, and LoopDetector pattern tracking. No DB/network."""
from app.devstudio.services import anti_loop


def test_normalize_strips_timestamps_uuids_paths_and_numbers():
    raw_a = ("2025-01-01T10:00:00.123Z ERROR request_id=zanelvo-agent-lab "
             "at /tmp/pytest-of-root/pytest-42/test0/file.py:17:3 connection refused on port 54231")
    raw_b = ("2026-09-10T22:11:05Z ERROR request_id=zanelvo-agent-lab "
             "at /tmp/pytest-of-root/pytest-99/test5/file.py:88:9 connection refused on port 61234")
    assert anti_loop.normalize_error(raw_a) == anti_loop.normalize_error(raw_b)


def test_normalize_does_not_erase_the_actual_error_signal():
    a = anti_loop.normalize_error("AssertionError: expected 200 got 500 at line 10")
    b = anti_loop.normalize_error("ModuleNotFoundError: No module named 'foo' at line 10")
    assert a != b


def test_classify_failure_buckets():
    assert anti_loop.classify_failure("pytest.TimeoutError: timed out after 30s") == "timeout"
    assert anti_loop.classify_failure("AssertionError: assert 1 == 2") == "assertion"
    assert anti_loop.classify_failure("SyntaxError: unexpected token") == "syntax"
    assert anti_loop.classify_failure("ModuleNotFoundError: No module named 'x'") == "missing_dependency"
    assert anti_loop.classify_failure("TypeError: NoneType is not callable") == "type_error"
    assert anti_loop.classify_failure("connection refused by remote host") == "connection"
    assert anti_loop.classify_failure("ECONNREFUSED 127.0.0.1:5432") == "network"
    assert anti_loop.classify_failure("random unclassified explosion") == "other"


def test_same_underlying_failure_fingerprints_identically_across_superficial_noise():
    fp1 = anti_loop.build_fingerprint(
        "2025-01-01T00:00:00Z AssertionError: expected /tmp/abc123/x to equal 5, got 4 (line 12:3)",
        command="pytest tests/test_x.py", subsystem="backend")
    fp2 = anti_loop.build_fingerprint(
        "2026-06-06T12:34:56Z AssertionError: expected /tmp/xyz789/x to equal 5, got 4 (line 99:1)",
        command="pytest tests/test_x.py", subsystem="backend")
    assert fp1["fingerprint"] == fp2["fingerprint"]


def test_different_failures_fingerprint_differently():
    fp1 = anti_loop.build_fingerprint("AssertionError: expected 5 got 4", command="pytest a", subsystem="backend")
    fp2 = anti_loop.build_fingerprint("TypeError: cannot read property of undefined", command="npm test",
                                        subsystem="frontend")
    assert fp1["fingerprint"] != fp2["fingerprint"]


def test_budget_presets_match_spec():
    assert anti_loop.ORCHESTRATION_BUDGETS == {"FAST": 4, "BALANCED": 8, "DEEP": 15}
    assert anti_loop.budget_for_preset("FAST") == 4
    assert anti_loop.budget_for_preset("UNKNOWN_PRESET") == 8  # falls back to BALANCED


def test_budget_exhausted():
    assert not anti_loop.budget_exhausted(3, 8)
    assert anti_loop.budget_exhausted(8, 8)
    assert anti_loop.budget_exhausted(9, 8)


def test_loop_detector_flags_identical_consecutive_diffs():
    det = anti_loop.LoopDetector()
    assert det.record_diff("diff --git a/x.py b/x.py\n+foo") is False  # first time, no prior diff
    assert det.record_diff("diff --git a/x.py b/x.py\n+foo") is True   # same diff again = no progress
    assert det.record_diff("diff --git a/y.py b/y.py\n+bar") is False  # different diff = progress


def test_loop_detector_tracks_command_repeat_streaks():
    det = anti_loop.LoopDetector()
    assert det.record_command("pytest -q") == 1
    assert det.record_command("pytest -q") == 2
    assert det.record_command("npm test") == 1
    assert det.record_command("pytest -q") == 1  # streak reset by the intervening different command


def test_loop_detector_flags_circular_handoffs():
    det = anti_loop.LoopDetector()
    det.record_handoff("backend")
    det.record_handoff("frontend")
    assert det.record_handoff("backend") is False  # only 3 entries so far
    assert det.record_handoff("frontend") is True  # backend->frontend->backend->frontend
