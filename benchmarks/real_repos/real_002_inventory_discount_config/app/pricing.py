from app.config import DISCOUNT_RATE


def discounted_price(total: int) -> float:
    return total * (1 - DISCOUNT_RATE)
