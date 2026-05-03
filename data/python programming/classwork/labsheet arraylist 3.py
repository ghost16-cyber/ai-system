monthly_sale = [0.0] * 12
print(f"Enter monthly sales in rupees")

for i in range(12):
    monthly_sale = float(input(f"month {i + 1}:"))

total = 0
num = 0
while num <  12:
    total += monthly_sale
    num += 1

average_sale = total / 12

print("average monthly sale is: ", average_sale)