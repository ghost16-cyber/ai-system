from app.reporting import render_report


def test_render_report():
    assert render_report('sales') == 'sales'
