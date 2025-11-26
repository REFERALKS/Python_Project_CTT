import ollama
import streamlit as st

if not 'messages' in st.session_state:
    st.session_state['messages'] = [
        {'role': 'system',
         'content': 'Задай вопрос на любом языке!'}
    ]
st.title("Полноценный чат с ботом")
chat_panel = st.container()

def write_message(msg):
    with chat_panel:
        with st.chat_message(msg['role']):
            st.write(msg['content'])

for msg in st.session_state.messages:
    if msg['role'] != 'system':
        write_message(msg)

txt = st.chat_input('Напиши вопрос')
if txt:
    msg = {'role': 'user', 'content': txt}
    st.session_state.messages.append(msg)
    write_message(msg)

    result=ollama.chat(model='gpt-oss:20b', messages=st.session_state.messages)
    msg = result['message']
    write_message(msg)
    st.session_state.messages.append(msg)