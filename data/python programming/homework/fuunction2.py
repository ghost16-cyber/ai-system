def menu():
    while True:
        print("\nMenu:")
        print("1. Print Hello")
        print("2. Print Good Bye")
        print("3. Exit")

        choice = input("Enter your choice (1-3): ")

        if choice == '1':
            print("Hello")
        elif choice == '2':
            print("Good Bye")
        elif choice == '3':
            print("Exiting program.")
            break
        else:
            print("Invalid choice. Please try again.")



menu()