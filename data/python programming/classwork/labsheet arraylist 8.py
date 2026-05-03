days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

month_names = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]

continue_program = "yes"

while continue_program == "yes":
    daily_income = float(input("\nEnter the daily income of the baby-sitter: "))

    print("\nMonthly Income Report:")
    for i in range(12):
        total_income = days_in_month[i] * daily_income
        print(f"{month_names[i]}: Rs {total_income:.2f}")


    while True:
        print("\nDo you want to enter data for another baby-sitter? yes or no")
        continue_program = input()
        if continue_program == "yes" or continue_program == "no":
            break
        else:
            print("Invalid input. Please type 'yes' or 'no'.")

print("\nProgram ended.")
