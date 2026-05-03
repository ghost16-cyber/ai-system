service = str(input("year of service: "))
salary = int(input("salary is: "))

if salary < 5000 or salary > 100000:
    print("Error:salary must be between more than 5000 and less than 100000")
else:
    if salary >= 75000:
        min_service = 0
        engine = 2000
        allowance = 10000
    elif salary >= 60000:
        min_service = 0
        engine = 1800
        allowance = 8000
    elif salary >= 50000 and service >= 10:
        min_service = 10
        engine = 1800
        allowance = 8000
    elif salary >= 50000:
        min_service = 0
        engine = 1600
        allowance = 6000
    elif salary >= 40000 and service >= 20:
        min_service = 20
        engine = 1500
        allowance = 5000
    elif salary >= 30000 and servie >= 25:
        min_service = 25
        engine = 1400
        allowance = 4000
    else:
        print("Error:not eligible")
        exit()

    print("\n Eligible for a free car")
    print("Car Engine Capacity:", engine, "cc")
    print("Optional Car Allowance: Rs", allowance)