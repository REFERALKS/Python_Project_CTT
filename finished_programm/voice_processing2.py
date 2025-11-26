import speech_recognition as sr
import whisper
import torch
import os
import ollama  # Официальная библиотека Ollama для Python
import pyttsx3 # Библиотека для TTS (озвучки)

# --- КОНФИГУРАЦИЯ ---
# Укажите точное название модели, как оно написано в 'ollama list'
LLM_MODEL = "gpt-oss:20b" 

# --- 1. Настройка Whisper (с поддержкой GPU) ---
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"1. Загрузка Whisper на устройство: {device}...")
stt_model = whisper.load_model("base", device=device)
fp16_setting = True if device == "cuda" else False

# --- 2. Настройка микрофона ---
recognizer = sr.Recognizer()

# --- 3. Настройка TTS (pyttsx3) ---
print("2. Инициализация голосового движка...")
tts_engine = pyttsx3.init()
# Можно настроить скорость и голос
tts_engine.setProperty('rate', 180) # Скорость речи

# --- ФУНКЦИИ ---

def ask_llm(text):
    """Отправляет текст в Ollama и возвращает ответ"""
    print(f"[LLM] Генерирую ответ с помощью {LLM_MODEL}...")
    try:
        response = ollama.chat(model=LLM_MODEL, messages=[
            {
                'role': 'user',
                'content': text,
            },
        ])
        return response['message']['content']
    except Exception as e:
        return f"Ошибка Ollama: {e}. Проверьте, запущена ли Ollama и скачана ли модель."

def text_to_speech(text):
    """Озвучивает текст"""
    print(f"[TTS] Озвучиваю...")
    try:
        tts_engine.say(text)
        tts_engine.runAndWait()
    except Exception as e:
        print(f"Ошибка TTS: {e}")

# --- ОСНОВНОЙ ЦИКЛ ---
def main():
    print(f"\n--- ГОЛОСОВОЙ АССИСТЕНТ ЗАПУЩЕН ({device}) ---")
    
    while True:
        with sr.Microphone() as source:
            print("\nСкажите что-нибудь...")
            # Быстрая калибровка шума (0.5 сек хватит для тихой комнаты)
            recognizer.adjust_for_ambient_noise(source, duration=2)
            
            try:
                # Слушаем (timeout=None значит ждать вечно, пока не начнут говорить)
                audio = recognizer.listen(source, timeout=None)
                
                # Сохраняем аудио
                with open("buffer.wav", "wb") as f:
                    f.write(audio.get_wav_data())
                
                # 1. Whisper: Аудио -> Текст
                print("Распознаю речь...")
                result = stt_model.transcribe("buffer.wav", fp16=fp16_setting)
                user_text = result['text'].strip()
                
                if not user_text:
                    print("Тишина...")
                    continue

                print(f"Вы сказали: {user_text}")
                
                # 2. Ollama: Текст -> Ответ ИИ
                ai_response = ask_llm(user_text)
                print(f"AI ответил: {ai_response}")
                
                # 3. Pyttsx3: Ответ ИИ -> Голос
                text_to_speech(ai_response)

            except KeyboardInterrupt:
                print("\nВыход из программы.")
                break
            except Exception as e:
                print(f"\nПроизошла ошибка: {e}")
                # Очистка файла в случае ошибки
                if os.path.exists("buffer.wav"):
                    os.remove("buffer.wav")

if __name__ == "__main__":
    main()