h1 = float(input("height of small hole,h1="))
r1 = float(input("radius of small hole,r1="))
h2 = float(input("height of big hole,h2="))
r2 = float(input("radius of big hole,r2="))
h = h1 + h2
r = r1 + r2
pi = 3.142
volume_of_cylinder = pi * r * r * h
volume_of_two_hole = (pi * r1 * r1 * h1 ) + (pi * r2 * r2 * h2)
print(volume_of_cylinder)
print(volume_of_two_hole)
volume_of_concrete= volume_of_cylinder - volume_of_two_hole
print(volume_of_concrete)
