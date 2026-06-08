from dataclasses import dataclass


@dataclass
class LineItem:
    price: int
    quantity: int


def line_total(item: LineItem) -> int:
    return item.price + item.quantity
