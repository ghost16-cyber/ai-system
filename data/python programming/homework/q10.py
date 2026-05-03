num1 = input("Enter the first number=")
num2 = input("Enter the second number=")
if isinstance(num1, int) or isinstance(num2, int):
    pass
else:
    print(f"Invalid num1 and num2. Expected integers but got {num1} and {num2}")
    num1 = int(input("Enter the first number="))
    num2 = int(input("Enter the second number="))
if num1 >  num2:
    print("num1 is greater than num2")
elif num1 == num2:
    print("num1 is equal to num2")
else:
    print("num1 less than num2")
