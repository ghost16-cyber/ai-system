from script import calculate_gradient, input_value
import math


def calculate_distance(x1, x2, y1, y2):
    distance = math.sqrt(((x2 - x1) ** 2) + ((y2 - y1) ** 2))
    return f"distance is: {distance}"


def main():
    x1, y1, x2, y2 = input_value()
    print(calculate_distance(x1, y1, x2, y2))


if __name__ == "__main__":
    main()
