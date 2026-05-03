# tariff = int(input("Chose tariff number (110, 120, 140): "))
# units = float(input("enter number of units consumed: "))
#
# if tariff == 110:
#     minimum_rate = 44.00
#     charged_rates = [2.75, 3.25, 4.00, 6.50]
# elif tariff == 120:
#     minimum_rate = 184.00
#     charged_rates = [ 3.00, 3.50, 4.25, 6.00]
# elif tariff == 140:
#     minimum_rate = 360.00
#     charged_rate = [3.25, 3.75, 4.50, 5.75]
# else:
#     print("invalid tariff number")

tariff_110 = [2.75, 3.25, 4.00, 6.50]
tariff_120 = [3.00, 3.50, 4.25, 6.00]
tariff_140 = [3.25, 3.75, 4.50, 5.75]
tariff_number = [110, 120, 140]
min_110 = 44
user_tariff = int(input(f'Choose tariff number: '))
user_unit = int(input(f'Choose unit consumed: '))

if user_tariff == 110:
    if user_unit <= 25:
        total_amount = min_110 + tariff_110[0]
    elif user_unit > 25 and user_unit <= 50:
        total_amount = min_110 + tariff_110[1]

for i in range(len(tariff_120)):
    if user_tariff == tariff_number[i]:

# tariff_number = {
#     "110": {
#         "minimum_charge": 44.00,
#         "charge_per_unit": [2.75, 3.25, 4.00, 6.50]
#     },
#     "120": {
#         "minimum_charge": 44.00,
#         "charge_per_unit": [3.00, 3.50, 4.25, 6.00]
#     },
#     "140": {
#         "minimum_charge": 44.00,
#         "charge_per_unit": [3.25, 3.75, 4.50, 5.75]
#     }
# }
#
# user_tariff = int(input("choose tariff number: "))
# user_units = float(input("number of unit: "))
#
# try:
#     if str(user_tariff) not in tariff_number:#110 or user_units != 120 or user_units != 140:
#         raise ValueError(f"Tariff number is in-existent. Please enter a valid tariff number: {110, 120, 140}")
# except ValueError as ve:
#     print(f"An error has occurred: {ve}")
#
# for key, values in tariff_number.items():
#     try:
#         if user_tariff == int(key):
#             if 0 <= user_units <= 25:
#                 total_amount = values["minimum_charge"] + values["charge_per_unit"][0]
#                 print(f"Total amount due is Rs{total_amount}")
#             elif 25 < user_units <= 75:
#                 total_amount = values["minimum_charge"] + values["charge_per_unit"][1]
#                 print(f"Total amount due is Rs{total_amount}")
#             elif 75 < user_units <= 150:
#                 total_amount = values["minimum_charge"] + values["charge_per_unit"][2]
#                 print(f"Total amount due is Rs{total_amount}")
#             elif user_units > 150:
#                 total_amount = values["minimum_charge"] + values["charge_per_unit"][3]
#                 print(f"Total amount due is Rs{total_amount}")
#             else:
#                 raise ValueError(f"Invalid input for charge per units")
#     except ValueError as ve:
#         print(f"An error has occurred: {ve}")