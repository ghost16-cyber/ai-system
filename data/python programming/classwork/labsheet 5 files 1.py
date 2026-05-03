file = open("Square.txt",'w')
file.write("number\t square\n")
print("Enter numbers. press 0 to stop")

while True:
    num = int(input("value: "))

    if num == 0:
        break

    square_number = num * num
    file.write(f"{num}\t {square_number}\n")

file.closed()

