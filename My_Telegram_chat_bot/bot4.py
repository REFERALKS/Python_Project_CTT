from __future__ import annotations

import base64
import io
import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

import telebot
from telebot import types
from openai import OpenAI

try:
    from tiktoken import get_encoding  # type: ignore
except Exception:  # pragma: no cover
    get_encoding = None  # type: ignore


# -------------------- CONFIG --------------------

# SECURITY: не хардкодь токен в коде. Твой токен был засвечен — его нужно отозвать у @BotFather и выпустить новый.
API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8057312342:AAEpPXaXZdgWyfTOK3IAeTIChDNZy6pUKP0").strip()
if not API_TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN is not set. "
        "Set it in env vars. (Also rotate the leaked token if it was exposed.)"
    )

BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:1234/v1").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "lm-studio").strip()

HISTORY_FILE = os.getenv("HISTORY_FILE", "history.json").strip()
SETTINGS_FILE = os.getenv("SETTINGS_FILE", "settings.json").strip()

TOKEN_LIMIT = int(os.getenv("TOKEN_LIMIT", "16834"))

AVAILABLE_MODELS = {
    "ministral": "mistralai/ministral-3-14b-reasoning",
    "qwen_vl": "qwen/qwen3-vl-30b",
    "local_default": "local-model",
}

ROLES = {
    "default": "Ты полезный ассистент. Отвечай кратко и по делу.",
    "coder": "Ты senior python разработчик. Давай практичные и безопасные решения.",
    "translator": "Ты переводчик. Переводи точно и естественно.",
    "physicist": "Ты профессор физики. Объясняй строго, но понятно.",
    "creative": "Ты креативный писатель. Пиши образно, но по задаче.",
}

# Формат ответа: сохраняем совместимость с твоим парсером "ОТВЕТ:"
RESPONSE_FORMAT_INSTRUCTION = (
    "Отвечай строго в формате:\n"
    "ОТВЕТ: <твой ответ>\n"
    "Не добавляй другие секции."
)

# Оценка стоимости изображения (у разных VLM моделей по-разному, это heuristic)
IMAGE_TOKEN_ESTIMATE = int(os.getenv("IMAGE_TOKEN_ESTIMATE", "900"))

# Chat overhead heuristic (примерно)
TOKENS_PER_MESSAGE_OVERHEAD = 3
TOKENS_PRIMING_OVERHEAD = 3

# История: если одно сообщение слишком большое, обрезаем текст до этого лимита
MIN_TEXT_TOKENS_TO_KEEP = int(os.getenv("MIN_TEXT_TOKENS_TO_KEEP", "256"))

# Ротация history.json по размеру
HISTORY_ROTATE_MAX_BYTES = int(os.getenv("HISTORY_ROTATE_MAX_BYTES", str(5 * 1024 * 1024)))  # 5 MB
HISTORY_ROTATE_BACKUPS = int(os.getenv("HISTORY_ROTATE_BACKUPS", "3"))


# -------------------- LOGGING --------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("tg-bot")


# -------------------- LOW-LEVEL UTIL --------------------

def uid(user_id: Union[int, str]) -> str:
    """Стабильный ключ пользователя для JSON."""
    return str(user_id)


def atomic_write_json(path: str, data: Any) -> None:
    """Атомарная запись JSON: защищает от битых файлов при падении."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass


def rotate_file(path: str, max_bytes: int, backups: int) -> None:
    """
    Если файл path >= max_bytes, делаем ротацию:
      path.(backups) удаляем,
      path.(i-1) -> path.i,
      path -> path.1
    """
    if backups <= 0:
        return
    try:
        if not os.path.exists(path):
            return
        size = os.path.getsize(path)
        if size < max_bytes:
            return

        # Удаляем самый старый
        oldest = f"{path}.{backups}"
        if os.path.exists(oldest):
            try:
                os.remove(oldest)
            except Exception:
                pass

        # Сдвигаем
        for i in range(backups - 1, 0, -1):
            src = f"{path}.{i}"
            dst = f"{path}.{i+1}"
            if os.path.exists(src):
                try:
                    os.replace(src, dst)
                except Exception:
                    pass

        # Текущий -> .1
        os.replace(path, f"{path}.1")
        logger.info("Rotated %s (size=%d bytes) -> %s.1", path, size, path)
    except Exception as e:
        logger.warning("Rotation failed for %s: %s", path, e)


class JsonStore:
    """Потокобезопасное хранилище JSON с атомарной записью и опциональной ротацией по размеру."""

    def __init__(
        self,
        path: str,
        default: Any,
        rotate_max_bytes: Optional[int] = None,
        rotate_backups: int = 0,
    ):
        self.path = path
        self.default = default
        self.rotate_max_bytes = rotate_max_bytes
        self.rotate_backups = rotate_backups
        self._lock = threading.RLock()
        self.data = self._load()

    def _load(self) -> Any:
        if not os.path.exists(self.path):
            return self.default
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to load %s (%s). Using default.", self.path, e)
            return self.default

    def save(self) -> None:
        with self._lock:
            if self.rotate_max_bytes is not None:
                rotate_file(self.path, self.rotate_max_bytes, self.rotate_backups)
            atomic_write_json(self.path, self.data)

    def get(self) -> Any:
        with self._lock:
            return self.data


# -------------------- TOKEN ESTIMATION --------------------

@dataclass(frozen=True)
class TokenStatus:
    used: int
    left: int


class TokenEstimator:
    """
    Оценщик токенов:
    - tiktoken cl100k_base для текста (если доступно)
    - overhead на сообщения
    - фикс-оценка на изображения
    """

    def __init__(self) -> None:
        self._enc = None
        if get_encoding is not None:
            try:
                self._enc = get_encoding("cl100k_base")
            except Exception as e:
                logger.warning("tiktoken init failed: %s", e)
                self._enc = None

    def count_text_tokens(self, text: str) -> int:
        if not text:
            return 0
        if self._enc is None:
            # fallback rough
            return max(1, len(text) // 3)
        return len(self._enc.encode(text))

    def truncate_text_to_tokens_keep_tail(self, text: str, max_tokens: int) -> str:
        """Обрезает текст так, чтобы осталось максимум max_tokens, сохраняя хвост (самое свежее)."""
        if max_tokens <= 0:
            return ""
        if not text:
            return ""
        if self._enc is None:
            # fallback: примерно
            max_chars = max(1, max_tokens * 3)
            if len(text) <= max_chars:
                return text
            return "… " + text[-max_chars:]
        toks = self._enc.encode(text)
        if len(toks) <= max_tokens:
            return text
        cut = toks[-max_tokens:]
        out = self._enc.decode(cut)
        return "… " + out

    @staticmethod
    def _iter_text_blocks(content: Any) -> List[str]:
        if isinstance(content, str):
            return [content]
        if isinstance(content, list):
            out: List[str] = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
                    out.append(b["text"])
            return out
        return []

    @staticmethod
    def _count_images(content: Any) -> int:
        if isinstance(content, list):
            n = 0
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") in ("image_url", "telegram_photo"):
                    n += 1
            return n
        return 0

    def estimate_messages(self, messages: List[Dict[str, Any]]) -> int:
        total = TOKENS_PRIMING_OVERHEAD
        for m in messages:
            total += TOKENS_PER_MESSAGE_OVERHEAD
            content = m.get("content")
            for part in self._iter_text_blocks(content):
                total += self.count_text_tokens(part)
            total += self._count_images(content) * IMAGE_TOKEN_ESTIMATE
        return total


# -------------------- BOT STATE --------------------

settings_store = JsonStore(SETTINGS_FILE, default={})
history_store = JsonStore(
    HISTORY_FILE,
    default={},
    rotate_max_bytes=HISTORY_ROTATE_MAX_BYTES,
    rotate_backups=HISTORY_ROTATE_BACKUPS,
)

user_settings: Dict[str, Dict[str, Any]] = settings_store.get()
chat_histories: Dict[str, List[Dict[str, Any]]] = history_store.get()

token_estimator = TokenEstimator()

bot = telebot.TeleBot(API_TOKEN)
client = OpenAI(base_url=BASE_URL, api_key=OPENAI_API_KEY)


# -------------------- SETTINGS / PROMPTS --------------------

DEFAULT_CFG = {"role": "default", "temperature": 0.7, "model": "local_default"}


def get_settings(user_id: Union[int, str]) -> Dict[str, Any]:
    k = uid(user_id)
    if k not in user_settings or not isinstance(user_settings.get(k), dict):
        user_settings[k] = DEFAULT_CFG.copy()
        settings_store.save()
    for kk, vv in DEFAULT_CFG.items():
        user_settings[k].setdefault(kk, vv)
    return user_settings[k]


def system_prompt_for(user_id: Union[int, str]) -> str:
    s = get_settings(user_id)
    role_text = ROLES.get(s.get("role", "default"), ROLES["default"])
    return f"{role_text}\n\n{RESPONSE_FORMAT_INSTRUCTION}"


def init_history(user_id: Union[int, str]) -> None:
    k = uid(user_id)
    chat_histories[k] = [{"role": "system", "content": system_prompt_for(k)}]
    history_store.save()


# -------------------- SUMMARY / COMPRESSION --------------------

def _is_summary_msg(msg: Dict[str, Any]) -> bool:
    return msg.get("role") == "system" and isinstance(msg.get("content"), str) and msg["content"].startswith("[SUMMARY]")


def _is_ultra_msg(msg: Dict[str, Any]) -> bool:
    return msg.get("role") == "system" and isinstance(msg.get("content"), str) and msg["content"].startswith("[ULTRA]")


def _content_to_plain_text(msg: Dict[str, Any]) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and isinstance(b.get("text"), str):
                parts.append(b["text"])
            elif b.get("type") in ("image_url", "telegram_photo"):
                parts.append("[изображение]")
        return " ".join(p.strip() for p in parts if p).strip()
    return ""


def compress_summary(history_slice: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for msg in history_slice:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _content_to_plain_text(msg)
        if not text:
            continue
        parts.append(("U: " if role == "user" else "A: ") + text)
    return " | ".join(parts)


def compression_engine(user_id: Union[int, str]) -> None:
    k = uid(user_id)
    history = chat_histories.get(k)
    if not history:
        init_history(k)
        return

    # Ensure first system prompt exists
    if history[0].get("role") != "system":
        history.insert(0, {"role": "system", "content": system_prompt_for(k)})

    has_sum = any(_is_summary_msg(m) for m in history)
    has_ultra = any(_is_ultra_msg(m) for m in history)

    # Stage 1: add summary once
    if len(history) > 12 and not has_sum and not has_ultra:
        window = history[1:9]
        summary = compress_summary(window)
        history = [history[0], {"role": "system", "content": f"[SUMMARY] {summary}"}] + history[9:]
        chat_histories[k] = history

    # Stage 2: compress summary to ultra
    history = chat_histories[k]
    if len(history) > 18 and not any(_is_ultra_msg(m) for m in history):
        for i, m in enumerate(history):
            if _is_summary_msg(m):
                old = m["content"]
                compact = old.replace("[SUMMARY] ", "")
                if len(compact) > 240:
                    compact = compact[:240].rstrip() + "…"
                history[i] = {"role": "system", "content": f"[ULTRA] {compact}"}
                chat_histories[k] = history
                break

    history_store.save()


# -------------------- STRICT TOKEN BUDGET --------------------

def _extract_summary_msg(history: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for m in history[1:]:
        if _is_ultra_msg(m) or _is_summary_msg(m):
            return m
    return None


def _non_system_msgs(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [m for m in history if m.get("role") != "system"]


def _rebuild_history(sys0: Dict[str, Any], summary: Optional[Dict[str, Any]], tail: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = [sys0]
    if summary is not None:
        out.append(summary)
    out.extend(tail)
    return out


def enforce_token_budget_strict(user_id: Union[int, str]) -> None:
    """
    Строго приводит историю к TOKEN_LIMIT (по оценке).
    1) Оставляет system + (summary/ultra) + хвост диалога.
    2) Если всё равно слишком много — удаляет самые старые сообщения хвоста.
    3) Если даже одно последнее сообщение слишком большое — обрезает текст (keep-tail).
    """
    k = uid(user_id)
    history = chat_histories.get(k)
    if not history:
        init_history(k)
        return

    sys0 = history[0] if history and history[0].get("role") == "system" else {"role": "system", "content": system_prompt_for(k)}
    summary = _extract_summary_msg(history)

    tail = _non_system_msgs(history)

    # Сначала пробуем просто удалять старое
    candidate = _rebuild_history(sys0, summary, tail)
    while len(tail) > 1 and token_estimator.estimate_messages(candidate) > TOKEN_LIMIT:
        tail = tail[1:]
        candidate = _rebuild_history(sys0, summary, tail)

    # Если всё ещё много и остался 1 хвостовой message: пробуем обрезать текст
    if token_estimator.estimate_messages(candidate) > TOKEN_LIMIT and tail:
        last = tail[-1]
        content = last.get("content")

        # Берем текст из content и обрезаем
        def get_joined_text(c: Any) -> Optional[str]:
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                texts = []
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
                        texts.append(b["text"])
                joined = " ".join(t.strip() for t in texts if t and isinstance(t, str)).strip()
                return joined if joined else None
            return None

        joined_text = get_joined_text(content)
        if joined_text:
            # оценим, сколько токенов "съедает" всё кроме текста
            last_copy = dict(last)
            if isinstance(content, str):
                last_copy["content"] = ""
            else:
                # сохраняем блоки изображений, но убираем текст
                if isinstance(content, list):
                    kept_blocks = []
                    for b in content:
                        if isinstance(b, dict) and b.get("type") in ("image_url", "telegram_photo"):
                            kept_blocks.append(b)
                        elif isinstance(b, dict) and b.get("type") == "telegram_photo":
                            kept_blocks.append(b)
                    last_copy["content"] = kept_blocks
                else:
                    last_copy["content"] = ""

            base_candidate = _rebuild_history(sys0, summary, tail[:-1] + [last_copy])
            base_tokens = token_estimator.estimate_messages(base_candidate)
            allowance = TOKEN_LIMIT - base_tokens
            allowance = max(0, allowance)

            # гарантируем минимум, чтобы хоть что-то осталось
            allowance = max(allowance, MIN_TEXT_TOKENS_TO_KEEP)

            truncated = token_estimator.truncate_text_to_tokens_keep_tail(joined_text, allowance)

            # Записываем обратно в историю (не ломаем формат telegram_photo)
            if isinstance(content, str):
                last["content"] = truncated
            elif isinstance(content, list):
                new_blocks: List[Dict[str, Any]] = []
                # сохраним "telegram_photo" блок(и), если есть
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "telegram_photo":
                        # caption убираем, текст пойдет отдельным блоком
                        new_b = dict(b)
                        new_b.pop("caption", None)
                        new_blocks.append(new_b)
                new_blocks.append({"type": "text", "text": truncated})
                last["content"] = new_blocks

            tail[-1] = last
            candidate = _rebuild_history(sys0, summary, tail)

    chat_histories[k] = candidate
    history_store.save()


def get_token_status(user_id: Union[int, str]) -> TokenStatus:
    k = uid(user_id)
    history = chat_histories.get(k) or []
    used = token_estimator.estimate_messages(history)
    return TokenStatus(used=used, left=TOKEN_LIMIT - used)


# -------------------- STORAGE FORMAT (NO BASE64 IN JSON) --------------------

def store_user_text(user_id: str, text: str) -> None:
    chat_histories[user_id].append({"role": "user", "content": [{"type": "text", "text": text}]})
    history_store.save()


def store_user_photo(user_id: str, file_id: str, caption: str) -> None:
    chat_histories[user_id].append(
        {"role": "user", "content": [{"type": "telegram_photo", "file_id": file_id, "caption": caption}]}
    )
    history_store.save()


def store_assistant_text(user_id: str, text: str) -> None:
    chat_histories[user_id].append({"role": "assistant", "content": text})
    history_store.save()


def materialize_for_api(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    for msg in history:
        role = msg.get("role")
        content = msg.get("content")

        if isinstance(content, str) or content is None:
            out.append({"role": role, "content": content or ""})
            continue

        if isinstance(content, list):
            blocks: List[Dict[str, Any]] = []
            for b in content:
                if not isinstance(b, dict):
                    continue
                t = b.get("type")

                if t == "text":
                    text = b.get("text")
                    if isinstance(text, str) and text.strip():
                        blocks.append({"type": "text", "text": text})

                elif t == "image_url":
                    blocks.append(b)

                elif t == "telegram_photo":
                    file_id = b.get("file_id")
                    caption = b.get("caption") if isinstance(b.get("caption"), str) else ""
                    if isinstance(file_id, str) and file_id:
                        try:
                            file_info = bot.get_file(file_id)
                            downloaded = bot.download_file(file_info.file_path)
                            b64 = base64.b64encode(downloaded).decode("utf-8")
                            blocks.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
                        except Exception as e:
                            logger.warning("Failed to materialize image (file_id=%s): %s", file_id, e)
                            blocks.append({"type": "text", "text": "[изображение недоступно]"})
                    if caption.strip():
                        blocks.append({"type": "text", "text": caption})

            out.append({"role": role, "content": blocks if blocks else ""})
            continue

        out.append({"role": role, "content": str(content)})

    return out


# -------------------- UI (KEYBOARDS) --------------------

def main_menu_keyboard(user_id: Union[int, str]) -> types.InlineKeyboardMarkup:
    st = get_token_status(user_id)
    s = get_settings(user_id)

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton(f"🧠 Tokens: {st.used}/{TOKEN_LIMIT}", callback_data="show_tokens"))
    markup.add(types.InlineKeyboardButton("🗑️ Новый чат", callback_data="new_chat"))
    markup.add(types.InlineKeyboardButton(f"🎭 Роль: {s['role']}", callback_data="menu_roles"))
    markup.add(types.InlineKeyboardButton(f"🤖 Модель: {s['model']}", callback_data="menu_models"))
    markup.add(types.InlineKeyboardButton(f"🌡️ Temp: {s['temperature']}", callback_data="menu_temp"))
    return markup


def roles_keyboard(user_id: Union[int, str]) -> types.InlineKeyboardMarkup:
    current = get_settings(user_id)["role"]
    markup = types.InlineKeyboardMarkup()
    for r in ROLES.keys():
        mark = " ✅" if r == current else ""
        markup.add(types.InlineKeyboardButton(f"🎭 {r}{mark}", callback_data=f"set_role_{r}"))
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="main_menu"))
    return markup


def models_keyboard(user_id: Union[int, str]) -> types.InlineKeyboardMarkup:
    current = get_settings(user_id)["model"]
    markup = types.InlineKeyboardMarkup()
    for m in AVAILABLE_MODELS.keys():
        mark = " ✅" if m == current else ""
        markup.add(types.InlineKeyboardButton(f"🤖 {m}{mark}", callback_data=f"set_model_{m}"))
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="main_menu"))
    return markup


def temp_keyboard(user_id: Union[int, str]) -> types.InlineKeyboardMarkup:
    current = float(get_settings(user_id)["temperature"])
    markup = types.InlineKeyboardMarkup()
    for t in ["0.1", "0.3", "0.7", "1.0"]:
        mark = " ✅" if float(t) == current else ""
        markup.add(types.InlineKeyboardButton(f"🌡️ {t}{mark}", callback_data=f"set_temp_{t}"))
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="main_menu"))
    return markup


# -------------------- TELEGRAM UTILS --------------------

def safe_delete(chat_id: int, message_id: int) -> None:
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass


def safe_edit_text(chat_id: int, message_id: int, text: str, reply_markup: Optional[Any] = None) -> None:
    try:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup)
    except Exception:
        try:
            bot.send_message(chat_id, text, reply_markup=reply_markup)
        except Exception:
            pass


def send_long_message(chat_id: int, text: str, reply_markup: Optional[Any] = None) -> None:
    max_len = 3900
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] or [""]
    for i, chunk in enumerate(chunks):
        bot.send_message(chat_id, chunk, reply_markup=reply_markup if i == len(chunks) - 1 else None)


# -------------------- COMMANDS --------------------

@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message) -> None:
    user_id = uid(message.from_user.id)
    init_history(user_id)
    bot.reply_to(message, "⚙️ Панель управления:", reply_markup=main_menu_keyboard(user_id))


@bot.message_handler(commands=["export"])
def cmd_export(message: types.Message) -> None:
    """
    Экспортирует историю и настройки текущего пользователя в JSON и отправляет файлом.
    """
    user_id = uid(message.from_user.id)
    if user_id not in chat_histories:
        init_history(user_id)

    # перед экспортом приводим историю к нормальному виду
    compression_engine(user_id)
    enforce_token_budget_strict(user_id)

    payload = {
        "user_id": user_id,
        "settings": get_settings(user_id),
        "token_status_estimate": {
            "used": get_token_status(user_id).used,
            "left": get_token_status(user_id).left,
            "limit": TOKEN_LIMIT,
        },
        "history": chat_histories.get(user_id, []),
    }

    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    bio = io.BytesIO(data)
    bio.name = f"export_{user_id}.json"  # telebot использует name как filename

    bot.send_document(
        message.chat.id,
        bio,
        caption="📦 Экспорт истории и настроек (JSON).",
    )


# -------------------- CALLBACKS --------------------

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call: types.CallbackQuery) -> None:
    user_id = uid(call.from_user.id)
    s = get_settings(user_id)

    if call.data == "main_menu":
        safe_edit_text(call.message.chat.id, call.message.message_id, "⚙️ Меню:", reply_markup=main_menu_keyboard(user_id))

    elif call.data == "new_chat":
        init_history(user_id)
        bot.send_message(call.message.chat.id, "🧹 История очищена.", reply_markup=main_menu_keyboard(user_id))

    elif call.data == "menu_models":
        safe_edit_text(call.message.chat.id, call.message.message_id, "🤖 Выберите модель:", reply_markup=models_keyboard(user_id))

    elif call.data.startswith("set_model_"):
        s["model"] = call.data.replace("set_model_", "", 1)
        settings_store.save()
        safe_edit_text(call.message.chat.id, call.message.message_id, "✅ Модель установлена.", reply_markup=main_menu_keyboard(user_id))

    elif call.data == "menu_roles":
        safe_edit_text(call.message.chat.id, call.message.message_id, "🎭 Выберите роль:", reply_markup=roles_keyboard(user_id))

    elif call.data.startswith("set_role_"):
        s["role"] = call.data.replace("set_role_", "", 1)
        settings_store.save()
        init_history(user_id)
        safe_edit_text(call.message.chat.id, call.message.message_id, "✅ Роль применена.", reply_markup=main_menu_keyboard(user_id))

    elif call.data == "menu_temp":
        safe_edit_text(call.message.chat.id, call.message.message_id, "🌡️ Температура:", reply_markup=temp_keyboard(user_id))

    elif call.data.startswith("set_temp_"):
        try:
            s["temperature"] = float(call.data.replace("set_temp_", "", 1))
        except ValueError:
            s["temperature"] = DEFAULT_CFG["temperature"]
        settings_store.save()
        safe_edit_text(call.message.chat.id, call.message.message_id, "✅ Температура обновлена.", reply_markup=main_menu_keyboard(user_id))

    elif call.data == "show_tokens":
        compression_engine(user_id)
        enforce_token_budget_strict(user_id)
        st = get_token_status(user_id)
        bot.send_message(
            call.message.chat.id,
            f"🧠 Tokens (оценка): {st.used}/{TOKEN_LIMIT}\n"
            f"📉 Осталось (оценка): {st.left}\n\n"
            f"📦 Экспорт: /export",
            reply_markup=main_menu_keyboard(user_id),
        )


# -------------------- MAIN MESSAGE HANDLER --------------------

@bot.message_handler(content_types=["text", "photo"])
def handle_message(message: types.Message) -> None:
    user_id = uid(message.from_user.id)
    if user_id not in chat_histories:
        init_history(user_id)

    s = get_settings(user_id)

    # Компрессия + строгое ограничение по токенам ДО добавления нового сообщения
    compression_engine(user_id)
    enforce_token_budget_strict(user_id)

    model_api = AVAILABLE_MODELS.get(s["model"], AVAILABLE_MODELS["local_default"])
    loading = bot.reply_to(message, "⏳ Генерирую ответ...")

    try:
        # Сохраняем вход пользователя (без base64 в JSON)
        if message.content_type == "photo" and message.photo:
            file_id = message.photo[-1].file_id
            caption = (message.caption or "Опиши изображение").strip()
            store_user_photo(user_id, file_id=file_id, caption=caption)
        else:
            text = (message.text or "").strip()
            if not text:
                safe_delete(message.chat.id, loading.message_id)
                bot.send_message(message.chat.id, "Пустое сообщение.", reply_markup=main_menu_keyboard(user_id))
                return
            store_user_text(user_id, text=text)

        # Снова приводим к лимиту (теперь уже с новым сообщением)
        compression_engine(user_id)
        enforce_token_budget_strict(user_id)

        # Материализуем фото -> base64 только для запроса
        api_messages = materialize_for_api(chat_histories[user_id])

        completion = client.chat.completions.create(
            model=model_api,
            messages=api_messages,
            temperature=float(s["temperature"]),
        )

        response = completion.choices[0].message.content or ""
        if "ОТВЕТ:" in response:
            response = response.split("ОТВЕТ:", 1)[1].strip()

        safe_delete(message.chat.id, loading.message_id)
        send_long_message(message.chat.id, response, reply_markup=main_menu_keyboard(user_id))

        store_assistant_text(user_id, response)

        # На всякий случай — снова лимит, чтобы JSON не распухал
        compression_engine(user_id)
        enforce_token_budget_strict(user_id)

    except Exception as e:
        safe_delete(message.chat.id, loading.message_id)
        logger.exception("Error while handling message: %s", e)
        bot.send_message(message.chat.id, f"Ошибка: {e}", reply_markup=main_menu_keyboard(user_id))


if __name__ == "__main__":
    logger.info("BOT READY ✔")
    bot.polling(non_stop=True)