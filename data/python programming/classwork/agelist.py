import csv

file = open("agelist.csv",'w', newline='')
writer = csv.writer(file)

writer.writerow(["Age"])

i = 1
while 1 <= 5:
    age = input("Enter age: ")
    writer.writerow([age])
    i = i + 1

file.closed()