import streamlit as st
import ollama  # Импортируем библиотеку для связи с локальной Ollama

st.title("Локальный Чат (Ollama)")

# --- ЧАСТЬ 1: Настройки (Sidebar) ---
st.sidebar.title("Настройки")
# Важно: введите точное имя модели, как оно отображается в 'ollama list'
# Я поставил значение по умолчанию, но проверьте его у себя в терминале
model_name = st.sidebar.text_input("Имя модели в Ollama", value="gpt-oss:20b")

# Кнопка очистки истории
if st.sidebar.button("Очистить чат"):
    st.session_state.messages = []
    st.rerun()

# --- ЧАСТЬ 2: Session State ---
if "messages" not in st.session_state:
    st.session_state.messages = []

# --- ЧАСТЬ 3: Отображение Истории ---
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# --- ЧАСТЬ 4: Ввод и Обработка (Реальный вызов Ollama) ---
if prompt := st.chat_input("Напишите сообщение модели..."):
    
    # 1. Показываем сообщение пользователя
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 2. Получаем ответ от локальной модели
    with st.chat_message("assistant"):
        message_placeholder = st.empty()
        full_response = ""
        
        try:
            # Вызов Ollama с включенным стримингом (stream=True)
            # Мы передаем всю историю сообщений (st.session_state.messages), чтобы модель помнила контекст
            stream = ollama.chat(
                model=model_name,
                messages=st.session_state.messages,
                stream=True
            )
            
            # Читаем поток данных от модели в реальном времени
            for chunk in stream:
                content = chunk['message']['content']
                full_response += content
                # Обновляем текст на экране (+ курсор ▌ для красоты)
                message_placeholder.markdown(full_response + "▌")
            
            # Финальный вывод без курсора
            message_placeholder.markdown(full_response)
            
            # 3. Сохраняем ответ модели в историю
            st.session_state.messages.append({"role": "assistant", "content": full_response})

        except Exception as e:
            st.error(f"Ошибка подключения к Ollama: {e}")
            st.info("Совет: Убедитесь, что приложение Ollama запущено, а имя модели введено верно.")