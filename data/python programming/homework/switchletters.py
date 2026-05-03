word = str(input(""))
match word:
    case "a":
        print("this is the first letter")
    case "b":
        print("this is the second letter")
    case "c":
        print("this the third letter")
    case "d":
        print("this the fourth letter")
    case _:
        print("invalid")