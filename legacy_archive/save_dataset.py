import csv

# Each pattern has 2-3 variations to ensure enough samples per class
rows = [
    # inefficient_loop variants
    ("for i in range(len(arr)): print(arr[i])", "inefficient_loop"),
    ("for j in range(len(items)): process(items[j])", "inefficient_loop"),
    ("for idx in range(len(data)): print(data[idx])", "inefficient_loop"),
    
    # good_loop variants
    ("for x in arr: print(x)", "good_loop"),
    ("for item in items: process(item)", "good_loop"),
    ("for element in data: print(element)", "good_loop"),
    
    # non_pythonic variants
    ("x = x + 1", "non_pythonic"),
    ("count = count + 5", "non_pythonic"),
    
    # pythonic variants
    ("x += 1", "pythonic"),
    ("count += 5", "pythonic"),
    
    # redundant_bool_compare variants
    ("if a == True:", "redundant_bool_compare"),
    ("if flag == False:", "redundant_bool_compare"),
    
    # good_bool_check variants
    ("if a:", "good_bool_check"),
    ("if not flag:", "good_bool_check"),
    
    # inefficient_append variants
    ("my_list = []\nfor i in range(10):\n    my_list.append(i)", "inefficient_append"),
    ("result = []\nfor x in data:\n    result.append(process(x))", "inefficient_append"),
    
    # good_list_creation variants
    ("my_list = list(range(10))", "good_list_creation"),
    ("result = [process(x) for x in data]", "good_list_creation"),
    
    # missing_docstring variants
    ("def foo():\n    pass", "missing_docstring"),
    ("def calculate(a, b):\n    return a + b", "missing_docstring"),
    
    # has_docstring variants
    ("def foo():\n    \"\"\"Do something.\"\"\"\n    pass", "has_docstring"),
    ("def calculate(a, b):\n    \"\"\"Add two numbers.\"\"\"\n    return a + b", "has_docstring"),
    
    # magic_number variants
    ("x = 3.1415926535", "magic_number"),
    ("threshold = 0.5", "magic_number"),
    
    # named_constant variants
    ("PI = 3.14159", "named_constant"),
    ("THRESHOLD = 0.5", "named_constant"),
    
    # len_check_nonzero variants
    ("if len(my_list) > 0:", "len_check_nonzero"),
    ("if len(data) != 0:", "len_check_nonzero"),
    
    # good_len_check variants
    ("if my_list:", "good_len_check"),
    ("if not data:", "good_len_check"),
    
    # bare_exception variants
    ("try:\n    risky()\nexcept Exception as e:\n    print(e)", "bare_exception"),
    ("try:\n    func()\nexcept Exception:\n    pass", "bare_exception"),
    
    # specific_exception variants
    ("try:\n    risky()\nexcept ValueError as e:\n    print(e)", "specific_exception"),
    ("try:\n    func()\nexcept FileNotFoundError:\n    handle_error()", "specific_exception"),
    
    # shallow_copy variants
    ("a = [1,2,3]\nb = a", "shallow_copy"),
    ("x = original_list", "shallow_copy"),
    
    # deep_copy variants
    ("b = a.copy()", "deep_copy"),
    ("import copy\nx = copy.deepcopy(original)", "deep_copy"),
    
    # while_true_break variants
    ("while True:\n    if condition:\n        break", "while_true_break"),
    ("while 1:\n    if done:\n        break", "while_true_break"),
    
    # unused_loop_var variants
    ("for _ in range(10):\n    do_something()", "unused_loop_var"),
    ("for _ in items:\n    process()", "unused_loop_var"),
    
    # good_none_check variants
    ("if x is None:", "good_none_check"),
    ("if value is not None:", "good_none_check"),
    
    # bad_none_check variants
    ("if x == None:", "bad_none_check"),
    ("if y != None:", "bad_none_check"),
    
    # star_import variants
    ("from module import *", "star_import"),
    ("from collections import *", "star_import"),
    
    # good_import variants
    ("from module import specific", "good_import"),
    ("import numpy as np", "good_import"),
    
    # python2_print variants
    ("print \"Hello\"", "python2_print"),
    ("print 'World'", "python2_print"),
    
    # python3_print variants
    ("print(\"Hello\")", "python3_print"),
    ("print('World')", "python3_print"),
    
    # good_json_load variants
    ("data = json.loads(s)", "good_json_load"),
    ("obj = json.load(fp)", "good_json_load"),
    
    # dangerous_eval variants
    ("data = eval(s)", "dangerous_eval"),
    ("result = eval(user_input)", "dangerous_eval"),
    
    # missing_encoding variants
    ("with open(file) as f:\n    data = f.read()", "missing_encoding"),
    ("with open('data.txt') as fp:\n    content = fp.read()", "missing_encoding"),
    
    # good_file_open variants
    ("with open(file, encoding=\"utf-8\") as f:\n    data = f.read()", "good_file_open"),
    ("with open('data.txt', encoding='utf-8') as fp:\n    content = fp.read()", "good_file_open"),
]

with open("code_patterns.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["code_snippet", "label"])
    for snippet, label in rows:
        writer.writerow([snippet, label])

print(f"Dataset written to code_patterns.csv with {len(rows)} examples")