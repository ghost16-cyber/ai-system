def calculate_gradient(x1, y1, x2, y2):
    gradient = (y2 - y1) / (x2 - x1)
    return f"gradient is: {gradient}"


def input_value():
    x1 = float(input("x1="))
    y1 = float(input("y1="))
    x2 = float(input("x2="))
    y2 = float(input("y2="))

    if x1 == x2:
        print("x1 cannot equal x2")
        x1 = float(input("x1="))
        x2 = float(input("x2="))

    return x1, y1, x2, y2


def main():
    x1, y1, x2, y2 = input_value()
    print(calculate_gradient(x1, y1, x2, y2))


if __name__ == "__main__":
    main()
