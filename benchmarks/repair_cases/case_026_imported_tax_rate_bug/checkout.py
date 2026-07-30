from tax_config import TAX_RATE


def total_with_tax(subtotal: float) -> float:
    return subtotal * (1 + TAX_RATE)
