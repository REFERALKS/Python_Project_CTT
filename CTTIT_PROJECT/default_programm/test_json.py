import json
import os

# Get the directory where the script is located
base_path = os.path.dirname(os.path.abspath(__file__))
file_path = os.path.join(base_path, 'test.json')

# Open the file using the absolute path
with open(file_path, 'r', encoding='utf-8') as file:
    data = json.load(file)

print(data)

# Save to test1.json in the same folder
output_path = os.path.join(base_path, 'test1.json')
with open(output_path, 'w', encoding='utf-8') as file:
    json.dump(data, file, ensure_ascii=False, indent=4)