import math

def square(n):
    return f"square of {n} is: {n ** 2}"

def cubic(n):
    return f"cube of {n} is: {n ** 3}"

def quadruple(n):
    return f"cube of {n} is: {n ** 4}"

def quintuple(n):
    return f"cube of {n} is: {n ** 5}"

def main():
    num = int(input("enter a number: "))

    while True:
        print("__menu__")
        print("1. exit")
        print("2. square number")
        print("3. cubic number")
        print("4. quadruple number")
        print("5. quintuple number")
        print("6. choose number again: ")

        choice = input("choose an option: ").strip()

        if choice == "1":
            print("exit")
            break
        elif choice == "2":
            square(num)
            print(square(num))
        elif choice == "3":
            cubic(num)
            print(cubic(num))
        elif choice == "4":
            quadruple(num)
            print(quadruple(num))
        elif choice == "5":
            quintuple(num)
            print(quintuple(num))
        elif choice == "6":
            num = int(input("enter another number: "))

        else:
            print("invalid option. Try again.")
main()
#
# def main():
#     user_choice = int(input(f"choice= "))
#     user_num = int(input(f"num= "))
#
#     menu(num=user_num, choice=user_choice)
#
#     if __name__ == __main__:
#      main()