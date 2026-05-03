annual_income = int(input("annual allowance is(Rs):"))
dependants = int(input("number of dependants(0-3):"))
#this limit the dependants to 3 max
if dependants < 0:
    dependants = 0
elif dependants < 3:
    dependants = 3
#now to define allowance with dependants
if dependants == 0 :
    allowance = 255000
elif dependants == 1:
    allowance = 325000
elif dependants == 2:
    allowance = 395000
elif dependants == 3:
    allowance = 455000
else:
    allowance = None

taxable_income = annual_income - allowance

if taxable_income <= 0:
    tax = 0
    print(f'tax= {tax}')
elif taxable_income <= 50000:
    tax = taxable_income * 0.15
    print(f'tax= {tax}')
elif taxable_income <= 120000:
    tax = 50000 * 0.15
    print(f'tax= {tax}')
