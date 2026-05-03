# letter1 = str(input("enter first choice"))
# letter2 = str(input("enter the second choice"))
# letter3 = str(input("enter the third choice"))
# letter4 = str(input("enter the fourth choice"))
# letter5 = str(input("enter the fifth choice"))
# letter6 = str(input("enter the sixth choice"))
# if letter1 == "a":
#     print("this is the first letter")
# if letter2 == "b:
#     print("this is the second letter")
# if letter3 == c:
#     print("this is the third letter")
# if letter4 == d:
#     print("this is the fourth letter")
# if letter5 == e:
#     print("this is the fifth letter")
# if letter6 == f:
#     print("this is the sixth letter")

# else:
#     print("error")

letter_list = ["a", "b", "c", "d", "e", "f"]
for i in range(1,6):
    letter = str(input(f"enter {i}th choice: "))
    if letter == letter_list[i-1]:
        print(f"this the {i}th letter")
    else:
        print("error")

