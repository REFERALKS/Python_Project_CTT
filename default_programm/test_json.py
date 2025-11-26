import json

with open('D:\проекты\python_project\MY_Project_CTTIT\default_programm\test.json', 'r') as file:
    data = json.load(file)

print(data)

with open("test1.json", 'w') as file:
    json.dump(data, file)

data_as_json_string = json.dumps(data)
data_again = json.loads(data_as_json_string)
