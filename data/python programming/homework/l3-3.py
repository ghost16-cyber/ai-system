hours_worked = int(input("how many hours worked?\n"))

if hours_worked > 0 and hours_worked < 40:
    total_wages = hours_worked * 100
    print("total wages for the week:",total_wages)
elif hours_worked > 40:
    total_wages = hours_worked * 150
    print("total wages for the week:",total_wages)
else:
    print("Error: invalid information")
