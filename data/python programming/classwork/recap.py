# import csv
# import matplotlib.pyplot as plt
# import numpy as np

file = open("names.csv","w")
writer = csv.writer(file)
writer.writerow(["Name of students", "Age of student"])

count = 0
while count < 5:
            name = str(input("Enter the name: "))
            age = int(input("Enter student age : "))
            writer.writerow([name, age])
            count = count + 1

file.closed()



