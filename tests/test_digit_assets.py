import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_committed_digits_match_generator():
    out = subprocess.run(
        [sys.executable, str(REPO / "tools" / "gen_digits.py"), "--stdout"],
        capture_output=True, text=True, check=True)
    committed = (REPO / "firmware" / "assets" / "digits.py").read_text()
    assert out.stdout == committed


def test_digit_shapes():
    sys.path.insert(0, str(REPO / "firmware"))
    try:
        from assets.digits import DIGITS
    finally:
        sys.path.pop(0)
    assert len(DIGITS) == 10
    for rows in DIGITS:
        assert len(rows) == 24
        for mask in rows:
            assert 0 <= mask < (1 << 14)
    # every digit must actually mark pixels, and 8 lights all 7 segments
    assert all(any(rows) for rows in DIGITS)
    assert sum(bin(m).count("1") for m in DIGITS[8]) > \
        sum(bin(m).count("1") for m in DIGITS[1])
