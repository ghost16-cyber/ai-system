d1 = float(input("d1="))
s1 = float(input("s1="))
d2 = float(input("d2="))
s2 = float(input("s2="))
d3 = float(input("d3="))
s3 = float(input("s3="))
total_distance_travelled = d1 + d2 + d3
print(f"total distance travel= {total_distance_travelled}km")
#to calculate total time taken travelled, we do total distance/total speed.#,
#but first we need to calculate total speed
total_speed = s1 + s2 + s3
print(f"total speed= {total_speed}km/h")
total_time_taken = total_distance_travelled / total_speed
print(f"Total time taken= {total_time_taken:.2f} hrs")
average_speed = total_distance_travelled / total_time_taken
print(f"average speed= {average_speed} km/h")


# def total_distance(d1, d2, d3):
#     return d1 + d2 + d3
#
#
# def total_time(d1, d3, s1, s3):
#     return abs((d3 - d1) / (s3 - s1))
#
#
# def avg_speed(d1, d2, d3, s1, s3):
#     return total_distance(d1, d2, d3) / total_time(d1, d3, s1, s3)
#
#
# print(f"total distance: {total_distance(d1, d2, d3)}")
# print(f"total time: {total_time(d1, d3, s1, s3)}")
# print(f"average speed: {avg_speed(d1, d2, d3, s1, s3)}")
