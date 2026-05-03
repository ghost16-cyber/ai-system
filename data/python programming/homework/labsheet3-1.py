radius = float(input("radius of circle: "))
pi = 3.142

if radius <= 0:
    print("Error: radius must be positive values")
else:
    area_of_circle = pi * radius * radius
    print("area of circle",area_of_circle)
