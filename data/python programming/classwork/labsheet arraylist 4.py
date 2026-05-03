integer = int(input("Input amount of numbers: "))
numbers = [0] * integer

for i in range(integer):
    numbers[i] = int(input(f"enter number {i + 1}: "))

smallest = numbers[0]
largest= numbers[0]

for i in range(1, integer):
    if numbers[i] < smallest:
        smallest = numbers[i]

    if numbers[i] > largest:
        largest = numbers[i]

print("smallest number is: ",smallest)
print("largest number is: ",largest)