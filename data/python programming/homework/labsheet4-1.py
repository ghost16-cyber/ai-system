x = float(input("Enter the fee per adult session (x): "))
y = float(input("Enter the fee per child session (y): "))
num_adults = int(input("Enter the number of adults: "))
num_children = int(input("Enter the number of children: "))
member = input("Is the family a member? (yes or no): ")

if member.lower() == "yes":
    total = (x * num_adults) + (y * num_children)
else:
    total = (2 * x * num_adults) + (1.5 * y * num_children)

print("total fee charged: $",total)