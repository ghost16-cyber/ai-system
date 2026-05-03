file_name = input("Enter the name of the file to save the data: ")

file = open(file_name,'w')

while True:
    student_id = input("Enter student ID.")

    if  student_id =="":
        break

    mark1 = float(input("Enter mark for test 1: "))
    mark2 = float(input("Enter mark for test 2: "))
    mark3 = float(input("Enter mark for test 3: "))

    file.write(f"{student_id}: {mark1} {mark2} {mark3}\n")

file.closed()