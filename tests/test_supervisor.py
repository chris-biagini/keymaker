import asyncio
import json
import os
from pathlib import Path

import pytest

import km_deck
import km_proto
from keymakerd.__main__ import Config, Supervisor

WORKSPACES = [{"id": 1, "windows": 1}, {"id": 3, "windows": 2}]


def _supervisor(tmp_path):
    """A Supervisor with no live link, for testing deck/knob/tap logic synchronously.

    SerialLink.__init__ only stores its path (serial_link.py:11-18) and send() no-ops
    while disconnected, so the device never has to exist. The async tests below build
    their own Supervisor against a real pty because they exercise the link itself;
    this one deliberately does not.
    """
    cfg = Config(device=str(tmp_path / "no-such-device"), runtime_dir=tmp_path,
                 home=tmp_path, state_dir=tmp_path)
    return Supervisor(cfg)


class FakeHypr:
    """Serves .socket.sock (requests) and .socket2.sock (events)."""

    def __init__(self):
        self.dispatched = []
        self.event_writer = None

    async def start(self, instance_dir):
        instance_dir.mkdir(parents=True)
        await asyncio.start_unix_server(self._req, path=str(instance_dir / ".socket.sock"))
        await asyncio.start_unix_server(self._ev, path=str(instance_dir / ".socket2.sock"))

    async def _req(self, reader, writer):
        cmd = (await reader.read(1024)).decode()
        if cmd == "j/workspaces":
            writer.write(json.dumps(WORKSPACES).encode())
        elif cmd == "j/activeworkspace":
            writer.write(b'{"id": 3}')
        elif cmd == "j/activewindow":
            writer.write(b'{"class": "foot", "title": "hello"}')
        elif cmd == "j/clients":
            writer.write(b"[]")
        else:
            self.dispatched.append(cmd)
            writer.write(b"ok")
        await writer.drain()
        writer.close()

    async def _ev(self, reader, writer):
        self.event_writer = writer


@pytest.fixture
def pad():
    master, slave = os.openpty()
    os.set_blocking(master, False)
    yield master, os.ttyname(slave)
    for fd in (master, slave):
        try:
            os.close(fd)
        except OSError:
            pass


def _read_msgs(master):
    codec = km_proto.LineCodec()
    try:
        return codec.feed(os.read(master, 65536))
    except BlockingIOError:
        return []


def test_snapshot_on_connect_and_key_dispatch(pad, tmp_path):
    # The old split deck read n<6 as a raw workspace switch; the switchboard
    # supersedes that (docs/superpowers/specs/2026-08-22-macropad-switchboard-
    # design.md § 1) -- every key is a deck slot now, and a tap on an empty
    # slot (nothing is running under FakeHypr) or a hold (reserved, spec § 10)
    # must dispatch nothing.
    master, slave_path = pad

    async def scenario():
        hypr = FakeHypr()
        await hypr.start(tmp_path / "hypr" / "fake0")
        cfg = Config(device=slave_path, runtime_dir=tmp_path, home=tmp_path,
                     state_dir=tmp_path)
        sup = Supervisor(cfg)
        task = asyncio.create_task(sup.run())
        await asyncio.sleep(0.4)
        msgs = _read_msgs(master)
        os.write(master, km_proto.encode({"t": "key", "n": 2, "act": "tap"}))
        os.write(master, km_proto.encode({"t": "key", "n": 0, "act": "hold"}))
        await asyncio.sleep(0.3)
        task.cancel()
        return msgs, hypr.dispatched

    msgs, dispatched = asyncio.run(scenario())
    types = [m["t"] for m in msgs]
    for expected in ("hello", "ws", "flags"):
        assert expected in types
    # The link-up snapshot goes out before the first Hyprland refresh, so the
    # FIRST ws msg is the default state; the refreshed one arrives last.
    ws = [m for m in msgs if m["t"] == "ws"][-1]
    assert ws == {"t": "ws", "active": 3, "occupied": [1, 3], "urgent": [], "colors": {}, "names": {}}
    assert not any(d.startswith("dispatch workspace") for d in dispatched)
    assert not any(d.startswith("dispatch movetoworkspacesilent") for d in dispatched)


def test_deck_tap_selects_tmux_window(monkeypatch, tmp_path):
    # Supersedes the old split-deck's ctx-based bottom-half key select (spec
    # § 1): every key is now a deck slot, resolved through self._deck_twins
    # (populated by the deck poll -- see _poll_deck) rather than self.ctx.
    from keymakerd import tmux as tmuxmod
    selected = []

    async def fake_select(session, i):
        selected.append((session, i))
        return True

    async def scenario():
        monkeypatch.setattr(tmuxmod, "select_window", fake_select)
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path,
                     state_dir=tmp_path)
        sup = Supervisor(cfg)
        sup.link = type("L", (), {"send": staticmethod(lambda m: True)})()
        # tmux:@3 sits at GLOBAL slot 13 (page 1, key 1) -- present specifically
        # so an unbounded on_tap(13) on page 0 would wrongly resolve to it.
        sup.deck.slots = {"tmux:@1": 0, "tmux:@2": 5, "tmux:@3": 13}
        sup._deck_twins = {
            "tmux:@1": {"id": "tmux:@1", "s": "mirepoix", "i": 1, "n": "rails",
                        "active": False, "bell": False},
            "tmux:@2": {"id": "tmux:@2", "s": "mirepoix", "i": 6, "n": "tests",
                        "active": False, "bell": False},
            "tmux:@3": {"id": "tmux:@3", "s": "mirepoix", "i": 9, "n": "logs",
                        "active": False, "bell": False},
        }
        sup._on_pad_msg({"t": "key", "n": 0, "act": "tap"})    # slot 0
        sup._on_pad_msg({"t": "key", "n": 5, "act": "tap"})    # slot 5
        sup._on_pad_msg({"t": "key", "n": 1, "act": "hold"})   # reserved: no-op
        sup._on_pad_msg({"t": "key", "n": 3, "act": "tap"})    # empty slot: no-op
        sup._on_pad_msg({"t": "key", "n": 13, "act": "tap"})   # out of range: must NOT
                                                                # reach tmux:@3's slot
        sup._on_pad_msg({"t": "key", "n": -1, "act": "tap"})   # negative: ignored
        await asyncio.sleep(0.05)
        return selected

    assert asyncio.run(scenario()) == [("mirepoix", 1), ("mirepoix", 6)]


def test_deck_tap_focuses_client_first_when_unfocused(monkeypatch, tmp_path):
    from keymakerd import tmux as tmuxmod
    calls = []

    async def fake_select(session, i):
        calls.append(("select", session, i))
        return True

    async def fake_dispatch(cmd):
        calls.append(("hypr", cmd))

    async def scenario():
        monkeypatch.setattr(tmuxmod, "select_window", fake_select)
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path,
                     state_dir=tmp_path)
        sup = Supervisor(cfg)
        sup.link = type("L", (), {"send": staticmethod(lambda m: True)})()
        sup._dispatch = fake_dispatch
        sup.deck.slots = {"tmux:@1": 0, "tmux:@2": 1}
        sup._deck_twins = {
            "tmux:@1": {"id": "tmux:@1", "s": "oracle", "i": 1, "n": "rails",
                        "active": False, "bell": False},
            "tmux:@2": {"id": "tmux:@2", "s": "oracle", "i": 2, "n": "logs",
                        "active": False, "bell": False},
        }
        sup._deck_ws_addr = {"oracle": "0xfeet"}
        sup.state.addr = "0xffox"                 # firefox holds focus
        sup._on_pad_msg({"t": "key", "n": 0, "act": "tap"})
        await asyncio.sleep(0.05)
        first = list(calls)
        calls.clear()
        sup.state.addr = "0xfeet"                 # foot already focused
        sup._on_pad_msg({"t": "key", "n": 1, "act": "tap"})
        await asyncio.sleep(0.05)
        return first, list(calls)

    unfocused, focused = asyncio.run(scenario())
    # unfocused: focus the foot window FIRST, then select the tmux window
    assert unfocused == [("hypr", "dispatch focuswindow address:0xfeet"),
                         ("select", "oracle", 1)]
    assert focused == [("select", "oracle", 2)]   # no focus hop when already there


def test_link_up_resends_the_deck(tmp_path):
    # C1: a reconnecting pad (USB reset, firmware deploy, the app-menu round
    # trip) must not stay stuck on its empty default deck until some
    # unrelated window event happens to change the computed message.
    sup = _supervisor(tmp_path)
    sup.deck_msg = {"t": "deck", "page": 0, "pages": 1,
                    "ws": [["mirepoix", "e16000"]],
                    "slots": [{"i": 0, "c": 0, "n": "1 rails", "s": "live"}],
                    "map": [1], "bells": []}
    sent = []
    sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()

    asyncio.run(sup._on_link_up())

    decks = [m for m in sent if m.get("t") == "deck"]
    assert decks == [sup.deck_msg]


def test_link_up_before_first_poll_sends_no_deck(tmp_path):
    sup = _supervisor(tmp_path)
    sent = []
    sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()

    asyncio.run(sup._on_link_up())

    assert not any(m.get("t") == "deck" for m in sent)


def test_link_drop_clears_deck_msg_so_the_next_poll_resends(tmp_path):
    # Even without an explicit resend, a dropped link must force _poll_deck to
    # re-emit on its next pass rather than staying silent because the computed
    # message is unchanged from before the drop.
    sup = _supervisor(tmp_path)
    sup.deck_msg = {"t": "deck", "page": 0, "pages": 1,
                    "ws": [], "slots": [], "map": [0], "bells": []}
    sup._on_link_down()
    assert sup.deck_msg is None


def test_dispatch_oserror_keeps_instance(tmp_path):
    # A transient IPC failure in _dispatch must NOT clear _instance:
    # only _hypr_events can rediscover it, and its trigger (socket2 death)
    # never fires on a healthy compositor — so clearing here permanently
    # killed the refresher (observed live 2026-08-18: frozen ws colors,
    # zero request-socket connects while events flowed).
    async def scenario():
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path,
                     state_dir=tmp_path)
        sup = Supervisor(cfg)
        dead = tmp_path / "hypr" / "gone"      # no .socket.sock -> OSError
        sup._instance = dead
        await sup._dispatch("dispatch workspace 2")
        assert sup._instance is dead           # still set; owner heals it
    asyncio.run(scenario())


def test_deck_message_is_emitted_from_live_state(tmp_path, monkeypatch):
    from keymakerd import __main__ as main_mod
    sup = _supervisor(tmp_path)                 # existing helper in this file
    sup.deck.update([{"id": "tmux:@1", "ws": "mirepoix", "n": "1 rails"}])
    msg = sup._deck_msg({"mirepoix": "e16000"}, focused="tmux:@1", bells=set())
    assert msg["t"] == "deck"
    assert msg["slots"] == [{"i": 0, "c": 0, "n": "1 rails", "s": "focused"}]


def test_paging_wraps_and_never_lands_on_a_page_that_does_not_exist(tmp_path):
    sup = _supervisor(tmp_path)
    sup.deck.update([{"id": "tmux:@%d" % i, "ws": "a", "n": str(i)} for i in range(13)])
    sup.on_knob_turn(-1)
    assert sup.deck_page == 1                    # wrapped backwards
    sup.on_knob_turn(1)
    assert sup.deck_page == 0


def test_a_bell_on_another_page_never_changes_the_page(tmp_path):
    # Spec section 7.3: auto-jumping would move the keys under your fingers in
    # response to something you did not do.
    sup = _supervisor(tmp_path)
    sup.deck.update([{"id": "tmux:@%d" % i, "ws": "a", "n": str(i)} for i in range(20)])
    sup.deck_page = 0
    sup._deck_msg({"a": "ffffff"}, focused=None, bells={"tmux:@15"})
    assert sup.deck_page == 0


def test_tapping_a_ghost_dismisses_it_and_launches_nothing(tmp_path):
    sup = _supervisor(tmp_path)
    sup.deck.update([{"id": "tmux:@1", "ws": "a", "n": "1 x"}])
    sup.deck.update([])                          # slot 0 ghosts
    assert sup.on_tap(0) == "dismissed"
    assert sup.deck.ghosts == {}


def test_slots_persist_across_a_supervisor_restart(tmp_path):
    sup = _supervisor(tmp_path)
    sup.deck.update([{"id": "tmux:@7", "ws": "a", "n": "1 x"}])
    sup.save_deck()
    again = _supervisor(tmp_path)
    assert again.deck.slots == {"tmux:@7": 0}


def test_dial_resends_the_deck_message_immediately(tmp_path):
    # Without this, a knob gesture is invisible on the pad until the next
    # _poll_deck cycle, up to CTX_POLL_S (1s) later -- reads as a broken knob.
    sup = _supervisor(tmp_path)
    sup.deck.update([{"id": "tmux:@%d" % i, "ws": "a", "n": str(i)} for i in range(13)])
    sent = []
    sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()
    # Simulate a poll having already landed once, without running one for real.
    sup._deck_render_args = ({"a": "ffffff"}, None, set())

    sup._on_pad_msg({"t": "dial", "d": 1})            # pages immediately
    assert sup.deck_page == 1
    assert sent and sent[-1]["t"] == "deck" and sent[-1]["page"] == 1


def test_knob_gestures_before_the_first_poll_send_nothing(tmp_path):
    sup = _supervisor(tmp_path)
    sent = []
    sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()
    sup._on_pad_msg({"t": "dial", "d": 1})
    assert sent == []                                 # no _deck_render_args yet: no crash, no send


def test_focused_window_id_matches_active_tmux_window_in_active_session(tmp_path):
    sup = _supervisor(tmp_path)
    twins = [{"id": "tmux:@1", "s": "mirepoix", "i": 1, "n": "rails",
              "active": True, "bell": False},
             {"id": "tmux:@2", "s": "mirepoix", "i": 2, "n": "tests",
              "active": False, "bell": False}]
    sup.state.fg = {1: {"addr": "0xaaa", "cls": "ws-mirepoix"}}
    sup.state.active = 1
    assert sup._focused_window_id(twins) == "tmux:@1"

    # No ws-* client on the active workspace: fall back to the plain
    # Hyprland-focused client's own address (sessionless terminal, or a
    # focused client that isn't a ws-* terminal at all).
    sup.state.fg = {}
    sup.state.addr = "0xffox"
    assert sup._focused_window_id(twins) == "hypr:0xffox"

    sup.state.addr = ""
    assert sup._focused_window_id(twins) is None


def test_poll_deck_populates_caches_dedupes_and_saves_on_change(monkeypatch, tmp_path):
    # The integration seam _deck_msg/on_tap/_focus_deck_window all assume
    # _poll_deck already populated: this drives it against a real
    # list_deck_windows shape and a real ws-* client, rather than hand-stubbing
    # _deck_twins/_deck_ws_addr the way the tap tests above do -- so the one
    # thing that must actually agree, _deck_ws_addr's key (client class minus
    # "ws-") against a twin's "s" (tmux session name), is genuinely exercised.
    from keymakerd import tmux as tmuxmod
    import json as jsonmod

    CLIENTS = [{"class": "ws-mirepoix", "address": "0xaaa",
                "workspace": {"id": 1, "name": "mirepoix"}}]
    ONE_WINDOW = [{"id": "tmux:@1", "s": "mirepoix", "i": 1, "n": "rails",
                   "active": True, "bell": False}]

    async def scenario():
        windows = list(ONE_WINDOW)

        async def fake_list():
            return windows

        monkeypatch.setattr(tmuxmod, "list_deck_windows", fake_list)
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path,
                     state_dir=tmp_path)
        sup = Supervisor(cfg)
        sent = []
        sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()
        sup.state.clients = CLIENTS
        sup.state.fg = {1: {"addr": "0xaaa", "cls": "ws-mirepoix"}}
        sup.state.active = 1

        await sup._poll_deck()
        after_first = list(sent)
        saved_after_first = jsonmod.loads((tmp_path / "deck-slots.json").read_text())
        twins_after_first = dict(sup._deck_twins)
        addr_after_first = dict(sup._deck_ws_addr)

        await sup._poll_deck()                     # identical input: no re-send
        after_second = list(sent)

        windows.clear()                             # the window disappears
        await sup._poll_deck()
        after_third = list(sent)
        saved_after_third = jsonmod.loads((tmp_path / "deck-slots.json").read_text())

        return (after_first, after_second, after_third, saved_after_first,
                saved_after_third, twins_after_first, addr_after_first)

    (after_first, after_second, after_third, saved_after_first, saved_after_third,
     twins_after_first, addr_after_first) = asyncio.run(scenario())

    deck_msgs_1 = [m for m in after_first if m["t"] == "deck"]
    deck_msgs_2 = [m for m in after_second if m["t"] == "deck"]
    deck_msgs_3 = [m for m in after_third if m["t"] == "deck"]
    assert len(deck_msgs_1) == 1
    assert deck_msgs_1[0]["slots"] == [{"i": 0, "c": 0, "n": "1 rails", "s": "focused"}]
    assert len(deck_msgs_2) == 1                    # dedupe: identical input, no re-send
    assert len(deck_msgs_3) == 2                     # the disappearance IS a change

    assert twins_after_first == {"tmux:@1": ONE_WINDOW[0]}
    assert addr_after_first == {"mirepoix": "0xaaa"}

    assert saved_after_first["slots"] == {"tmux:@1": 0}
    assert saved_after_third["slots"] == {}          # save gate fired on the shrink


def test_poll_deck_with_no_hyprland_snapshot_never_wipes_the_persisted_deck(monkeypatch, tmp_path):
    # I1: state.clients starts [] and is only filled by HyprState.refresh, while
    # _poll_deck runs on its own independent timer. A poll landing before the
    # first refresh (or after a hyprctl round trip fails once) must NOT be read
    # as "no windows are running" -- that would clear every slot and overwrite
    # deck-slots.json with {}, destroying the exact file sticky allocation
    # exists to protect.
    from keymakerd import tmux as tmuxmod

    async def fake_list():
        return [{"id": "tmux:@1", "s": "mirepoix", "i": 1, "n": "rails",
                 "active": True, "bell": False}]

    async def scenario():
        monkeypatch.setattr(tmuxmod, "list_deck_windows", fake_list)
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path,
                     state_dir=tmp_path)
        sup = Supervisor(cfg)
        sup.link = type("L", (), {"send": staticmethod(lambda m: True)})()
        sup.deck.slots = {"tmux:@1": 0}
        sup.deck._last = {"tmux:@1": {"ws": "mirepoix", "n": "1 rails"}}
        sup.save_deck()
        before = dict(sup.deck.slots)

        sup.state.clients = []           # no snapshot yet
        await sup._poll_deck()

        return before, dict(sup.deck.slots)

    before, after = asyncio.run(scenario())
    assert after == before == {"tmux:@1": 0}
    saved = json.loads((tmp_path / "deck-slots.json").read_text())
    assert saved["slots"] == {"tmux:@1": 0}


def test_poll_deck_survives_a_tmux_outage_without_blanking_bare_terminals(monkeypatch, tmp_path):
    # I2: list_deck_windows() returns None when the tmux SERVER is down, not
    # when it merely has no windows. A bare `foot` earns its key purely from
    # state.clients and has nothing to do with tmux's health, so a tmux
    # outage must not blank keys unrelated to tmux.
    from keymakerd import tmux as tmuxmod

    async def fake_list_none():
        return None

    async def scenario():
        monkeypatch.setattr(tmuxmod, "list_deck_windows", fake_list_none)
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path,
                     state_dir=tmp_path)
        sup = Supervisor(cfg)
        sent = []
        sup.link = type("L", (), {"send": staticmethod(lambda m: sent.append(m) or True)})()
        sup.state.clients = [{"class": "foot", "address": "0xbare",
                              "workspace": {"id": 1, "name": "home"}, "title": "bare"}]
        await sup._poll_deck()
        return sent, dict(sup.deck.slots)

    sent, slots = asyncio.run(scenario())
    deck_msgs = [m for m in sent if m["t"] == "deck"]
    assert len(deck_msgs) == 1
    assert deck_msgs[0]["slots"] == [{"i": 0, "c": 0, "n": "bare", "s": "live"}]
    assert slots == {"hypr:0xbare": 0}


def test_poll_deck_tmux_outage_still_ghosts_tmux_windows_that_disappear(monkeypatch, tmp_path):
    # Guard against I2's fix reintroducing I1: with twins == [] (whether from a
    # real empty tmux server or an outage) and a POPULATED client list,
    # tmux-backed windows legitimately vanish from the deck and must ghost
    # normally rather than being protected like the empty-clients case.
    from keymakerd import tmux as tmuxmod

    async def fake_list_none():
        return None

    async def scenario():
        monkeypatch.setattr(tmuxmod, "list_deck_windows", fake_list_none)
        cfg = Config(device="/dev/null", runtime_dir=tmp_path, home=tmp_path,
                     state_dir=tmp_path)
        sup = Supervisor(cfg)
        sup.link = type("L", (), {"send": staticmethod(lambda m: True)})()
        sup.deck.slots = {"tmux:@1": 0}
        sup.deck._last = {"tmux:@1": {"ws": "mirepoix", "n": "1 rails"}}
        sup.state.clients = [{"class": "ws-mirepoix", "address": "0xaaa",
                              "workspace": {"id": 1, "name": "mirepoix"}}]
        await sup._poll_deck()
        return dict(sup.deck.slots), dict(sup.deck.ghosts)

    slots, ghosts = asyncio.run(scenario())
    assert slots == {}
    assert ghosts == {0: {"ws": "mirepoix", "n": "1 rails"}}


def test_rekey_clears_slots_and_ghosts_and_re_derives_in_order(tmp_path):
    # Sticky allocation means a window keeps its first slot for life, so this
    # is the ONLY way to reorder a board once assignments exist.
    sup = _supervisor(tmp_path)
    sup.deck.slots = {"tmux:@9": 0, "tmux:@1": 1}
    sup.deck.ghosts = {5: {"ws": "gone", "n": "1 old"}}
    sup.deck._last = {"tmux:@9": {"ws": "b", "n": "1 x"},
                      "tmux:@1": {"ws": "a", "n": "1 y"}}
    sup._deck_render_args = ({}, None, [])
    sup._deck_wins = [{"id": "tmux:@1", "ws": "a", "n": "1 y"},
                      {"id": "tmux:@9", "ws": "b", "n": "1 x"}]
    sup.on_rekey()
    assert sup.deck.slots == {"tmux:@1": 0, "tmux:@9": 1}
    assert sup.deck.ghosts == {}
