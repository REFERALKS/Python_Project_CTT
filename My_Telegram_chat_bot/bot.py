import telebot
from telebot import types
from openai import OpenAI
import json
import os
import base64

# --- КОНФИГУРАЦИЯ ---
API_TOKEN = '8057312342:AAEpPXaXZdgWyfTOK3IAeTIChDNZy6pUKP0'

# СПИСОК РАЗРЕШЕННЫХ ЛЮДЕЙ
# Впишите сюда ID друзей через запятую
ALLOWED_USERS = [
    5178568186, # Ваш ID
    1848889256, # ID САША
    #987654321  # ID
]

HISTORY_FILE = 'history.json'
SETTINGS_FILE = 'settings.json'

# --- СПИСОК МОДЕЛЕЙ ---
AVAILABLE_MODELS = {
    "ministral": "mistralai/ministral-3-14b-reasoning",
    "qwen_vl": "qwen/qwen3-vl-30b",
    "local_default": "local-model"
}

# Настройки подключения
BASE_URL = "http://localhost:1234/v1"

bot = telebot.TeleBot(API_TOKEN)
client = OpenAI(base_url=BASE_URL, api_key="lm-studio")

# --- РОЛИ И ПРОМПТЫ ---
ROLES = {
    "default": "Ты полезный и умный ассистент.",
    "coder": "Ты опытный Senior Python разработчик. Пиши чистый код. Объясняй кратко.",
    "translator": "Ты профессиональный переводчик. Переводи текст точно.",
    "physicist": "Ты профессор физики. Объясняй сложные явления доступным языком.",
    "creative": "Ты креативный писатель. Используй богатый литературный язык."
}

THINKING_INSTRUCTION = (
    "\nВАЖНО: Ты должен показать свой мыслительный процесс."
    "\nФормат ответа строго такой:"
    "\nМЫСЛИ: [Твои рассуждения]"
    "\nОТВЕТ: [Твой финальный ответ]"
)

# --- РАБОТА С ФАЙЛАМИ (JSON) ---

def load_json(filename, default_data):
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {int(k) if k.isdigit() else k: v for k, v in data.items()}
        except:
            return default_data
    return default_data

def save_json(filename, data):
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

chat_histories = load_json(HISTORY_FILE, {})
user_settings = load_json(SETTINGS_FILE, {})

# --- ПРОВЕРКА ДОСТУПА ---

def is_allowed(user_id):
    """Проверяет, есть ли пользователь в списке разрешенных."""
    return user_id in ALLOWED_USERS

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---

def encode_image(file_path):
    """Кодирует картинку в base64 для отправки в LM Studio."""
    with open(file_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def get_settings(user_id):
    if user_id not in user_settings:
        user_settings[user_id] = {
            "role": "default", 
            "temperature": 0.7, 
            "model": "local_default"
        }
        save_json(SETTINGS_FILE, user_settings)
    
    if "model" not in user_settings[user_id]:
        user_settings[user_id]["model"] = "local_default"
        
    return user_settings[user_id]

def get_system_prompt(user_id):
    settings = get_settings(user_id)
    role_text = ROLES.get(settings["role"], ROLES["default"])
    return role_text + THINKING_INSTRUCTION

def init_history(user_id):
    sys_prompt = get_system_prompt(user_id)
    chat_histories[user_id] = [{"role": "system", "content": sys_prompt}]
    save_json(HISTORY_FILE, chat_histories)

# --- КЛАВИАТУРЫ ---

def main_menu_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🆕 Новый чат", callback_data="new_chat"),
        types.InlineKeyboardButton("📜 История", callback_data="show_history"),
        types.InlineKeyboardButton("🎭 Роль", callback_data="menu_roles"),
        types.InlineKeyboardButton("🤖 Модель", callback_data="menu_models"),
        types.InlineKeyboardButton("🌡️ Температура", callback_data="menu_temp")
    )
    return markup

def roles_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [types.InlineKeyboardButton(k.capitalize(), callback_data=f"set_role_{k}") for k in ROLES.keys()]
    markup.add(*buttons)
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="main_menu"))
    return markup

def models_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    buttons = []
    for friendly_name, model_id in AVAILABLE_MODELS.items():
        buttons.append(types.InlineKeyboardButton(f"🖥️ {friendly_name}", callback_data=f"set_model_{friendly_name}"))
    markup.add(*buttons)
    markup.add(types.InlineKeyboardButton("🔙 Назад", callback_data="main_menu"))
    return markup

def temp_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        types.InlineKeyboardButton("0.1 (Строгий)", callback_data="set_temp_0.1"),
        types.InlineKeyboardButton("0.3 (Четкий)", callback_data="set_temp_0.3"),
        types.InlineKeyboardButton("0.7 (Баланс)", callback_data="set_temp_0.7"),
        types.InlineKeyboardButton("1.0 (Креатив)", callback_data="set_temp_1.0"),
        types.InlineKeyboardButton("🔙 Назад", callback_data="main_menu")
    )
    return markup

# --- ОБРАБОТЧИКИ КОМАНД ---

@bot.message_handler(commands=['id'])
def show_id(message):
    """Показывает ID пользователя, чтобы он мог скинуть его админу."""
    bot.reply_to(message, f"Ваш ID: `{message.from_user.id}`", parse_mode='Markdown')

@bot.message_handler(commands=['start'])
def send_welcome(message):
    if not is_allowed(message.from_user.id):
        bot.reply_to(message, "⛔ У вас нет доступа к этому боту. Напишите владельцу.\nВаш ID: " + str(message.from_user.id))
        return

    current_model = get_settings(message.from_user.id)["model"]
    bot.reply_to(message, f"🤖 Привет! Это твой ИИ сервер.\nТекущая модель: **{current_model}**", 
                 parse_mode="Markdown", reply_markup=main_menu_keyboard())

# --- ОБРАБОТЧИКИ CALLBACK ---

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    if not is_allowed(user_id): return

    settings = get_settings(user_id)

    if call.data == "main_menu":
        bot.edit_message_text(f"🤖 Панель управления.\nМодель: {settings['model']}", 
                              call.message.chat.id, call.message.message_id, reply_markup=main_menu_keyboard())

    elif call.data == "new_chat":
        init_history(user_id)
        bot.send_message(call.message.chat.id, "🧹 История очищена!", reply_markup=main_menu_keyboard())

    elif call.data == "menu_models":
        bot.edit_message_text("🤖 Выберите модель из списка:", call.message.chat.id, call.message.message_id, reply_markup=models_keyboard())

    elif call.data.startswith("set_model_"):
        model_key = call.data.split("set_model_")[1]
        real_model_name = AVAILABLE_MODELS.get(model_key, "local-model")
        settings["model"] = model_key 
        save_json(SETTINGS_FILE, user_settings)
        bot.answer_callback_query(call.id, f"Модель изменена на {model_key}")
        bot.edit_message_text(f"✅ Выбрана модель: **{model_key}**\nID: `{real_model_name}`", 
                              call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif call.data == "menu_roles":
        bot.edit_message_text("🎭 Выберите роль:", call.message.chat.id, call.message.message_id, reply_markup=roles_keyboard())

    elif call.data.startswith("set_role_"):
        new_role = call.data.split("_")[2]
        settings["role"] = new_role
        save_json(SETTINGS_FILE, user_settings)
        init_history(user_id)
        bot.answer_callback_query(call.id, f"Роль {new_role} установлена!")
        bot.edit_message_text(f"✅ Роль: **{new_role}**.", call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=main_menu_keyboard())

    elif call.data == "menu_temp":
        bot.edit_message_text(f"🌡️ Текущая: {settings['temperature']}", call.message.chat.id, call.message.message_id, reply_markup=temp_keyboard())

    elif call.data.startswith("set_temp_"):
        new_temp = float(call.data.split("_")[2])
        settings["temperature"] = new_temp
        save_json(SETTINGS_FILE, user_settings)
        bot.answer_callback_query(call.id, "Температура обновлена")
        bot.edit_message_text(f"✅ Температура: {new_temp}", call.message.chat.id, call.message.message_id, reply_markup=main_menu_keyboard())
    
    elif call.data == "show_history":
        history = chat_histories.get(user_id, [])
        bot.send_message(call.message.chat.id, f"Сообщений в памяти: {len(history)}")

# --- ОБРАБОТКА СООБЩЕНИЙ ---

@bot.message_handler(content_types=['text', 'photo'])
def handle_message(message):
    user_id = message.from_user.id
    
    if not is_allowed(user_id):
        bot.reply_to(message, "⛔ Доступ запрещен. Обратитесь к администратору бота.")
        return

    if user_id not in chat_histories: init_history(user_id)

    history = chat_histories[user_id]
    settings = get_settings(user_id)
    
    model_key = settings.get("model", "local_default")
    model_api_name = AVAILABLE_MODELS.get(model_key, "local-model")

    temp_msg = bot.reply_to(message, f"🧠 {model_key} думает...")

    try:
        new_content = []
        
        if message.photo:
            file_info = bot.get_file(message.photo[-1].file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            # Временно сохраняем файл для кодирования
            temp_file_path = f"{file_info.file_id}.jpg"
            with open(temp_file_path, 'wb') as new_file:
                new_file.write(downloaded_file)
                
            base64_image = encode_image(temp_file_path)
            os.remove(temp_file_path) # Удаляем временный файл
            
            new_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}})
            text_prompt = message.caption if message.caption else "Опиши это изображение."
            new_content.append({"type": "text", "text": text_prompt})
        elif message.text:
            new_content = message.text

        history.append({"role": "user", "content": new_content})
        save_json(HISTORY_FILE, chat_histories)

        completion = client.chat.completions.create(
            model=model_api_name, 
            messages=history,
            temperature=settings['temperature'],
            stream=False
        )

        full_response = completion.choices[0].message.content
        
        if "МЫСЛИ:" in full_response and "ОТВЕТ:" in full_response:
            parts = full_response.split("ОТВЕТ:")
            thoughts = parts[0].replace("МЫСЛИ:", "").strip()
            answer = parts[1].strip()
            
            # --- ИСПОЛЬЗУЕМ HTML РАЗМЕТКУ ---
            final_output = (
                f"💭 <b>Мысли ({model_key}):</b>\n"
                f"<code>{thoughts}</code>\n\n"
                f"🗣️ <b>Ответ:</b>\n{answer}"
            )
        else:
            final_output = full_response

        bot.delete_message(message.chat.id, temp_msg.message_id)
        # --- СМЕНА РЕЖИМА НА HTML ---
        bot.reply_to(message, final_output, parse_mode="HTML") 

        history.append({"role": "assistant", "content": full_response})
        save_json(HISTORY_FILE, chat_histories)

    except Exception as e:
        error_text = f"❌ Ошибка: {e}"
        bot.edit_message_text(error_text, message.chat.id, temp_msg.message_id)
        if len(history) > 1:
            history.pop()
            save_json(HISTORY_FILE, chat_histories)

print(f"✅ Бот запущен. Разрешено пользователей: {len(ALLOWED_USERS)}")
bot.polling(non_stop=True)