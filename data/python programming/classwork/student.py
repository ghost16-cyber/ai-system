import csv

file = open('student.csv1', 'w', newline ='')
writer = csv.writer(file)
writer.writerow(["name" , "20"])
writer.writerow(["Alice" , "21"])
writer.writerow(["Bob" , "19"])
writer.writerow(["Charlie" ,"22" ])
writer.writerow(["Diana" , "20"])


file.close()