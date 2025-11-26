import ollama
from textwrap import wrap

result = ollama.chat(
    model='gpt-oss:20b',
    messages=[{'role': 'user', 'content': 'Привет, ты кто такой?'}]
)

text = '\n'.join(wrap(result['message']['content'], 40))
print(text)