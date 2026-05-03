radius = float(input("Enter radius= "))
height = float(input("Enter height= "))
pi = 3.142
r = radius
h = height
area_of_circle = pi * (r * r)
area_of_cylinder = 2 * pi * r * h
total_surface_area = (2 * area_of_circle) + area_of_cylinder
print(f"total surface area={total_surface_area}cm")
