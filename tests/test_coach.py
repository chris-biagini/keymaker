"""Tests for km_coach: curriculum, grids, scoring, history rules."""
import pytest

import km_coach as kc


class TestStages:
    def test_six_stages_named(self):
        assert len(kc.STAGES) == 6
        assert kc.STAGES[0]["name"] == "metronome"
        assert kc.STAGES[5]["name"] == "off the grid"

    def test_stage0_empty_stage5_no_hats(self):
        assert kc.STAGES[0]["pattern"] == {}
        assert kc.HAT not in kc.STAGES[5]["pattern"]
        assert kc.STAGES[5]["variance"] is True

    def test_backbeat_pattern(self):
        p = kc.STAGES[2]["pattern"]
        assert p[kc.KICK] == (0, 8, 16, 24)
        assert p[kc.SNARE] == (4, 12, 20, 28)

    def test_pocket_has_eighth_hats(self):
        assert kc.STAGES[3]["pattern"][kc.HAT] == tuple(range(0, 32, 2))


class TestGrid:
    def test_backbeat_at_120bpm(self):
        # sixteenth = 15000/120 = 125 ms
        g = kc.loop_grid_ms(2, 120)
        kicks = [t for i, t in g if i == kc.KICK]
        snares = [t for i, t in g if i == kc.SNARE]
        assert kicks == [0.0, 1000.0, 2000.0, 3000.0]
        assert snares == [500.0, 1500.0, 2500.0, 3500.0]

    def test_sorted_by_time(self):
        g = kc.loop_grid_ms(3, 95)
        assert [t for _, t in g] == sorted(t for _, t in g)

    def test_swing_straight_matches_grid(self):
        straight = kc.loop_grid_ms(3, 120)
        swung50 = kc.loop_grid_ms(4, 120, swing=50)
        assert [t for i, t in swung50 if i == kc.HAT] == \
               [t for i, t in straight if i == kc.HAT]

    def test_swing_67_shifts_offbeat_hats(self):
        # quarter = 500 ms at 120; off-beat hat of first quarter: 0.67*500 = 335
        g = kc.loop_grid_ms(4, 120, swing=67)
        hats = [t for i, t in g if i == kc.HAT]
        assert hats[0] == 0.0
        assert hats[1] == pytest.approx(335.0)
        # on-beat hats never move
        assert 1000.0 in hats and 2000.0 in hats

    def test_only_hats_swing(self):
        g = kc.loop_grid_ms(4, 120, swing=67)
        assert [t for i, t in g if i == kc.SNARE] == [500.0, 1500.0, 2500.0, 3500.0]


class TestScorer:
    def _one(self, hit_at, expect_at=1000.0):
        sc = kc.SessionScorer()
        sc.add_expected(kc.KICK, expect_at)
        return sc.on_hit(kc.KICK, hit_at)

    def test_window_boundaries(self):
        assert self._one(1035.0) == "green"     # +35 inclusive
        assert self._one(1036.0) == "amber"     # late = dragging
        assert self._one(964.0) == "red"        # -36 early = rushing
        assert self._one(1120.0) == "amber"     # still matched at +120
        assert self._one(1121.0) == "stray"     # beyond the miss window
        assert self._one(879.0) == "stray"

    def test_duplicate_hit_is_stray(self):
        sc = kc.SessionScorer()
        sc.add_expected(kc.KICK, 1000.0)
        assert sc.on_hit(kc.KICK, 990.0) == "green"
        assert sc.on_hit(kc.KICK, 1010.0) == "stray"

    def test_nearest_slot_wins(self):
        sc = kc.SessionScorer()
        sc.add_expected(kc.KICK, 1000.0)
        sc.add_expected(kc.KICK, 1158.0)
        assert sc.on_hit(kc.KICK, 1100.0) == "red"      # -58 to the later slot
        assert sc.finalize()["reds"] == 1

    def test_wrong_instrument_is_stray(self):
        sc = kc.SessionScorer()
        sc.add_expected(kc.KICK, 1000.0)
        assert sc.on_hit(kc.HAT, 1000.0) == "stray"

    def test_expire_reports_misses_once(self):
        sc = kc.SessionScorer()
        sc.add_expected(kc.KICK, 1000.0)
        sc.add_expected(kc.SNARE, 1500.0)
        assert sc.expire(1120.0) == []                   # window still open
        assert sc.expire(1121.0) == [(kc.KICK, 1000.0)]
        assert sc.expire(1121.0) == []                   # only reported once
        res = sc.finalize()
        assert res["misses"] == 2

    def test_accuracy(self):
        sc = kc.SessionScorer()
        for i in range(16):
            sc.add_expected(kc.KICK, i * 500.0)
        for i in range(14):
            sc.on_hit(kc.KICK, i * 500.0 + 10.0)         # 14 green
        sc.on_hit(kc.KICK, 14 * 500.0 + 60.0)            # 1 amber
        res = sc.finalize()                              # 1 miss
        assert res["accuracy"] == pytest.approx(14 / 16)
        assert res["score"] == res["accuracy"]

    def test_accuracy_empty_is_zero(self):
        assert kc.SessionScorer().finalize()["accuracy"] == 0.0

    def test_strays_count_against(self):
        sc = kc.SessionScorer()
        sc.add_expected(kc.KICK, 1000.0)
        sc.on_hit(kc.KICK, 1000.0)
        sc.on_hit(kc.KICK, 5000.0)
        assert sc.finalize()["accuracy"] == pytest.approx(0.5)

    def test_live_accuracy(self):
        sc = kc.SessionScorer()
        sc.add_expected(kc.KICK, 1000.0)
        sc.add_expected(kc.KICK, 2000.0)                 # unresolved: excluded
        assert sc.live_accuracy() is None
        sc.on_hit(kc.KICK, 1010.0)
        assert sc.live_accuracy() == pytest.approx(1.0)


class TestVarianceScorer:
    def _session(self, snare_offsets, kick_perfect=True):
        sc = kc.SessionScorer(variance=True)
        for i in range(4):
            sc.add_expected(kc.KICK, i * 1000.0)
            sc.add_expected(kc.SNARE, i * 1000.0 + 500.0)
        if kick_perfect:
            for i in range(4):
                sc.on_hit(kc.KICK, i * 1000.0)
        for i, off in enumerate(snare_offsets):
            sc.on_hit(kc.SNARE, i * 1000.0 + 500.0 + off)
        return sc.finalize()

    def test_steady_drag_scores_one(self):
        res = self._session([60.0, 60.0, 60.0, 60.0])
        assert res["score"] == pytest.approx(1.0)
        assert res["mean_offset"] == pytest.approx(60.0)

    def test_wobbly_drag_penalized(self):
        # offsets 20/100 twice: pstdev = 40 -> variance term 0
        res = self._session([20.0, 100.0, 20.0, 100.0])
        assert res["score"] == pytest.approx(0.0)

    def test_on_grid_fails_gate(self):
        res = self._session([5.0, 5.0, 5.0, 5.0])        # mean < +10: not late
        assert res["score"] == 0.0

    def test_coverage_scales(self):
        res = self._session([60.0, 60.0])                # 2 of 4 snares hit
        assert res["score"] == pytest.approx(0.5)

    def test_bad_kick_caps_score(self):
        res = self._session([60.0] * 4, kick_perfect=False)
        assert res["score"] == 0.0                       # min(kick_acc=0, 1.0)

    def test_snare_never_in_grid_accuracy(self):
        res = self._session([60.0] * 4)
        assert res["accuracy"] == pytest.approx(1.0)     # kick only


def _sess(stage, score, dur=40000):
    return {"stage": stage, "score": score, "duration_ms": dur}


class TestHistory:
    def test_empty_history(self):
        s = kc.summarize([])
        assert s == {"unlocked": 1, "graduated": False, "stages": {},
                     "practice_ms": 0}

    def test_two_sessions_insufficient(self):
        s = kc.summarize([_sess(1, 0.9), _sess(1, 0.9)])
        assert s["unlocked"] == 1

    def test_mean_of_last_three(self):
        s = kc.summarize([_sess(1, 0.5), _sess(1, 0.9), _sess(1, 0.9), _sess(1, 0.9)])
        assert s["unlocked"] == 2                      # last 3 mean 0.9
        s = kc.summarize([_sess(1, 0.9), _sess(1, 0.9), _sess(1, 0.5)])
        assert s["unlocked"] == 1                      # mean ~0.766

    def test_unlocks_chain_but_do_not_skip(self):
        hist = [_sess(2, 0.9)] * 3                     # stage 2 passed...
        assert kc.summarize(hist)["unlocked"] == 1     # ...but stage 1 never was

    def test_graduated(self):
        hist = [_sess(s, 0.9) for s in (1, 2, 3, 4, 5) for _ in range(3)]
        s = kc.summarize(hist)
        assert s["unlocked"] == 5 and s["graduated"] is True

    def test_stage_summaries_and_practice(self):
        s = kc.summarize([_sess(1, 0.7, 1000), _sess(1, 0.95, 2000),
                          _sess(1, 0.8, 3000), _sess(1, 0.85, 4000)])
        st = s["stages"]["1"]
        assert st["best"] == 0.95
        assert st["recent"] == [0.95, 0.8, 0.85]
        assert s["practice_ms"] == 10000

    def test_merge_unlock_extends_host(self):
        host = {"unlocked": 2, "graduated": False,
                "stages": {"2": {"best": 0.9, "recent": [0.9, 0.9]}}}
        u, g = kc.merge_unlock(host, [_sess(2, 0.9)])
        assert (u, g) == (3, False)

    def test_merge_unlock_standalone(self):
        u, g = kc.merge_unlock({}, [_sess(1, 0.9)] * 3)
        assert u == 2

    def test_merge_graduation_local(self):
        host = {"unlocked": 5, "stages": {}}
        u, g = kc.merge_unlock(host, [_sess(5, 0.9)] * 3)
        assert g is True

    def test_unlock_is_monotone_over_history(self):
        hist = [_sess(1, 0.9)] * 3 + [_sess(1, 0.1)] * 5
        assert kc.summarize(hist)["unlocked"] == 2

    def test_graduation_is_sticky(self):
        hist = [_sess(s, 0.9) for s in (1, 2, 3, 4, 5) for _ in range(3)]
        hist += [_sess(5, 0.0)] * 4
        assert kc.summarize(hist)["graduated"] is True

    def test_passing_window_mid_history(self):
        hist = [_sess(1, 0.1), _sess(1, 0.9), _sess(1, 0.9), _sess(1, 0.9),
                _sess(1, 0.1)]
        assert kc.summarize(hist)["unlocked"] == 2

    def test_merge_unlock_clamps_corrupt_host(self):
        assert kc.merge_unlock({"unlocked": 99}, [])[0] == 5


def test_format_results():
    assert kc.format_results({"greens": 14, "ambers": 2, "reds": 1,
                              "misses": 1, "strays": 0}) == "g14 a2 r1 m1 s0"


class TestAckCycle:
    def test_flush_ack_cycle_does_not_double_count(self):
        # session A: finish -> local=[A]; flush -> awaiting=[A]
        a = _sess(1, 0.9)
        local, awaiting = [a], [a]
        host = kc.summarize([a])                     # daemon ack after append
        local = kc.prune_acked(local, awaiting)      # pad prunes on ack
        assert local == []
        # session B: finish while linked
        b = _sess(1, 0.9)
        local.append(b)
        u, _ = kc.merge_unlock(host, local)
        assert u == 1                                 # 2 real sessions: no unlock yet
        host = kc.summarize([a, b])
        local = kc.prune_acked(local, [b])
        c = _sess(1, 0.9)
        local.append(c)
        u, _ = kc.merge_unlock(host, local)
        assert u == 2                                 # 3rd real session unlocks

    def test_prune_acked_removes_each_once(self):
        a, b = _sess(1, 0.9), _sess(1, 0.9)          # equal dicts, distinct objects
        assert kc.prune_acked([a, b], [a]) == [b]
        assert kc.prune_acked([a], [b, b]) == []
        assert kc.prune_acked([], [a]) == []
