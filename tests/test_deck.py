import km_deck


def w(wid, ws="mirepoix", n="1 rails"):
    return {"id": wid, "ws": ws, "n": n}


def test_cold_start_preserves_caller_order_not_alphabetical():
    d = km_deck.Deck()
    # "colorhash" sorts before "mirepoix" alphabetically, but km_deck no longer
    # sorts -- the caller decides order (e.g. Hyprland's on-screen workspace
    # order), and km_deck must not silently reorder it.
    d.update([w("tmux:@3", "mirepoix", "2 specs"), w("tmux:@1", "colorhash", "1 lab")])
    assert d.slots == {"tmux:@3": 0, "tmux:@1": 1}


def test_a_caller_supplied_order_is_respected_exactly():
    d = km_deck.Deck()
    d.update([w("tmux:@1", "colorhash", "1 lab"), w("tmux:@3", "mirepoix", "2 specs"),
              w("tmux:@2", "bonsai", "1")])
    assert d.slots == {"tmux:@1": 0, "tmux:@3": 1, "tmux:@2": 2}


def test_a_window_holds_its_slot_when_others_come_and_go():
    d = km_deck.Deck()
    d.update([w("tmux:@1", "a", "1"), w("tmux:@2", "b", "1")])
    assert d.slots["tmux:@2"] == 1
    d.update([w("tmux:@1", "a", "1"), w("tmux:@2", "b", "1"), w("tmux:@9", "a", "2")])
    assert d.slots["tmux:@2"] == 1          # untouched by an unrelated open
    assert d.slots["tmux:@9"] == 2


def test_closing_a_window_ghosts_its_slot():
    d = km_deck.Deck()
    d.update([w("tmux:@1", "a", "1"), w("tmux:@2", "b", "2 specs")])
    d.update([w("tmux:@1", "a", "1")])
    assert "tmux:@2" not in d.slots
    assert d.ghosts[1] == {"ws": "b", "n": "2 specs"}


def test_a_new_window_reclaims_the_lowest_free_slot_ghost_or_not():
    d = km_deck.Deck()
    d.update([w("tmux:@1", "a", "1"), w("tmux:@2", "b", "2"), w("tmux:@3", "c", "3")])
    d.update([w("tmux:@1", "a", "1"), w("tmux:@3", "c", "3")])   # slot 1 ghosts
    d.update([w("tmux:@1", "a", "1"), w("tmux:@3", "c", "3"), w("tmux:@4", "d", "4")])
    assert d.slots["tmux:@4"] == 1          # ghost slot beats the never-used slot 3
    assert 1 not in d.ghosts                # claiming clears the ghost


def test_dismiss_clears_a_ghost_and_reports_whether_it_did():
    d = km_deck.Deck()
    d.update([w("tmux:@1", "a", "1")])
    d.update([])
    assert d.dismiss(0) is True
    assert d.ghosts == {}
    assert d.dismiss(0) is False            # nothing there now
    assert d.dismiss(11) is False


def test_restored_slots_are_honoured_and_dead_ids_dropped():
    d = km_deck.Deck({"tmux:@1": 5, "tmux:@99": 2})
    d.update([w("tmux:@1", "a", "1"), w("tmux:@7", "b", "1")])
    assert d.slots["tmux:@1"] == 5          # restored position survives
    assert d.slots["tmux:@7"] == 0          # lowest free
    assert "tmux:@99" not in d.slots        # gone at first update
    assert d.ghosts == {}                   # a restart never fabricates ghosts


def test_beyond_twelve_windows_keep_allocating_into_later_pages():
    d = km_deck.Deck()
    d.update([w("tmux:@%d" % i, "a", str(i)) for i in range(20)])
    assert sorted(d.slots.values()) == list(range(20))


import json
import km_proto


def test_page_count_is_one_when_empty_and_grows_by_twelve():
    d = km_deck.Deck()
    assert d.page_count() == 1
    d.update([w("tmux:@%d" % i, "a", str(i)) for i in range(12)])
    assert d.page_count() == 1
    d.update([w("tmux:@%d" % i, "a", str(i)) for i in range(13)])
    assert d.page_count() == 2


def test_message_carries_only_the_current_page_with_ws_by_reference():
    d = km_deck.Deck()
    # Caller order is preserved (km_deck no longer sorts), so "mirepoix" --
    # listed first -- claims slot 0 and is the first ws entry.
    d.update([w("tmux:@1", "mirepoix", "1 rails"), w("tmux:@2", "colorhash", "1 lab")])
    m = d.message(page=0, colors={"mirepoix": "e16000", "colorhash": "6d0a9e"})
    assert m["t"] == "deck"
    assert m["ws"] == [["mirepoix", "e16000"], ["colorhash", "6d0a9e"]]
    assert m["slots"] == [{"i": 0, "c": 0, "n": "1 rails", "s": "live"},
                          {"i": 1, "c": 1, "n": "1 lab", "s": "live"}]
    assert m["pages"] == 1 and m["page"] == 0


def test_message_no_longer_carries_a_knob_mode():
    # The knob has exactly one mode now (paging), so a mode field on the wire
    # would be a constant the pad has to branch on for no reason.
    d = km_deck.Deck()
    d.update([{"id": "tmux:@1", "ws": "a", "n": "1 sh"}])
    msg = d.message(0, {"a": "ff0000"})
    assert "knob" not in msg


def test_message_marks_focused_bell_and_ghost_states():
    d = km_deck.Deck()
    d.update([w("tmux:@1", "a", "1"), w("tmux:@2", "a", "2"), w("tmux:@3", "a", "3")])
    d.update([w("tmux:@1", "a", "1"), w("tmux:@2", "a", "2")])   # @3 ghosts at slot 2
    m = d.message(page=0, colors={"a": "ffffff"},
                  focused="tmux:@1", bells=["tmux:@2"])
    states = {s["i"]: s["s"] for s in m["slots"]}
    assert states == {0: "focused", 1: "bell", 2: "ghost"}


def test_bells_are_global_slot_numbers_so_offpage_alerts_survive():
    d = km_deck.Deck()
    d.update([w("tmux:@%d" % i, "a", "%02d" % i) for i in range(20)])
    m = d.message(page=0, colors={"a": "ffffff"}, bells=["tmux:@15"])
    assert m["bells"] == [15]                  # slot 15 lives on page 1, not shown
    assert all(s["i"] < 12 for s in m["slots"])
    # `map` is a per-page BITMASK, not a count: page 0 has all 12 slots
    # occupied (bits 0-11 set), page 1 has slots 0-7 of its own range occupied.
    assert m["map"] == [0xFFF, 0xFF]


def test_map_is_a_bitmask_not_a_count_so_sparse_pages_agree_with_bells():
    # I3: a count drew the first N cells of a page while bells draw at the
    # slot's ACTUAL position -- disagreeing whenever occupied slots aren't a
    # contiguous run from 0, which is routine (a slot restored at a high
    # number, a dismissed mid-page ghost). Slots 0 and 7 occupied, matching
    # the finding's own example: a count-based map would show TWO filled
    # cells at positions 0 and 1 -- neither of which is slot 7, where the
    # bell actually lives.
    d = km_deck.Deck({"tmux:@1": 0, "tmux:@2": 7})
    d.update([w("tmux:@1", "a", "1"), w("tmux:@2", "a", "2")])
    m = d.message(page=0, colors={"a": "ffffff"}, bells=["tmux:@2"])
    assert m["map"] == [0b10000001]             # bits 0 and 7 set, nothing between
    assert m["bells"] == [7]


def test_names_are_trimmed_so_the_wire_stays_under_the_codec_cap():
    # Workspace dedup saves nothing when every slot has a distinct workspace.
    # Sweep window counts from 12 to 400 with every window ringing to stress the
    # wire format. A single-workspace message cannot expose the original defect
    # (workspace names unbounded), so do not substitute a simpler version.
    peak_bytes = 0
    for win_count in [12, 24, 60, 120, 240, 400]:
        d = km_deck.Deck()
        windows = [w("tmux:@%d" % i, "workspace-%d-very-long-name-string" % (i % 20),
                     "%d some-long-window-name-here" % i)
                   for i in range(win_count)]
        d.update(windows)
        m = d.message(page=0,
                      colors={("workspace-%d-very-long-name-string" % i): "e16000" for i in range(20)},
                      bells=["tmux:@%d" % i for i in range(win_count)])
        # Workspace names are trimmed to ws_max (default 12).
        assert all(len(ws[0]) <= 12 for ws in m["ws"]), "workspace names must be trimmed"
        # Map and bells are bounded by minimap geometry. `map` is a per-page
        # bitmask (12 bits) now, not a count -- still at most 4 decimal digits
        # (0-4095) on the wire, so it does not regress the size this test
        # exists to guard.
        assert len(m["map"]) <= 5, "map must fit on screen (MINIMAP_MAX_PAGES=5)"
        assert all(0 <= mv <= 0xFFF for mv in m["map"]), "map entries are 12-bit masks"
        assert all(s < 60 for s in m["bells"]), "all bells must be within drawable pages"
        encoded = km_proto.encode(m)
        peak_bytes = max(peak_bytes, len(encoded))
        assert len(encoded) < 2048, "deck message would be DISCARDED by LineCodec"


def test_minimap_geometry_lays_pages_out_left_to_right():
    boxes = km_deck.minimap_boxes(3)
    assert boxes == [(1, 38, 15, 20), (20, 38, 15, 20), (39, 38, 15, 20)]
    assert km_deck.minimap_cell(0, boxes[0]) == (3, 41)
    assert km_deck.minimap_cell(4, boxes[0]) == (7, 45)      # row 1, col 1
    assert km_deck.minimap_cell(12, boxes[1]) == (22, 41)    # slot 12 -> page 1, key 0


def test_minimap_pixels_is_empty_when_there_are_no_pages():
    # idle_card clears the strip through this path; it must produce a frame the
    # diff painter can subtract down to a blank bitmap.
    assert km_deck.minimap_pixels(0, 0, (), (), False) == set()


def test_minimap_pixels_outlines_only_the_page_on_the_keys():
    lit = km_deck.minimap_pixels(3, 1, (0, 0, 0), (), False)
    for p, (x, y, w, h) in enumerate(km_deck.minimap_boxes(3, y=0)):
        assert ((x, y) in lit) is (p == 1), "page %d outline wrong" % p


def test_minimap_pixels_draws_a_cell_only_where_the_mask_claims_one():
    lit = km_deck.minimap_pixels(1, 0, (0b101,), (), False)   # bits 0 and 2
    box = km_deck.minimap_boxes(1, y=0)[0]
    assert km_deck.minimap_cell(0, box) in lit
    assert km_deck.minimap_cell(2, box) in lit
    assert km_deck.minimap_cell(1, box) not in lit


def test_minimap_pixels_bells_only_light_on_the_blink_phase():
    box = km_deck.minimap_boxes(1, y=0)[0]
    cx, cy = km_deck.minimap_cell(5, box)
    on = km_deck.minimap_pixels(1, 0, (0,), (5,), True)
    off = km_deck.minimap_pixels(1, 0, (0,), (5,), False)
    # 3x3 for a bell vs 2x2 for a plain cell: the far corner is bell-only.
    assert (cx + 2, cy + 2) in on
    assert (cx + 2, cy + 2) not in off


def test_minimap_pixels_ignores_bells_on_pages_it_cannot_draw():
    # The keys reach every page; the strip only draws MINIMAP_MAX_PAGES. A bell
    # past the drawable range must not be painted onto some other page's box.
    # (page 0 is on the keys, so its outline is lit either way -- the claim is
    # that the undrawable bell adds nothing, not that the frame is blank.)
    assert (km_deck.minimap_pixels(1, 0, (0,), (99,), True)
            == km_deck.minimap_pixels(1, 0, (0,), (), True))


def test_minimap_pixels_matches_a_reference_clear_then_repaint():
    # The diff painter is only correct if the frame it computes equals what the
    # old fill(0)-then-draw produced. That old routine, reproduced as an oracle.
    pages, page, masks, bells, blink = 3, 1, (0b1011, 0b1, 0), (13,), True
    ref = set()
    boxes = km_deck.minimap_boxes(pages, y=0)
    for p, (x, y, w, h) in enumerate(boxes):
        if p == page:
            for dx in range(w):
                ref.add((x + dx, y))
                ref.add((x + dx, y + h - 1))
            for dy in range(h):
                ref.add((x, y + dy))
                ref.add((x + w - 1, y + dy))
        mask = masks[p] if p < len(masks) else 0
        for i in range(km_deck.SLOTS_PER_PAGE):
            if not mask >> i & 1:
                continue
            cx, cy = km_deck.minimap_cell(p * km_deck.SLOTS_PER_PAGE + i, (x, y, w, h))
            for dx in range(2):
                for dy in range(2):
                    ref.add((cx + dx, cy + dy))
    for gslot in bells:
        cx, cy = km_deck.minimap_cell(gslot, boxes[gslot // km_deck.SLOTS_PER_PAGE])
        for dx in range(3):
            for dy in range(3):
                ref.add((cx + dx, cy + dy))
    assert km_deck.minimap_pixels(pages, page, masks, bells, blink) == ref
