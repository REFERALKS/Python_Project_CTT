import speech_recognition as sr
import whisper
import torch
import os

# Проверяем наличие видеокарты
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"CUDA доступна: {torch.cuda.is_available()}")
print(f"Модель будет загружена на: {device}")

# Инициализация (загружаем модель сразу на нужное устройство)
model = whisper.load_model("base", device=device)
recognizer = sr.Recognizer()

def listen_and_transcribe():
    with sr.Microphone() as source:
        print("Скажите что-нибудь (настройка шума 1 сек)...")
        recognizer.adjust_for_ambient_noise(source, duration=1)
        
        print("Слушаю...")
        try:
            # Запись аудио (слушает до паузы в речи)
            audio = recognizer.listen(source, timeout=5, phrase_time_limit=10)
            
            # Сохраняем во временный файл
            with open("temp_voice.wav", "wb") as f:
                f.write(audio.get_wav_data())
            
            # Транскрибация
            print(f"Распознаю на {device}...")
            
            # Если GPU (cuda) -> fp16=True (быстрее), Если CPU -> fp16=False (совместимость)
            use_fp16 = True if device == "cuda" else False
            
            result = model.transcribe("temp_voice.wav", fp16=use_fp16)
            text = result['text'].strip()
            
            print(f"Вы сказали: {text}")
            return text

        except sr.WaitTimeoutError:
            print("Тишина...")
            return None
        except Exception as e:
            print(f"Ошибка: {e}")
            return None
        finally:
            if os.path.exists("temp_voice.wav"):
                os.remove("temp_voice.wav")

if __name__ == "__main__":
    listen_and_transcribe()