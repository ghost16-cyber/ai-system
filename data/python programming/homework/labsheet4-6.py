car_type = int(input("Enter car type (1 to 5): "))


match car_type:
    case 1:
        print("Type 1: 1500 CC")
    case 2:
        print("Type 2: 1500 CC with automatic mirrors")
    case 3:
        print("Type 3: 1500 CC with automatic mirrors and front and rear sensors")
    case 4:
        print("Type 4: 1200 CC")
    case 5:
        print("Type 5: 1200 CC with automatic gear")
    case _:
        print("Invalid car type. Please enter a number between 1 and 5.")
