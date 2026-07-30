from prefixes import has_prefix


def test_has_prefix_uses_start():
    assert has_prefix('report-2026', 'report') is True
