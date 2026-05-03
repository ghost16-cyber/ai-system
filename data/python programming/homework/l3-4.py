entry_time = int(input("Enter time of entry: "))
leaving_time = int(input("Enter leaving time(0-23): "))
bill = 0

if entry_time < 21:
    if leaving_time <= 21:
        work = leaving_time - entry_time
    else:
        work = 21 - entry_time
    