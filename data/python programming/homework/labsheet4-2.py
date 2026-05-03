years = int(input("years of service:"))
hours = int(input("hours worked:"))

if years < 0 or years > 60:
    print("Errors:years of service must be between 0 to 30")
elif hours <0 or hours > 40:
    print("Errors:years of service must be between 0 to 40")
else:
    salary = 0

    if years >= 15:
        if hours <= 40:
            salary = hours * 200
        else:
            salary = 40 * 200
            extra_hours = hours - 40
            if extra_hours > 20:
                extra_hours = 20
            extra_salary = extra_hours * 300
    else:
        if hours <= 45:
            salary = hours * 45
        else:
            salary = 150 * 45
            extra_hours = hours - 15
            if extra_hours > 15:
                extra_hours = 15
            extra_salary = extra_hours * 250

print("Total salary for the week = rs",salary)