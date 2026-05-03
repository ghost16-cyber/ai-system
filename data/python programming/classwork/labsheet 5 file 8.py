import csv
file_name = input("Enter file name: ")

file =  open(file_name,'w')

region = 1

while True:
    rainfall = input(f"Enter amount of rainfall for region {region}: ")

    if rainfall == "":
        break
    evaporation = input(f"Enter amount evaporation for region{region}: ")
    if evaporation == "":
        break

    file.write(f"{region},{rainfall},{evaporation}\n")

    region += 1

