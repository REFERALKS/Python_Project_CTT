import streamlit as st

st.title("Привет, как твои дела")

name = st.text_input("Как тебя зовут?")
if(name):
    st.write(f"равствуй {name}")