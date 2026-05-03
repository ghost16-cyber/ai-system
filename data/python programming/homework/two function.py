def calculate_perimeter(w, l):
    perimeter = (2 * w) + (2 * l)
    return perimeter

def calculate_tsa(x, y, d):
    tsa = (2 * x) + (2 * y) + (2 * d)
    return tsa

print("perimeter is: ",calculate_perimeter(10,5))
print("total surface area : ",calculate_tsa(2, 3, 6))