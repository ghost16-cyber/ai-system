password = int(input("Enter password: "))
attempt = input("Enter your password: ")

while attempt != password:
    print("Incorrect password. Try again:")
    attempt = input("Enter password")