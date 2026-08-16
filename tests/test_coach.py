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
