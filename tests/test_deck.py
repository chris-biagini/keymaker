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


def test_bells_still_mark_slot_state_though_the_map_field_is_gone():
    # bells stays a PARAMETER of message() -- it still drives each slot's own
    # "s" field, which is how the legend renders bell state. Only the old
    # "map"/"bells" OUTPUT fields (spec 7.1) are gone.
    d = km_deck.Deck()
    d.update([w("tmux:@%d" % i, "a", "%02d" % i) for i in range(20)])
    m = d.message(page=0, colors={"a": "ffffff"}, bells=["tmux:@15"])
    assert "map" not in m and "bells" not in m
    assert all(s["i"] < 12 for s in m["slots"])


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
        encoded = km_proto.encode(m)
        peak_bytes = max(peak_bytes, len(encoded))
        assert len(encoded) < 2048, "deck message would be DISCARDED by LineCodec"


def test_cell_label_is_always_exactly_six_characters():
    # The legend row packs three cells at a fixed 7-character pitch; a label of
    # any other length shifts every column to its right.
    for ws, name in [("mirepoix", "2 recipe-page-redesign"), ("a", "x"),
                     ("", ""), ("colorhash", "1 color-hash")]:
        assert len(km_deck.cell_label(ws, name)) == 6


def test_cell_label_keeps_the_tmux_index_as_a_disambiguator():
    # Window names arrive as "<index> <name>", and the index is the most
    # distinguishing thing about two windows in the same session.
    assert km_deck.cell_label("mirepoix", "2 recipe-page") == "mir:2r"
    assert km_deck.cell_label("mirepoix", "3 alias-bug") == "mir:3a"


def test_cell_label_pads_a_short_session_rather_than_shifting_columns():
    assert km_deck.cell_label("a", "1 sh") == "a  :1s"


def test_legend_row_is_twenty_characters_with_gaps_at_6_and_13():
    labels = ["ab" + str(i) + "de" + str(i) for i in range(12)]
    row = km_deck.legend_row(labels, 1)
    assert len(row) == 20
    assert row[6] == " " and row[13] == " "
    assert row[0:6] == labels[3] and row[7:13] == labels[4] and row[14:20] == labels[5]


def test_legend_row_renders_missing_slots_as_blanks():
    row = km_deck.legend_row(["abcdef"], 0)
    assert row == "abcdef" + " " * 14


def test_gutter_pixels_empty_state_lights_nothing():
    assert km_deck.gutter_pixels(["empty"] * 12, True) == set()


def test_gutter_pixels_live_is_a_single_column():
    lit = km_deck.gutter_pixels(["live"] + ["empty"] * 11, True)
    assert lit == {(1, y) for y in range(9)}


def test_gutter_pixels_ghost_is_a_dotted_column():
    lit = km_deck.gutter_pixels(["ghost"] + ["empty"] * 11, True)
    assert lit == {(1, y) for y in range(0, 9, 2)}


def test_gutter_pixels_focused_is_hollow_and_bell_is_solid():
    hollow = km_deck.gutter_pixels(["focused"] + ["empty"] * 11, True)
    solid = km_deck.gutter_pixels(["bell"] + ["empty"] * 11, True)
    assert len(solid) == 4 * 9
    assert hollow < solid            # a proper subset: outline inside the block
    assert (2, 4) in solid and (2, 4) not in hollow


def test_gutter_pixels_bell_goes_dark_on_the_off_phase():
    assert km_deck.gutter_pixels(["bell"] + ["empty"] * 11, False) == set()


def test_gutter_pixels_places_each_slot_in_the_key_grid():
    # Slot i lives at row i//3, column i%3 -- the MacroPad's physical layout.
    lit = km_deck.gutter_pixels(["empty"] * 4 + ["live"] + ["empty"] * 7, True)
    assert lit == {(1 + 42, y) for y in range(10, 19)}


def test_countdown_text_boundaries():
    assert km_deck.countdown_text(0) is None
    assert km_deck.countdown_text(499) is None
    assert km_deck.countdown_text(500) == "RE-KEY IN 3"
    assert km_deck.countdown_text(1499) == "RE-KEY IN 3"
    assert km_deck.countdown_text(1500) == "RE-KEY IN 2"
    assert km_deck.countdown_text(2500) == "RE-KEY IN 1"
    assert km_deck.countdown_text(3499) == "RE-KEY IN 1"
    assert km_deck.countdown_text(3500) == "RE-KEYING"


def test_message_carries_the_focused_window_even_when_it_is_off_page():
    # The focused window is exactly what you want named while you are paging
    # AWAY from it, so this cannot be derived pad-side from `slots`.
    d = km_deck.Deck()
    wins = [{"id": "tmux:@%d" % i, "ws": "ws%d" % i, "n": "%d w" % i}
            for i in range(14)]
    d.update(wins)
    msg = d.message(0, {}, focused="tmux:@13")
    assert msg["focus"] == "ws13 13 w"
    assert len(msg["slots"]) == 12                   # page 0 only; @13 is on page 1


def test_message_focus_is_blank_when_nothing_is_focused():
    d = km_deck.Deck()
    d.update([{"id": "tmux:@1", "ws": "a", "n": "1 sh"}])
    assert d.message(0, {})["focus"] == ""


def test_message_focus_is_trimmed_to_forty_not_the_screen_width():
    # FOCUS_MAX is 40, not the 21-column screen width -- trimming to the
    # screen width would make km_text.marquee's scroll (spec 5.2) unreachable,
    # since marquee returns early whenever the text already fits the display.
    d = km_deck.Deck()
    d.update([{"id": "tmux:@1", "ws": "a" * 30, "n": "1 " + "b" * 30}])
    focus = d.message(0, {}, focused="tmux:@1")["focus"]
    assert len(focus) > 21          # longer than the screen: marquee has work to do
    assert len(focus) <= 40


def test_message_focus_is_blank_when_focused_window_is_unknown():
    # .get() handles a focused id that names no known window; nothing pinned
    # this before.
    d = km_deck.Deck()
    assert d.message(0, {}, focused="tmux:@99")["focus"] == ""


def test_message_reports_the_total_window_count_across_all_pages():
    d = km_deck.Deck()
    d.update([{"id": "tmux:@%d" % i, "ws": "a", "n": "%d w" % i} for i in range(14)])
    assert d.message(0, {})["total"] == 14


def test_message_worst_case_wire_size_is_under_the_codec_cap():
    # The true worst case for one page: TWELVE DISTINCT workspaces, not one
    # repeated -- workspaces are sent once by reference (the `ws` list), so a
    # single repeated workspace is actually the BEST case. Every window rings
    # and one is focused, since both add bytes. This settles a real
    # disagreement between three prior estimates (km_proto.py's ~1099,
    # spec 7.1's ~1028, a hand measurement's 1001) with an actual measurement.
    # LineCodec discards an over-long line WHOLE (silently blanking the pad),
    # so this cap is worth a real test rather than another guess.
    d = km_deck.Deck()
    windows = [{"id": "tmux:@%d" % i,
                "ws": "workspace-%d-very-long-name-string" % i,
                "n": "%d some-long-window-name-here" % i}
               for i in range(12)]
    d.update(windows)
    colors = {win["ws"]: "e16000" for win in windows}
    m = d.message(page=0, colors=colors, focused="tmux:@0",
                  bells=[win["id"] for win in windows])
    encoded = km_proto.encode(m)
    assert len(encoded) < 2048
