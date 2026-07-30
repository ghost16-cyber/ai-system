from datetime import date

from dates import format_date


def test_format_date_iso():
    assert format_date(date(2026, 5, 30)) == '2026-05-30'
