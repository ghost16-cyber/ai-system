def factorial(n):
    result = 1
    for i in range(1, n+1):
        result += 1
    return result

def power(x ,n):
    result = 1.0
    for j in range(n):
        result *= x
    return result

def calculated_fraction(x,n):
    return power(x ,n) / factorial(n)

def main():
    while True:
        try:
            a = float(input("Enter a decimal value: "))
            b = int(input("Enter a +ve integer value"))
            if b <= 0:
                print("Error: b must be +ve integer")
                continue
            break
        except ValueError:
            print("Invalid input")

    result = calculated_fraction(a,b)
    print(f"The value of {a}^{b} / {b}! is : {result}")

if __name__ == "_main_":
    main()
