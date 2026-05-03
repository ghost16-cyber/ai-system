id = [""] * 4
name =[""] * 4
mark1 = [0] * 4
mark2 = [0] * 4
for i in range(4):
    id[i] = int(input("id: "))
    name[i] = input("name: ")
    mark1[i] = int(input("mark1: "))
    mark2[i] = int(input("mark2: "))

pos = int(input("Enter position to display: "))
print(id[pos])
print(name[pos])
print(mark1[pos])
print(mark2[pos])

upd_pos = int(input("Enter position to update mark1: "))
new_mark = int(input("Enter new mark1 to replace old mark1: "))


