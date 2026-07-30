from config import DISCOUNT_RATE


def discounted_price(price: float) -> float:
    return price * (1 - DISCOUNT_RATE)
