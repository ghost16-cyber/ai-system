days_in_months = [31, 28, 31, 30, 31, 30, 31, 30, 31, 30, 31, 30]
months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November","December"]
daily_income = int(input("Enter payment: "))
for i in range(12):
    total_income_each_month = daily_income * days_in_months[i]
    print(f"Income each month is:rs {total_income_each_month}")