import km_proto


def test_encode_is_compact_jsonl():
    assert km_proto.encode({"t": "ping"}) == b'{"t":"ping"}\n'


def test_feed_single_message():
    c = km_proto.LineCodec()
    assert c.feed(b'{"t":"ws","active":3}\n') == [{"t": "ws", "active": 3}]


def test_feed_partial_then_rest():
    c = km_proto.LineCodec()
    assert c.feed(b'{"t":"pi') == []
    assert c.feed(b'ng"}\n{"t":"pong"}\n') == [{"t": "ping"}, {"t": "pong"}]


def test_feed_skips_garbage_and_non_dicts():
    c = km_proto.LineCodec()
    out = c.feed(b'not json\n[1,2]\n{"no_t":1}\n{"t":"ok"}\n\n')
    assert out == [{"t": "ok"}]


def test_oversize_line_dropped_and_recovers():
    c = km_proto.LineCodec(max_line=32)
    assert c.feed(b"x" * 100) == []
    assert c.feed(b'y\n{"t":"ok"}\n') == [{"t": "ok"}]


def test_oversize_complete_line_dropped():
    c = km_proto.LineCodec(max_line=32)
    big = b'{"t":"ok","pad":"' + b"x" * 100 + b'"}\n'
    assert c.feed(big) == []
    assert c.feed(b'{"t":"ok"}\n') == [{"t": "ok"}]
