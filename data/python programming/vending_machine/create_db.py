import pymysql

conn = pymysql.connect(
    host="vending-db.cn4sicq46640.eu-north-1.rds.amazonaws.com",
    user="vm_user",
    password="palla16moon02",
    port=3306
)

cur = conn.cursor()
cur.execute("CREATE DATABASE vending_db;")
print("Database created!")
