import streamlit as st
import ollama

# --- 1. Настройка интерфейса и Session State ---

st.title("Чат с локальной моделью (Ollama)")
st.caption("Подключено к локальной LLM через Ollama")

# --- Настройки в Sidebar ---
st.sidebar.title("Настройки Ollama")

# Поле для ввода имени модели. По умолчанию 'gpt-oss-20b'
model_name = st.sidebar.text_input(
    "Имя модели в Ollama", 
    value="gpt-oss:20b",
    help="Проверьте точное имя командой 'ollama list'"
)

# Кнопка очистки истории (сбрасывает Session State)
if st.sidebar.button("Очистить чат"):
    st.session_state.messages = []
    # Перезапуск, чтобы обновить интерфейс
    st.rerun()

# --- 2. Инициализация Session State ---
# Создаем список сообщений, если его еще нет
if "messages" not in st.session_state:
    # Добавим приветственное сообщение от ассистента
    st.session_state.messages = [
        {"role": "assistant", "content": f"Привет! Я готова начать. Использую модель: {model_name}"}
    ]

# --- 3. Отображение Истории ---
# Проходим по истории и выводим все старые сообщения
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# --- 4. Обработка Ввода и Вызов LLM ---

# Ждем ввод от пользователя
if prompt := st.chat_input("Ваш вопрос..."):
    
    # 4а. Отображаем сообщение пользователя и добавляем его в историю
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 4б. Вызов LLM
    with st.chat_message("assistant"):
        message_placeholder = st.empty() # Контейнер для стриминга
        full_response = ""
        
        try:
            # Вызов ollama.chat с полной историей и стримингом
            stream = ollama.chat(
                model=model_name,
                # Передаем всю историю для поддержания контекста
                messages=st.session_state.messages,
                stream=True
            )
            
            # Читаем поток ответа по частям (стриминг)
            for chunk in stream:
                content = chunk['message']['content']
                full_response += content
                # Динамически обновляем текст
                message_placeholder.markdown(full_response + "▌")
            
            # Финальный вывод (убираем курсор)
            message_placeholder.markdown(full_response)
            
            # 4в. Сохраняем ответ модели в историю
            st.session_state.messages.append({"role": "assistant", "content": full_response})

        except Exception as e:
            error_message = f"Ошибка: Не удалось подключиться к модели '{model_name}'. Проверьте, что Ollama запущен и модель загружена. Детали: {e}"
            st.error(error_message)
            # Если произошла ошибка, удаляем последнее сообщение пользователя, чтобы не сохранять его без ответа
            st.session_state.messages.pop()