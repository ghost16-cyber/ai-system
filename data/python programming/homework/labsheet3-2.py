year_of_birth = int(input("Enter year of birth:"))
age = 2025 - year_of_birth

if age < 18:
    print("you are a child of",age,"!")
else:
    print("you are an adult of",age,"year old !")