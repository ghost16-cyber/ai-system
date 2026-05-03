names = [""] * 5

for i in range(5):
    names[i] = input(f"Enter name {i +1}: ")

index = int(input("Enter the index (0-4) you want to update: "))
new_name = input("Enter the new name: ")
names[index] = new_name

print("\nUpdated names:")
for i in range(5):
    print(f"Person {i + 1}: {names[i]}")
