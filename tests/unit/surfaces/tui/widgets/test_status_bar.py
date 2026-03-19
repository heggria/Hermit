"""Unit tests for StatusBar._format_count() helper.

_format_count is a pure static method so it can be tested without
instantiating the Textual widget (which would require a running app event loop).
"""

from __future__ import annotations

import pytest

from hermit.surfaces.cli.tui.widgets.status_bar import StatusBar

# ---------------------------------------------------------------------------
# _format_count
# ---------------------------------------------------------------------------


class TestFormatCount:
    """Parametrised coverage of StatusBar._format_count()."""

    # ------------------------------------------------------------------
    # Below the 1 000 threshold → plain integer string
    # ------------------------------------------------------------------

    def test_zero(self) -> None:
        assert StatusBar._format_count(0) == "0"

    def test_single_digit(self) -> None:
        assert StatusBar._format_count(7) == "7"

    def test_two_digits(self) -> None:
        assert StatusBar._format_count(42) == "42"

    def test_three_digits(self) -> None:
        assert StatusBar._format_count(999) == "999"

    def test_just_below_threshold(self) -> None:
        """999 is the largest value that should NOT be abbreviated."""
        assert StatusBar._format_count(999) == "999"

    # ------------------------------------------------------------------
    # At and above the 1 000 threshold → "<x.xk>" format
    # ------------------------------------------------------------------

    def test_exactly_one_thousand(self) -> None:
        assert StatusBar._format_count(1000) == "1.0k"

    def test_one_thousand_five_hundred(self) -> None:
        assert StatusBar._format_count(1500) == "1.5k"

    def test_rounding_down(self) -> None:
        """1 234 → 1.2k (truncated to one decimal via f-string rounding)."""
        assert StatusBar._format_count(1234) == "1.2k"

    def test_rounding_up(self) -> None:
        """1 750 → 1.8k (Python rounds half-to-even; 1.75 → 1.8)."""
        assert StatusBar._format_count(1750) == "1.8k"

    def test_ten_thousand(self) -> None:
        assert StatusBar._format_count(10_000) == "10.0k"

    def test_hundred_thousand(self) -> None:
        assert StatusBar._format_count(100_000) == "100.0k"

    def test_one_million(self) -> None:
        assert StatusBar._format_count(1_000_000) == "1000.0k"

    def test_large_value(self) -> None:
        """Very large token counts should still format without error."""
        assert StatusBar._format_count(999_999) == "1000.0k"

    # ------------------------------------------------------------------
    # Return-type contract
    # ------------------------------------------------------------------

    @pytest.mark.parametrize("n", [0, 500, 1000, 50_000])
    def test_always_returns_str(self, n: int) -> None:
        result = StatusBar._format_count(n)
        assert isinstance(result, str)

    # ------------------------------------------------------------------
    # Boundary: values straddling 1 000
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "n, expected",
        [
            (999, "999"),
            (1000, "1.0k"),
            (1001, "1.0k"),
        ],
    )
    def test_boundary_parametrised(self, n: int, expected: str) -> None:
        assert StatusBar._format_count(n) == expected
