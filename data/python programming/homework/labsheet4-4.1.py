tariff = int(input("Enter tariff number (110, 120, 140): "))
units = int(input("Enter number of units consumed: "))

amount = 0

if tariff == 110:
    minimum = 44.00
    if units <= 25:
        amount = units * 2.75
    elif units <= 75:
        amount = 25 * 2.75 + (units - 25) * 3.25
    elif units <= 150:
        amount = 25 * 2.75 + 50 * 3.25 + (units - 75) * 4.00
    else:
        amount = 25 * 2.75 + 50 * 3.25 + 75 * 4.00 + (units - 150) * 6.50

elif tariff == 120:
    minimum = 184.00
    if units <= 25:
        amount = units * 3.00
    elif units <= 75:
        amount = 25 * 3.00 + (units - 25) * 3.50
    elif units <= 150:
        amount = 25 * 3.00 + 50 * 3.50 + (units - 75) * 4.25
    else:
        amount = 25 * 3.00 + 50 * 3.50 + 75 * 4.25 + (units - 150) * 6.00

elif tariff == 140:
    minimum = 360.00
    if units <= 25:
        amount = units * 3.25
    elif units <= 75:
        amount = 25 * 3.25 + (units - 25) * 3.75
    elif units <= 150:
        amount = 25 * 3.25 + 50 * 3.75 + (units - 75) * 4.50
    else:
        amount = 25 * 3.25 + 50 * 3.75 + 75 * 4.50 + (units - 150) * 5.75

else:
    print("Invalid tariff number.")
    exit()

if amount <= minimum:
    amount = minimum
