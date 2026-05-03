choice = 0

while choice != 3:
    print("Hello")
    print("\n Menu")
    print("1. Option One")
    print("2. Option Two")
    print("3. option three")
    print("3. Exit")

    choice = int(input("Enter your choice: "))

    while choice == 1:
        print("hello.")
        break

    while choice == 2:
        print("Good bye.")
        break

    while choice < 1 or choice > 3:
        print("Invalid choice. Please try again.")
        break


print("Program exited.")
