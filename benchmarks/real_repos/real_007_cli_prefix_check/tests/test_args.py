from cli_tools.args import has_prefix


def test_has_prefix():
    assert has_prefix('--verbose', '--') is True
