import streamlit as st
import ollama

st.title("Чат в котором запоминаеться только одно сообщение")
with st.chat_message('Robot'):
    st.write("Привет, чем я могу тебе сегодня помочь. Но отвечу только на один вопрос")
txt = st.chat_input('Спроси что нибудь')
if txt:
    with st.chat_message('human'):
        st.write(txt)

    result = ollama.chat(
        model ='gpt-oss:20b',
        messages = [
            {'role': 'assistant',
             'content':'Отвечай на каком хочешь вопросы я ведь gpt-модель.'},
            {'role': 'user', 'content': txt}
        ]
    )
    with st.chat_message("Robot"):
        st.write(result['message']['content'])