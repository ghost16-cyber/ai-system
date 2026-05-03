age_groups = [0] * 15

group_labels = [
    "0 - 5", "6 - 10", "11 - 15", "16 - 20", "21 - 25",
    "26 - 30", "31 - 35", "36 - 40", "41 - 45", "46 - 50",
    "51 - 55", "56 - 60", "61 - 65", "66 - 70", "Above 70"
]

print("Enter ages one by one. Enter -1 to stop.\n")

while True:
    age = int(input("Enter age: "))

    if age == -1:
        break
    elif 0 <= age <= 70:
        age_groups[age // 5] += 1
    elif age > 70:
        age_groups[14] += 1
    else:
        print("Invalid age.")

print("\nSurvey Result:")
for i in range(15):
    print(f"Age Group {group_labels[i]}: {age_groups[i]} person(s)")