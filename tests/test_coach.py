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
