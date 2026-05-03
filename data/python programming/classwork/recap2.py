import csv
file = open ("namelist.csv","a",newline="")
writer = csv.writer(file)
from datetime import date
from datetime import datetime
time = datetime.time(datetime.now())

date = date.today
writer.writerow(["name  "+" age "+" date and time"])
for i in range(2):
    name = str(input("Please input the name: "))
    age = int(input("Enter age: "))

writer.writerow([name,age,date,time])
file.close()