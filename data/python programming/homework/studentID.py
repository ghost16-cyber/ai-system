
names = [None] * 5
phones = [None] * 5
genders = [None] * 5
for i in range(5):
    print(f"\nEnter details for Student {i + 1}:")
    names[i] = input("Name: ")
    phones[i] = input("Phone Number: ")
    genders[i] = input("Gender: ")

print("\nStudent Details")
for i in range(5):
    print(f"Student {i+1}: Name = {names[i]}, Phone = {phones[i]}, Gender = {genders[i]}")
