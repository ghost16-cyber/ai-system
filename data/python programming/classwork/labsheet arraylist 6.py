salary = int(input("fixed salary is: "))
days_in_months = [31, 28, 31, 30, 31, 30, 31, 30, 31, 30, 31, 30]
months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November","December"]

print("Average daily income for each month is: ")
for i in range(12):
    average_daily_income = salary / days_in_months[i]
    print(f"{months[i]}: {average_daily_income} rs")

