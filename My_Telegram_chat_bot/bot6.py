from __future__ import annotations

import atexit
import base64
import copy
import io
import json
import logging
import os
import signal
import tempfile
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple, Union

import telebot
from telebot import types
from openai import OpenAI

try:
    from tiktoken import get_encoding  # type: ignore
except Exception:  # pragma: no cover
    get_encoding = None  # type: ignore


# =============================================================================
# CONFIG
# =============================================================================

# NOTE: токен у тебя был засвечен — лучше перевыпустить у @BotFather.
API_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "8057312342:AAEpPXaXZdgWyfTOK3IAeTIChDNZy6pUKP0",
).strip()
if not API_TOKEN:
    raise RuntimeError("Bot token is empty. Set TELEGRAM_BOT_TOKEN or hardcode API_TOKEN.")

BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:1234/v1").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "lm-studio").strip()

HISTORY_FILE = os.getenv("HISTORY_FILE", "history.json").strip()
SETTINGS_FILE = os.getenv("SETTINGS_FILE", "settings.json").strip()

TOKEN_LIMIT = int(os.getenv("TOKEN_LIMIT", "16834"))

# Владелец бота (для приоритета).
BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "5178568186"))

# --- Надёжность/нагрузка ---
MAX_ACTIVE_GLOBAL = int(os.getenv("MAX_ACTIVE_GLOBAL", "1"))   # сколько задач одновременно на весь бот/ПК
WORKER_COUNT = int(os.getenv("WORKER_COUNT", "1"))             # сколько worker-потоков (обычно 1 на 1 ПК)

USER_MIN_INTERVAL_SEC = float(os.getenv("USER_MIN_INTERVAL_SEC", "2.0"))          # минимум секунд между постановками
USER_MAX_PER_MINUTE = int(os.getenv("USER_MAX_PER_MINUTE", "12"))                 # максимум задач в минуту
MAX_PENDING_PER_USER = int(os.getenv("MAX_PENDING_PER_USER", "5"))

# Telegram polling
SKIP_PENDING_UPDATES = os.getenv("SKIP_PENDING_UPDATES", "1").strip().lower() in ("1", "true", "yes")
POLLING_TIMEOUT = int(os.getenv("POLLING_TIMEOUT", "20"))
LONG_POLLING_TIMEOUT = int(os.getenv("LONG_POLLING_TIMEOUT", "20"))

# Дедуп сообщений (защита от повторных апдейтов)
DEDUP_TTL_SEC = float(os.getenv("DEDUP_TTL_SEC", "120"))
DEDUP_CACHE_SIZE = int(os.getenv("DEDUP_CACHE_SIZE", "2000"))

# LLM timeouts / retries
LLM_TIMEOUT_SEC = float(os.getenv("LLM_TIMEOUT_SEC", "120"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
LLM_RETRY_BACKOFF_SEC = float(os.getenv("LLM_RETRY_BACKOFF_SEC", "0.8"))

# Circuit breaker
CB_FAILURE_THRESHOLD = int(os.getenv("CB_FAILURE_THRESHOLD", "3"))     # сколько подряд ошибок, чтобы "открыться"
CB_RESET_TIMEOUT_SEC = float(os.getenv("CB_RESET_TIMEOUT_SEC", "20"))  # сколько секунд "открыт" после ошибок

# ---- Token estimation (heuristics) ----
IMAGE_TOKEN_ESTIMATE = int(os.getenv("IMAGE_TOKEN_ESTIMATE", "900"))
TOKENS_PER_MESSAGE_OVERHEAD = 3
TOKENS_PRIMING_OVERHEAD = 3
MIN_TEXT_TOKENS_TO_KEEP = int(os.getenv("MIN_TEXT_TOKENS_TO_KEEP", "256"))

# ---- JSON rotation ----
HISTORY_ROTATE_MAX_BYTES = int(os.getenv("HISTORY_ROTATE_MAX_BYTES", str(5 * 1024 * 1024)))  # 5 MB
HISTORY_ROTATE_BACKUPS = int(os.getenv("HISTORY_ROTATE_BACKUPS", "3"))

# ---- Priority ----
IMAGE_PRIORITY_PENALTY = int(os.getenv("IMAGE_PRIORITY_PENALTY", "3000"))
TOKENS_PRIORITY_WEIGHT = int(os.getenv("TOKENS_PRIORITY_WEIGHT", "2"))
USED_TOKENS_WEIGHT = int(os.getenv("USED_TOKENS_WEIGHT", "1"))

# ---- Smart stop ----
SMART_STOP_DISCARD_PARTIAL = os.getenv("SMART_STOP_DISCARD_PARTIAL", "1").strip().lower() in ("1", "true", "yes")

# ---- Memory ----
MAX_MEMORY_ITEMS = int(os.getenv("MAX_MEMORY_ITEMS", "20"))
MAX_MEMORY_ITEM_LEN = int(os.getenv("MAX_MEMORY_ITEM_LEN", "500"))

# ---- LM Studio model id cache ----
MODEL_ID_TTL_SEC = float(os.getenv("MODEL_ID_TTL_SEC", "30"))

# ---- Фото: авто-описание только если caption пустой ----
AUTO_IMAGE_DESCRIPTION_5S = (
    "Опиши изображение примерно 7–10 осмысленными предложениями, без списков. "
    "Укажи: что в целом изображено, ключевые объекты/действия, важные детали (текст/цифры/ошибки, если есть) "
    "и один полезный вывод."
)

# ---- Промпты ----
EXECUTION_GUIDE = (
    "Политика выполнения:\n"
    "1) Не отлынивай. Если запрос безопасный и выполнимый — выполняй.\n"
    "2) Если не хватает данных — сделай разумные допущения и явно их перечисли, "
    "а затем задай 1–3 уточняющих вопроса.\n"
    "3) Давай конкретный результат: шаги, команды, код, таблицу, чек-лист — что уместно.\n"
    "4) Не выдумывай факты. Если нужно проверить — скажи, что именно проверить и как.\n"
    "5) Если пользователь прислал изображение и написал текст/подпись — НЕ давай отдельное описание изображения, "
    "если он прямо не попросил. Фокусируйся на задаче из текста.\n"
    "6) Если запрос связан с причинением вреда (взлом, мошенничество, вредонос, кража данных и т.п.) — откажись и "
    "предложи безопасную альтернативу.\n"
)

ROLES: Dict[str, str] = {
    "default": (
        "Ты полезный, вежливый и точный ассистент.\n"
        "Цель: быстро помогать пользователю решать задачу.\n"
        "Стиль: ясно, структурно, без воды.\n"
        "Правила:\n"
        "1) Если информации недостаточно — задай 1–3 уточняющих вопроса.\n"
        "2) Отвечай кратко, но полно: не пропускай критичные детали.\n"
        "3) Если есть варианты — предложи 2–3 и порекомендуй лучший.\n"
        "4) Не выдумывай факты. Если не уверен — скажи и предложи как проверить.\n"
        "5) Для инструкций используй нумерацию и короткие пункты.\n"
    ),
    "coder": (
        "Ты Senior Python Software Engineer с сильной базой в CS, архитектуре и безопасности.\n"
        "Цель: давать решения уровня production.\n"
        "Правила:\n"
        "1) Пиши чистый код (DRY/KISS/SOLID), осмысленные имена.\n"
        "2) Безопасность: валидация ввода, избегай инъекций, секреты не в коде.\n"
        "3) Надёжность: обработка ошибок, таймауты, ретраи где уместно.\n"
        "4) Производительность: Big-O, правильные структуры данных.\n"
        "5) Если пишешь код — докстринги, короткие комментарии к нетривиальному.\n"
        "Формат:\n"
        "- Короткий план\n"
        "- Полный рабочий код\n"
        "- Краткое объяснение решений\n"
    ),
    "translator": (
        "Ты профессиональный переводчик.\n"
        "Цель: точный и естественный перевод с сохранением смысла, стиля и тональности.\n"
        "Правила:\n"
        "1) Не добавляй отсебятину.\n"
        "2) Учитывай контекст (технический/деловой/разговорный).\n"
        "3) Термины и имена собственные — единообразно.\n"
        "4) Для техтекста сохраняй форматирование, код/команды.\n"
        "5) Если двусмысленно — дай 2 варианта и разницу.\n"
    ),
    "physicist": (
        "Ты профессор физики.\n"
        "Цель: строгие, проверяемые объяснения и решения.\n"
        "Правила:\n"
        "1) Сначала формулировка и допущения.\n"
        "2) Законы/уравнения и почему применимы.\n"
        "3) Пошаговый вывод без воды.\n"
        "4) Проверка размерностей/предельных случаев.\n"
    ),
    "creative": (
        "Ты креативный писатель и редактор.\n"
        "Цель: выразительный текст под задачу пользователя.\n"
        "Правила:\n"
        "1) Учитывай жанр, тон, аудиторию, длину.\n"
        "2) Пиши образно, но не уходи от запроса.\n"
        "3) Держи структуру.\n"
    ),
}

RESPONSE_FORMAT_INSTRUCTION = (
    "Отвечай строго в формате:\n"
    "ОТВЕТ: <твой ответ>\n"
    "Не добавляй другие секции."
)


# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("tg-bot")


# =============================================================================
# GLOBAL STATE / SHUTDOWN
# =============================================================================

START_TS = time.time()
SHUTDOWN_EVENT = threading.Event()
ACCEPTING_JOBS = True

# recent errors (for /status)
RECENT_ERRORS: Deque[Tuple[float, str]] = deque(maxlen=50)


def record_error(msg: str) -> None:
    RECENT_ERRORS.append((time.time(), msg))


# =============================================================================
# LOCKS / STATE
# =============================================================================

STATE_LOCK = threading.RLock()   # chat_histories + user_settings
SCHED_LOCK = threading.RLock()   # scheduling structures
SCHED_COND = threading.Condition(SCHED_LOCK)

_job_id_lock = threading.Lock()
_job_id_seq = 0


def next_job_id() -> int:
    global _job_id_seq
    with _job_id_lock:
        _job_id_seq += 1
        return _job_id_seq


# =============================================================================
# LOW-LEVEL UTIL
# =============================================================================

def uid(user_id: Union[int, str]) -> str:
    return str(user_id)


def atomic_write_json(path: str, data: Any) -> None:
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
    if backups <= 0:
        return
    try:
        if not os.path.exists(path):
            return
        size = os.path.getsize(path)
        if size < max_bytes:
            return

        oldest = f"{path}.{backups}"
        if os.path.exists(oldest):
            try:
                os.remove(oldest)
            except Exception:
                pass

        for i in range(backups - 1, 0, -1):
            src = f"{path}.{i}"
            dst = f"{path}.{i + 1}"
            if os.path.exists(src):
                try:
                    os.replace(src, dst)
                except Exception:
                    pass

        os.replace(path, f"{path}.1")
        logger.info("Rotated %s (size=%d bytes)", path, size)
    except Exception as e:
        logger.warning("Rotation failed for %s: %s", path, e)


class JsonStore:
    def __init__(self, path: str, default: Any, rotate_max_bytes: Optional[int] = None, rotate_backups: int = 0):
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


# =============================================================================
# TOKEN ESTIMATION
# =============================================================================

@dataclass(frozen=True)
class TokenStatus:
    used: int
    left: int


class TokenEstimator:
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
            return max(1, len(text) // 3)
        return len(self._enc.encode(text))

    def truncate_text_to_tokens_keep_tail(self, text: str, max_tokens: int) -> str:
        if max_tokens <= 0 or not text:
            return ""
        if self._enc is None:
            max_chars = max(1, max_tokens * 3)
            if len(text) <= max_chars:
                return text
            return "… " + text[-max_chars:]
        toks = self._enc.encode(text)
        if len(toks) <= max_tokens:
            return text
        return "… " + self._enc.decode(toks[-max_tokens:])

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


token_estimator = TokenEstimator()


# =============================================================================
# BOT STATE (JSON)
# =============================================================================

settings_store = JsonStore(SETTINGS_FILE, default={})
history_store = JsonStore(
    HISTORY_FILE,
    default={},
    rotate_max_bytes=HISTORY_ROTATE_MAX_BYTES,
    rotate_backups=HISTORY_ROTATE_BACKUPS,
)

user_settings: Dict[str, Dict[str, Any]] = settings_store.get()
chat_histories: Dict[str, List[Dict[str, Any]]] = history_store.get()

bot = telebot.TeleBot(API_TOKEN)

# OpenAI client (LM Studio)
try:
    client = OpenAI(base_url=BASE_URL, api_key=OPENAI_API_KEY, timeout=LLM_TIMEOUT_SEC)  # type: ignore[arg-type]
except TypeError:
    client = OpenAI(base_url=BASE_URL, api_key=OPENAI_API_KEY)


# =============================================================================
# CIRCUIT BREAKER
# =============================================================================

class CircuitBreaker:
    def __init__(self, failure_threshold: int, reset_timeout_sec: float) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.reset_timeout_sec = max(1.0, reset_timeout_sec)
        self._lock = threading.RLock()
        self._fail_streak = 0
        self._opened_at: Optional[float] = None

    def on_success(self) -> None:
        with self._lock:
            self._fail_streak = 0
            self._opened_at = None

    def on_failure(self) -> None:
        with self._lock:
            self._fail_streak += 1
            if self._fail_streak >= self.failure_threshold and self._opened_at is None:
                self._opened_at = time.time()

    def is_open(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            if (time.time() - self._opened_at) >= self.reset_timeout_sec:
                # half-open: allow one try (reset opened_at but keep fail streak)
                self._opened_at = None
                return False
            return True

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "open": self._opened_at is not None and (time.time() - self._opened_at) < self.reset_timeout_sec,
                "fail_streak": self._fail_streak,
                "opened_at": self._opened_at,
                "reset_timeout_sec": self.reset_timeout_sec,
                "failure_threshold": self.failure_threshold,
            }


CB = CircuitBreaker(CB_FAILURE_THRESHOLD, CB_RESET_TIMEOUT_SEC)


# =============================================================================
# SAFE OPENAI CALLS (timeouts/retries)
# =============================================================================

def _openai_models_list() -> Any:
    try:
        return client.models.list(timeout=LLM_TIMEOUT_SEC)  # type: ignore[call-arg]
    except TypeError:
        return client.models.list()


def _openai_chat_create(**kwargs: Any) -> Any:
    try:
        return client.chat.completions.create(timeout=LLM_TIMEOUT_SEC, **kwargs)  # type: ignore[call-arg]
    except TypeError:
        return client.chat.completions.create(**kwargs)


def call_with_retries(fn, *, name: str) -> Any:
    if CB.is_open():
        raise RuntimeError("LLM circuit breaker is open (LM Studio temporarily unavailable).")

    last_exc: Optional[Exception] = None
    for attempt in range(LLM_MAX_RETRIES + 1):
        try:
            res = fn()
            CB.on_success()
            return res
        except Exception as e:
            last_exc = e
            CB.on_failure()
            record_error(f"{name} failed: {type(e).__name__}: {e}")
            if attempt >= LLM_MAX_RETRIES:
                break
            sleep_s = LLM_RETRY_BACKOFF_SEC * (2 ** attempt)
            time.sleep(sleep_s)

    assert last_exc is not None
    raise last_exc


# =============================================================================
# SETTINGS / MEMORY
# =============================================================================

DEFAULT_CFG: Dict[str, Any] = {"role": "default", "temperature": 0.7, "memory": []}


def get_settings(user_id: Union[int, str]) -> Dict[str, Any]:
    k = uid(user_id)
    changed = False
    with STATE_LOCK:
        if k not in user_settings or not isinstance(user_settings.get(k), dict):
            user_settings[k] = copy.deepcopy(DEFAULT_CFG)
            changed = True

        for kk, vv in DEFAULT_CFG.items():
            if kk not in user_settings[k]:
                user_settings[k][kk] = copy.deepcopy(vv)
                changed = True

        # миграция: вырезали выбор модели
        if "model" in user_settings[k]:
            user_settings[k].pop("model", None)
            changed = True
        if "model_key" in user_settings[k]:
            user_settings[k].pop("model_key", None)
            changed = True

        if not isinstance(user_settings[k].get("memory"), list):
            user_settings[k]["memory"] = []
            changed = True

        if changed:
            settings_store.save()

        return user_settings[k]


def memory_text_for(user_id: Union[int, str]) -> str:
    s = get_settings(user_id)
    mem = s.get("memory", [])
    if not mem:
        return ""

    safe_items: List[str] = []
    for item in mem:
        if isinstance(item, str) and item.strip():
            safe_items.append(item.strip())

    if not safe_items:
        return ""

    lines = "\n".join(f"- {x}" for x in safe_items[:MAX_MEMORY_ITEMS])
    return "Память о пользователе (учитывай это, если уместно):\n" + lines + "\n"


def system_prompt_for(user_id: Union[int, str]) -> str:
    s = get_settings(user_id)
    role_text = ROLES.get(s.get("role", "default"), ROLES["default"])
    mem = memory_text_for(user_id)
    return (
        f"{role_text}\n\n"
        f"{EXECUTION_GUIDE}\n"
        f"{mem}\n"
        f"{RESPONSE_FORMAT_INSTRUCTION}"
    ).strip()


def init_history(user_id: Union[int, str]) -> None:
    k = uid(user_id)
    with STATE_LOCK:
        chat_histories[k] = [{"role": "system", "content": system_prompt_for(k)}]
        history_store.save()


def refresh_system_prompt_in_history(user_id: str) -> None:
    with STATE_LOCK:
        history = chat_histories.get(user_id)
        if not history:
            init_history(user_id)
            return
        if history[0].get("role") != "system":
            history.insert(0, {"role": "system", "content": system_prompt_for(user_id)})
        else:
            history[0]["content"] = system_prompt_for(user_id)
        history_store.save()


def build_photo_caption(user_caption: Optional[str]) -> str:
    cap = (user_caption or "").strip()
    if cap:
        return cap
    return AUTO_IMAGE_DESCRIPTION_5S


# =============================================================================
# TOKEN STATUS
# =============================================================================

def get_token_status(user_id: Union[int, str]) -> TokenStatus:
    k = uid(user_id)
    with STATE_LOCK:
        history = chat_histories.get(k) or []
        used = token_estimator.estimate_messages(history)
    return TokenStatus(used=used, left=TOKEN_LIMIT - used)


# =============================================================================
# LM STUDIO ACTIVE MODEL RESOLVE
# =============================================================================

_MODEL_ID_CACHE: Dict[str, Any] = {"value": "local-model", "ts": 0.0}


def resolve_lmstudio_model_id() -> str:
    now = time.time()
    if MODEL_ID_TTL_SEC > 0 and (now - float(_MODEL_ID_CACHE["ts"])) < MODEL_ID_TTL_SEC:
        v = _MODEL_ID_CACHE["value"]
        return v if isinstance(v, str) and v else "local-model"

    def _list():
        return _openai_models_list()

    model_id = "local-model"
    try:
        models = call_with_retries(_list, name="models.list")
        data = getattr(models, "data", None)
        if isinstance(data, list) and data:
            mid = getattr(data[0], "id", None)
            if isinstance(mid, str) and mid.strip():
                model_id = mid.strip()
    except Exception:
        # leave fallback
        pass

    _MODEL_ID_CACHE["value"] = model_id
    _MODEL_ID_CACHE["ts"] = now
    return model_id


# =============================================================================
# STORAGE (NO BASE64 IN JSON)
# =============================================================================

def store_user_text(user_id: str, text: str, job_id: int) -> None:
    with STATE_LOCK:
        chat_histories[user_id].append({"role": "user", "content": [{"type": "text", "text": text}], "_job_id": job_id})
        history_store.save()


def store_user_photo(user_id: str, file_id: str, caption: str, job_id: int) -> None:
    with STATE_LOCK:
        chat_histories[user_id].append(
            {"role": "user", "content": [{"type": "telegram_photo", "file_id": file_id, "caption": caption}], "_job_id": job_id}
        )
        history_store.save()


def insert_assistant_after_job(user_id: str, job_id: int, text: str) -> bool:
    with STATE_LOCK:
        history = chat_histories.get(user_id)
        if not history:
            return False

        idx = None
        for i, m in enumerate(history):
            if m.get("role") == "user" and m.get("_job_id") == job_id:
                idx = i
                break
        if idx is None:
            return False

        history[idx].pop("_job_id", None)
        history.insert(idx + 1, {"role": "assistant", "content": text})
        history_store.save()
        return True


def remove_user_message_by_job(user_id: str, job_id: int) -> bool:
    with STATE_LOCK:
        history = chat_histories.get(user_id)
        if not history:
            return False
        for i, m in enumerate(history):
            if m.get("role") == "user" and m.get("_job_id") == job_id:
                if i + 1 < len(history) and history[i + 1].get("role") == "assistant":
                    return False
                history.pop(i)
                history_store.save()
                return True
        return False


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
                            record_error(f"materialize photo failed: {type(e).__name__}: {e}")
                            blocks.append({"type": "text", "text": "[изображение недоступно]"})
                    if caption.strip():
                        blocks.append({"type": "text", "text": caption})

            out.append({"role": role, "content": blocks if blocks else ""})
            continue

        out.append({"role": role, "content": str(content)})

    return out


# =============================================================================
# DEDUP + RATE LIMIT
# =============================================================================

_dedup_lock = threading.RLock()
_seen_msgs: Deque[Tuple[int, int, float]] = deque(maxlen=DEDUP_CACHE_SIZE)  # (chat_id, msg_id, ts)
_seen_set: set[Tuple[int, int]] = set()


def is_duplicate_message(chat_id: int, message_id: int) -> bool:
    now = time.time()
    key = (chat_id, message_id)
    with _dedup_lock:
        # cleanup TTL
        while _seen_msgs and (now - _seen_msgs[0][2]) > DEDUP_TTL_SEC:
            old_chat, old_mid, _ts = _seen_msgs.popleft()
            _seen_set.discard((old_chat, old_mid))

        if key in _seen_set:
            return True
        _seen_set.add(key)
        _seen_msgs.append((chat_id, message_id, now))
        return False


_rate_lock = threading.RLock()
_user_last_ts: Dict[str, float] = {}
_user_window: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=200))


def check_rate_limit(user_id: str) -> Optional[str]:
    now = time.time()
    with _rate_lock:
        last = _user_last_ts.get(user_id)
        if last is not None and (now - last) < USER_MIN_INTERVAL_SEC:
            return f"Слишком часто. Подожди {USER_MIN_INTERVAL_SEC:.0f} сек."

        window = _user_window[user_id]
        # cleanup 60s
        while window and (now - window[0]) > 60:
            window.popleft()
        if len(window) >= USER_MAX_PER_MINUTE:
            return f"Лимит: {USER_MAX_PER_MINUTE}/мин. Подожди немного."

        _user_last_ts[user_id] = now
        window.append(now)
        return None


# =============================================================================
# QUEUE / JOBS
# =============================================================================

@dataclass
class Job:
    job_id: int
    user_id: str
    chat_id: int
    status_message_id: int
    created_at: float
    priority: int
    has_image: bool
    cancel_event: threading.Event = field(default_factory=threading.Event)
    started: bool = False
    done: bool = False
    canceled: bool = False


jobs: Dict[int, Job] = {}
user_queues: Dict[str, Deque[int]] = {}
user_busy: Dict[str, bool] = {}
active_job_by_user: Dict[str, int] = {}
active_global: int = 0


def compute_priority(user_id: str, prompt_tokens_estimate: int, used_tokens_estimate: int, has_image: bool) -> int:
    is_owner = False
    try:
        is_owner = (int(user_id) == BOT_OWNER_ID and BOT_OWNER_ID != 0)
    except Exception:
        is_owner = False

    if is_owner:
        return 2_000_000_000

    pr = 50_000
    pr -= prompt_tokens_estimate * TOKENS_PRIORITY_WEIGHT
    pr -= used_tokens_estimate * USED_TOKENS_WEIGHT
    if has_image:
        pr -= IMAGE_PRIORITY_PENALTY
    return pr


def get_or_create_user_queue(user_id: str) -> Deque[int]:
    q = user_queues.get(user_id)
    if q is None:
        q = deque()
        user_queues[user_id] = q
    user_busy.setdefault(user_id, False)
    return q


def enqueue_job(job: Job) -> bool:
    with SCHED_LOCK:
        q = get_or_create_user_queue(job.user_id)

        # clean head garbage
        while q:
            j = jobs.get(q[0])
            if not j or j.canceled or j.done:
                q.popleft()
            else:
                break

        if len(q) >= MAX_PENDING_PER_USER:
            return False

        q.append(job.job_id)
        jobs[job.job_id] = job
        SCHED_COND.notify_all()
        return True


def select_next_job_id() -> Optional[int]:
    global active_global
    with SCHED_LOCK:
        if active_global >= MAX_ACTIVE_GLOBAL:
            return None

        best: Optional[Tuple[int, float, int]] = None  # (priority, created_at, job_id)

        for u, q in user_queues.items():
            if not q:
                continue
            if user_busy.get(u, False):
                continue

            while q:
                j = jobs.get(q[0])
                if not j or j.canceled or j.done:
                    q.popleft()
                else:
                    break
            if not q:
                continue

            jid = q[0]
            j = jobs.get(jid)
            if not j or j.canceled or j.done:
                q.popleft()
                continue

            cand = (j.priority, j.created_at, jid)
            if best is None or cand[0] > best[0] or (cand[0] == best[0] and cand[1] < best[1]):
                best = cand

        if best is None:
            return None

        jid = best[2]
        j = jobs.get(jid)
        if not j:
            return None

        q = user_queues.get(j.user_id)
        if q and q[0] == jid:
            q.popleft()
        elif q:
            try:
                q.remove(jid)
            except ValueError:
                pass

        user_busy[j.user_id] = True
        active_job_by_user[j.user_id] = jid
        active_global += 1
        return jid


def mark_job_finished(user_id: str, job_id: int) -> None:
    global active_global
    with SCHED_LOCK:
        user_busy[user_id] = False
        if active_job_by_user.get(user_id) == job_id:
            active_job_by_user.pop(user_id, None)
        if active_global > 0:
            active_global -= 1
        SCHED_COND.notify_all()


def has_pending_for_user(user_id: str) -> bool:
    with SCHED_LOCK:
        q = user_queues.get(user_id)
        if not q:
            return False
        for jid in q:
            j = jobs.get(jid)
            if j and not j.canceled and not j.done:
                return True
        return False


def cleanup_jobs(max_keep: int = 5000) -> None:
    with SCHED_LOCK:
        if len(jobs) <= max_keep:
            return
        done_ids = [jid for jid, j in jobs.items() if j.done or j.canceled]
        done_ids.sort(key=lambda jid: jobs[jid].created_at if jid in jobs else 0.0)
        to_delete = done_ids[: max(0, len(jobs) - max_keep)]
        for jid in to_delete:
            jobs.pop(jid, None)


# =============================================================================
# QUEUE STATUS
# =============================================================================

@dataclass(frozen=True)
class QueueStatus:
    user_ahead: int
    user_position: int
    user_has_active: bool
    global_pending: int
    global_active: int
    priority: int


def compute_queue_status_for_job(user_id: str, job_id: int) -> QueueStatus:
    with SCHED_LOCK:
        q = user_queues.get(user_id) or deque()
        busy = bool(user_busy.get(user_id, False))

        global_pending = sum(len(qq) for qq in user_queues.values())
        global_active_now = active_global

        job = jobs.get(job_id)
        pr = job.priority if job else 0

        idx_in_q = -1
        try:
            idx_in_q = list(q).index(job_id)
        except ValueError:
            idx_in_q = -1

        ahead_in_q = idx_in_q if idx_in_q >= 0 else 0
        user_ahead = ahead_in_q + (1 if busy else 0)
        user_position = (idx_in_q + 1) + (1 if busy else 0) if idx_in_q >= 0 else (1 if busy else 1)

        return QueueStatus(
            user_ahead=user_ahead,
            user_position=user_position,
            user_has_active=busy,
            global_pending=global_pending,
            global_active=global_active_now,
            priority=pr,
        )


# =============================================================================
# UI (KEYBOARDS)
# =============================================================================

def main_menu_keyboard(user_id: Union[int, str]) -> types.InlineKeyboardMarkup:
    st = get_token_status(user_id)
    s = get_settings(user_id)

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton(f"🧠 Tokens: {st.used}/{TOKEN_LIMIT}", callback_data="show_tokens"))
    markup.add(types.InlineKeyboardButton("🗑️ Новый чат", callback_data="new_chat"))
    markup.add(types.InlineKeyboardButton(f"🎭 Роль: {s['role']}", callback_data="menu_roles"))
    markup.add(types.InlineKeyboardButton(f"🌡️ Temp: {s['temperature']}", callback_data="menu_temp"))
    markup.add(types.InlineKeyboardButton("🧾 Память", callback_data="menu_memory"))
    markup.add(types.InlineKeyboardButton("📌 Очередь", callback_data="show_queue"))
    return markup


def roles_keyboard(user_id: Union[int, str]) -> types.InlineKeyboardMarkup:
    current = get_settings(user_id)["role"]
    markup = types.InlineKeyboardMarkup()
    for r in ROLES.keys():
        mark = " ✅" if r == current else ""
        markup.add(types.InlineKeyboardButton(f"🎭 {r}{mark}", callback_data=f"set_role_{r}"))
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


def stop_keyboard(job_id: int) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🛑 Остановить", callback_data=f"stop:{job_id}"))
    return markup


def memory_keyboard() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("🧹 Очистить память", callback_data="memory_clear"))
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="main_menu"))
    return markup


# =============================================================================
# TELEGRAM UTILS
# =============================================================================

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


# =============================================================================
# SUMMARY / COMPRESSION (kept)
# =============================================================================

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


def compression_engine_inplace(user_id: str) -> None:
    with STATE_LOCK:
        history = chat_histories.get(user_id)
        if not history:
            chat_histories[user_id] = [{"role": "system", "content": system_prompt_for(user_id)}]
            history_store.save()
            return

        # refresh system
        if history[0].get("role") == "system":
            history[0]["content"] = system_prompt_for(user_id)
        else:
            history.insert(0, {"role": "system", "content": system_prompt_for(user_id)})

        has_sum = any(_is_summary_msg(m) for m in history)
        has_ultra = any(_is_ultra_msg(m) for m in history)

        if len(history) > 12 and not has_sum and not has_ultra:
            window = history[1:9]
            summary = compress_summary(window)
            history = [history[0], {"role": "system", "content": f"[SUMMARY] {summary}"}] + history[9:]
            chat_histories[user_id] = history

        history = chat_histories[user_id]
        if len(history) > 18 and not any(_is_ultra_msg(m) for m in history):
            for i, m in enumerate(history):
                if _is_summary_msg(m):
                    compact = str(m["content"]).replace("[SUMMARY] ", "")
                    if len(compact) > 240:
                        compact = compact[:240].rstrip() + "…"
                    history[i] = {"role": "system", "content": f"[ULTRA] {compact}"}
                    break

        history_store.save()


# =============================================================================
# STRICT TOKEN BUDGET (kept)
# =============================================================================

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


def enforce_token_budget_strict_list(user_id: str, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not history:
        return [{"role": "system", "content": system_prompt_for(user_id)}]

    history = copy.deepcopy(history)
    if history[0].get("role") == "system":
        history[0]["content"] = system_prompt_for(user_id)
    else:
        history.insert(0, {"role": "system", "content": system_prompt_for(user_id)})

    for m in history:
        m.pop("_job_id", None)

    sys0 = history[0]
    summary = _extract_summary_msg(history)
    tail = _non_system_msgs(history)

    candidate = _rebuild_history(sys0, summary, tail)
    while len(tail) > 1 and token_estimator.estimate_messages(candidate) > TOKEN_LIMIT:
        tail = tail[1:]
        candidate = _rebuild_history(sys0, summary, tail)

    if token_estimator.estimate_messages(candidate) > TOKEN_LIMIT and tail:
        last = copy.deepcopy(tail[-1])
        content = last.get("content")

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
            last_copy = copy.deepcopy(last)
            if isinstance(content, str):
                last_copy["content"] = ""
            elif isinstance(content, list):
                kept = []
                for b in content:
                    if isinstance(b, dict) and b.get("type") in ("image_url", "telegram_photo"):
                        nb = dict(b)
                        if nb.get("type") == "telegram_photo":
                            nb.pop("caption", None)
                        kept.append(nb)
                last_copy["content"] = kept
            else:
                last_copy["content"] = ""

            base_candidate = _rebuild_history(sys0, summary, tail[:-1] + [last_copy])
            base_tokens = token_estimator.estimate_messages(base_candidate)
            allowance = max(0, TOKEN_LIMIT - base_tokens)
            allowance = max(allowance, MIN_TEXT_TOKENS_TO_KEEP)
            truncated = token_estimator.truncate_text_to_tokens_keep_tail(joined_text, allowance)

            if isinstance(content, str):
                last["content"] = truncated
            elif isinstance(content, list):
                new_blocks: List[Dict[str, Any]] = []
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "telegram_photo":
                        nb = dict(b)
                        nb.pop("caption", None)
                        new_blocks.append(nb)
                new_blocks.append({"type": "text", "text": truncated})
                last["content"] = new_blocks
            else:
                last["content"] = truncated

            tail[-1] = last
            candidate = _rebuild_history(sys0, summary, tail)

    return candidate


# =============================================================================
# SNAPSHOT FOR JOB
# =============================================================================

def find_job_user_message_index(history: List[Dict[str, Any]], job_id: int) -> Optional[int]:
    for i, m in enumerate(history):
        if m.get("role") == "user" and m.get("_job_id") == job_id:
            return i
    return None


def snapshot_history_for_job(user_id: str, job_id: int) -> List[Dict[str, Any]]:
    with STATE_LOCK:
        history = chat_histories.get(user_id) or [{"role": "system", "content": system_prompt_for(user_id)}]
        idx = find_job_user_message_index(history, job_id)
        snap = copy.deepcopy(history[: idx + 1]) if idx is not None else copy.deepcopy(history)

    if snap and snap[0].get("role") == "system":
        snap[0]["content"] = system_prompt_for(user_id)
    else:
        snap.insert(0, {"role": "system", "content": system_prompt_for(user_id)})

    for m in snap:
        m.pop("_job_id", None)

    return enforce_token_budget_strict_list(user_id, snap)


def message_has_image(message: types.Message) -> bool:
    return bool(message.content_type == "photo" and message.photo)


# =============================================================================
# COMPLETION (STREAMING + STOP SUPPORT + retries)
# =============================================================================

def run_completion_streaming(
    api_messages: List[Dict[str, Any]],
    temperature: float,
    cancel_event: threading.Event,
) -> str:
    model_id = resolve_lmstudio_model_id()
    chunks: List[str] = []

    def _stream_call():
        return _openai_chat_create(
            model=model_id,
            messages=api_messages,
            temperature=temperature,
            stream=True,
        )

    def _nonstream_call():
        return _openai_chat_create(
            model=model_id,
            messages=api_messages,
            temperature=temperature,
        )

    # Prefer streaming, fallback to non-stream.
    try:
        stream = call_with_retries(_stream_call, name="chat.create(stream)")
        for ev in stream:
            if cancel_event.is_set():
                break
            delta = None
            try:
                delta = ev.choices[0].delta.content  # type: ignore[attr-defined]
            except Exception:
                delta = None
            if isinstance(delta, str) and delta:
                chunks.append(delta)
        return "".join(chunks)
    except Exception as e:
        record_error(f"stream failed -> fallback non-stream: {type(e).__name__}: {e}")

    completion = call_with_retries(_nonstream_call, name="chat.create")
    return completion.choices[0].message.content or ""


# =============================================================================
# POSTPROCESS (ONLY WHEN IDLE)
# =============================================================================

def postprocess_user_history_if_idle(user_id: str) -> None:
    with SCHED_LOCK:
        busy = user_busy.get(user_id, False)
        pending = has_pending_for_user(user_id)
    if busy or pending:
        return

    refresh_system_prompt_in_history(user_id)
    compression_engine_inplace(user_id)

    with STATE_LOCK:
        current = chat_histories.get(user_id) or [{"role": "system", "content": system_prompt_for(user_id)}]
        trimmed = enforce_token_budget_strict_list(user_id, current)
        chat_histories[user_id] = trimmed
        history_store.save()


# =============================================================================
# WORKER THREADS
# =============================================================================

def worker_loop(worker_id: int) -> None:
    logger.info("Worker #%d started", worker_id)

    while not SHUTDOWN_EVENT.is_set():
        with SCHED_LOCK:
            jid = select_next_job_id()
            if jid is None:
                SCHED_COND.wait(timeout=1.0)
                continue

        job = jobs.get(jid)
        if not job:
            continue

        job.started = True

        with STATE_LOCK:
            s = get_settings(job.user_id)
            temperature = float(s["temperature"])

        safe_edit_text(
            job.chat_id,
            job.status_message_id,
            "⏳ Генерирую ответ… (можно остановить кнопкой ниже)",
            reply_markup=stop_keyboard(job.job_id),
        )

        try:
            snap = snapshot_history_for_job(job.user_id, job.job_id)
            api_messages = materialize_for_api(snap)

            raw = run_completion_streaming(
                api_messages=api_messages,
                temperature=temperature,
                cancel_event=job.cancel_event,
            )

            canceled = job.cancel_event.is_set() or SHUTDOWN_EVENT.is_set()

            if canceled and SMART_STOP_DISCARD_PARTIAL:
                response = "Остановлено."
            else:
                response = raw or ""
                if "ОТВЕТ:" in response:
                    response = response.split("ОТВЕТ:", 1)[1].strip()
                if canceled and not response.strip():
                    response = "Остановлено."

            inserted = insert_assistant_after_job(job.user_id, job.job_id, response)
            if not inserted:
                job.canceled = True

            safe_delete(job.chat_id, job.status_message_id)
            send_long_message(job.chat_id, response, reply_markup=main_menu_keyboard(job.user_id))

        except Exception as e:
            record_error(f"worker error: {type(e).__name__}: {e}")
            safe_delete(job.chat_id, job.status_message_id)
            bot.send_message(job.chat_id, f"Ошибка: {e}", reply_markup=main_menu_keyboard(job.user_id))
        finally:
            job.done = True
            mark_job_finished(job.user_id, job.job_id)
            postprocess_user_history_if_idle(job.user_id)
            cleanup_jobs()


_workers: List[threading.Thread] = []
for i in range(max(1, WORKER_COUNT)):
    t = threading.Thread(target=worker_loop, args=(i + 1,), daemon=True)
    _workers.append(t)
    t.start()


# =============================================================================
# COMMANDS
# =============================================================================

@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message) -> None:
    user_id = uid(message.from_user.id)
    with STATE_LOCK:
        if user_id not in chat_histories:
            init_history(user_id)
    refresh_system_prompt_in_history(user_id)
    bot.reply_to(message, "⚙️ Панель управления:", reply_markup=main_menu_keyboard(user_id))


@bot.message_handler(commands=["export"])
def cmd_export(message: types.Message) -> None:
    user_id = uid(message.from_user.id)
    with STATE_LOCK:
        if user_id not in chat_histories:
            init_history(user_id)

        payload = {
            "user_id": user_id,
            "settings": get_settings(user_id),
            "token_status_estimate": {
                "used": get_token_status(user_id).used,
                "left": get_token_status(user_id).left,
                "limit": TOKEN_LIMIT,
            },
            "lmstudio_loaded_model_id_estimate": resolve_lmstudio_model_id(),
            "history": chat_histories.get(user_id, []),
        }

    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    bio = io.BytesIO(data)
    bio.name = f"export_{user_id}.json"
    bot.send_document(message.chat.id, bio, caption="📦 Экспорт истории и настроек (JSON).")


@bot.message_handler(commands=["profile"])
def cmd_profile(message: types.Message) -> None:
    user_id = uid(message.from_user.id)
    s = get_settings(user_id)
    mem = s.get("memory", [])
    lines = [
        f"👤 User: {user_id}",
        f"🎭 Role: {s.get('role')}",
        f"🌡️ Temp: {s.get('temperature')}",
        f"🤖 LM Studio model: {resolve_lmstudio_model_id()}",
        "",
        "🧾 Память:",
    ]
    if mem:
        for i, item in enumerate(mem[:MAX_MEMORY_ITEMS], 1):
            lines.append(f"{i}) {item}")
    else:
        lines.append("(пусто)")
    lines.append("")
    lines.append("Команды: /remember <текст>, /forget <n|all>, /queue, /stop, /status")
    bot.send_message(message.chat.id, "\n".join(lines), reply_markup=main_menu_keyboard(user_id))


@bot.message_handler(commands=["status"])
def cmd_status(message: types.Message) -> None:
    # owner-only
    if int(message.from_user.id) != BOT_OWNER_ID:
        return

    uptime = time.time() - START_TS
    cb = CB.status()

    with SCHED_LOCK:
        global_pending = sum(len(qq) for qq in user_queues.values())
        global_active_now = active_global
        users_in_queue = sum(1 for qq in user_queues.values() if len(qq) > 0)
        active_users = sum(1 for v in user_busy.values() if v)

    last_errs = list(RECENT_ERRORS)[-8:]
    err_text = "\n".join(
        f"- {time.strftime('%H:%M:%S', time.localtime(ts))}: {msg}" for ts, msg in last_errs
    ) or "(нет)"

    text = (
        "🛠 /status\n"
        f"⏱ Uptime: {uptime:.0f}s\n"
        f"🤖 LM Studio model: {resolve_lmstudio_model_id()}\n"
        f"⚙️ Active(global): {global_active_now}/{MAX_ACTIVE_GLOBAL} | workers={WORKER_COUNT}\n"
        f"📥 Pending(global): {global_pending} | users_in_queue={users_in_queue}\n"
        f"👥 Active users: {active_users}\n"
        "\n"
        f"🧯 Circuit breaker: open={cb['open']} fail_streak={cb['fail_streak']} "
        f"threshold={cb['failure_threshold']} reset={cb['reset_timeout_sec']}s\n"
        "\n"
        "❗ Последние ошибки:\n"
        f"{err_text}"
    )
    bot.reply_to(message, text, reply_markup=main_menu_keyboard(uid(message.from_user.id)))


@bot.message_handler(commands=["remember"])
def cmd_remember(message: types.Message) -> None:
    user_id = uid(message.from_user.id)
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        bot.reply_to(message, "Использование: /remember <что запомнить>", reply_markup=main_menu_keyboard(user_id))
        return

    item = parts[1].strip()
    if len(item) > MAX_MEMORY_ITEM_LEN:
        item = item[:MAX_MEMORY_ITEM_LEN].rstrip() + "…"

    with STATE_LOCK:
        s = get_settings(user_id)
        mem = s.setdefault("memory", [])
        if not isinstance(mem, list):
            mem = []
            s["memory"] = mem
        mem = [m for m in mem if isinstance(m, str) and m.strip() and m.strip() != item]
        mem.insert(0, item)
        s["memory"] = mem[:MAX_MEMORY_ITEMS]
        settings_store.save()

    refresh_system_prompt_in_history(user_id)
    bot.reply_to(message, "✅ Запомнил.", reply_markup=main_menu_keyboard(user_id))


@bot.message_handler(commands=["forget"])
def cmd_forget(message: types.Message) -> None:
    user_id = uid(message.from_user.id)
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)

    with STATE_LOCK:
        s = get_settings(user_id)
        mem = s.get("memory", [])
        if not isinstance(mem, list):
            mem = []
            s["memory"] = mem

        if len(parts) < 2:
            if not mem:
                bot.reply_to(message, "Память пустая.", reply_markup=main_menu_keyboard(user_id))
                return
            lines = ["🧾 Память:"]
            for i, item in enumerate(mem[:MAX_MEMORY_ITEMS], 1):
                lines.append(f"{i}) {item}")
            lines.append("Удаление: /forget <номер> или /forget all")
            bot.reply_to(message, "\n".join(lines), reply_markup=main_menu_keyboard(user_id))
            return

        arg = parts[1].strip().lower()
        if arg == "all":
            s["memory"] = []
            settings_store.save()
        else:
            try:
                n = int(arg)
                if n < 1 or n > len(mem):
                    bot.reply_to(message, "Неверный номер.", reply_markup=main_menu_keyboard(user_id))
                    return
                mem.pop(n - 1)
                s["memory"] = mem
                settings_store.save()
            except ValueError:
                bot.reply_to(message, "Использование: /forget <номер> или /forget all", reply_markup=main_menu_keyboard(user_id))
                return

    refresh_system_prompt_in_history(user_id)
    bot.reply_to(message, "✅ Готово.", reply_markup=main_menu_keyboard(user_id))


@bot.message_handler(commands=["stop"])
def cmd_stop(message: types.Message) -> None:
    user_id = uid(message.from_user.id)
    with SCHED_LOCK:
        jid = active_job_by_user.get(user_id)
    if not jid:
        bot.reply_to(message, "Сейчас нет активной генерации.", reply_markup=main_menu_keyboard(user_id))
        return

    j = jobs.get(jid)
    if j and not j.done and not j.canceled:
        j.cancel_event.set()
    bot.reply_to(message, "🛑 Останавливаю…", reply_markup=main_menu_keyboard(user_id))


@bot.message_handler(commands=["queue"])
def cmd_queue(message: types.Message) -> None:
    user_id = uid(message.from_user.id)
    with SCHED_LOCK:
        q = user_queues.get(user_id) or deque()
        busy = bool(user_busy.get(user_id, False))
        active_id = active_job_by_user.get(user_id)
        global_pending = sum(len(qq) for qq in user_queues.values())
        global_active_now = active_global

    lines = [
        "📌 Очередь",
        f"⚙️ Active(global): {global_active_now}/{MAX_ACTIVE_GLOBAL}",
        f"📥 Pending(global): {global_pending}",
        "",
        f"👤 У тебя активная: {'да' if busy else 'нет'}" + (f" (job {active_id})" if active_id else ""),
        f"📬 Pending у тебя: {len(q)}",
    ]
    if q:
        lines.append("Твои pending job_id: " + ", ".join(str(x) for x in list(q)[:10]) + ("…" if len(q) > 10 else ""))
    bot.send_message(message.chat.id, "\n".join(lines), reply_markup=main_menu_keyboard(user_id))


# =============================================================================
# CALLBACKS
# =============================================================================

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call: types.CallbackQuery) -> None:
    user_id = uid(call.from_user.id)
    s = get_settings(user_id)

    if call.data == "main_menu":
        safe_edit_text(call.message.chat.id, call.message.message_id, "⚙️ Меню:", reply_markup=main_menu_keyboard(user_id))

    elif call.data == "new_chat":
        with SCHED_LOCK:
            busy = user_busy.get(user_id, False)
            pending = has_pending_for_user(user_id)
        if busy or pending:
            bot.send_message(call.message.chat.id, "Сначала дождись/останови текущие задачи.", reply_markup=main_menu_keyboard(user_id))
            return
        init_history(user_id)
        bot.send_message(call.message.chat.id, "🧹 История очищена.", reply_markup=main_menu_keyboard(user_id))

    elif call.data == "menu_roles":
        safe_edit_text(call.message.chat.id, call.message.message_id, "🎭 Выберите роль:", reply_markup=roles_keyboard(user_id))

    elif call.data.startswith("set_role_"):
        role = call.data.replace("set_role_", "", 1)
        if role not in ROLES:
            bot.answer_callback_query(call.id, "Неизвестная роль.")
            return
        with STATE_LOCK:
            s["role"] = role
            settings_store.save()
        refresh_system_prompt_in_history(user_id)
        safe_edit_text(call.message.chat.id, call.message.message_id, "✅ Роль применена.", reply_markup=main_menu_keyboard(user_id))

    elif call.data == "menu_temp":
        safe_edit_text(call.message.chat.id, call.message.message_id, "🌡️ Температура:", reply_markup=temp_keyboard(user_id))

    elif call.data.startswith("set_temp_"):
        with STATE_LOCK:
            try:
                s["temperature"] = float(call.data.replace("set_temp_", "", 1))
            except ValueError:
                s["temperature"] = DEFAULT_CFG["temperature"]
            settings_store.save()
        safe_edit_text(call.message.chat.id, call.message.message_id, "✅ Температура обновлена!", reply_markup=main_menu_keyboard(user_id))

    elif call.data == "show_tokens":
        st = get_token_status(user_id)
        cb = CB.status()
        bot.send_message(
            call.message.chat.id,
            f"🧠 Tokens (оценка): {st.used}/{TOKEN_LIMIT}\n"
            f"📉 Осталось (оценка): {st.left}\n"
            f"🤖 LM Studio model: {resolve_lmstudio_model_id()}\n"
            f"🧯 CB open={cb['open']} fail_streak={cb['fail_streak']}\n"
            f"📦 Экспорт: /export\n"
            f"📌 Очередь: /queue",
            reply_markup=main_menu_keyboard(user_id),
        )

    elif call.data == "show_queue":
        cmd_queue(call.message)

    elif call.data == "menu_memory":
        mem = get_settings(user_id).get("memory", [])
        lines = ["🧾 Память:"]
        if mem:
            for i, item in enumerate(mem[:MAX_MEMORY_ITEMS], 1):
                lines.append(f"{i}) {item}")
        else:
            lines.append("(пусто)")
        lines.append("")
        lines.append("Добавить: /remember <текст>")
        lines.append("Удалить: /forget <номер> или /forget all")
        safe_edit_text(call.message.chat.id, call.message.message_id, "\n".join(lines), reply_markup=memory_keyboard())

    elif call.data == "memory_clear":
        with STATE_LOCK:
            s = get_settings(user_id)
            s["memory"] = []
            settings_store.save()
        refresh_system_prompt_in_history(user_id)
        safe_edit_text(call.message.chat.id, call.message.message_id, "🧹 Память очищена.", reply_markup=main_menu_keyboard(user_id))

    elif call.data.startswith("stop:"):
        try:
            job_id = int(call.data.split(":", 1)[1])
        except Exception:
            bot.answer_callback_query(call.id, "Неверный job id.")
            return

        job = jobs.get(job_id)
        if not job:
            bot.answer_callback_query(call.id, "Задача не найдена/устарела.")
            return
        if job.user_id != user_id:
            bot.answer_callback_query(call.id, "Нельзя остановить чужую задачу.")
            return
        if job.done or job.canceled:
            bot.answer_callback_query(call.id, "Уже завершено.")
            return

        job.cancel_event.set()

        was_removed = False
        with SCHED_LOCK:
            if not job.started:
                q = user_queues.get(user_id)
                if q:
                    try:
                        q.remove(job_id)
                        job.canceled = True
                        was_removed = True
                    except ValueError:
                        pass
                SCHED_COND.notify_all()

        if was_removed:
            remove_user_message_by_job(user_id, job_id)
            safe_edit_text(job.chat_id, job.status_message_id, "🛑 Отменено (удалено из очереди).", reply_markup=None)
        else:
            safe_edit_text(job.chat_id, job.status_message_id, "🛑 Останавливаю…", reply_markup=None)

        bot.answer_callback_query(call.id, "Ок.")


# =============================================================================
# MAIN MESSAGE HANDLER (ENQUEUE + reliability checks)
# =============================================================================

@bot.message_handler(content_types=["text", "photo"])
def handle_message(message: types.Message) -> None:
    global ACCEPTING_JOBS

    # дедуп от повторных апдейтов
    if is_duplicate_message(message.chat.id, message.message_id):
        return

    user_id = uid(message.from_user.id)

    # не принимаем новые задачи при shutdown
    if SHUTDOWN_EVENT.is_set() or not ACCEPTING_JOBS:
        bot.send_message(message.chat.id, "Бот сейчас перезапускается/останавливается. Попробуй позже.")
        return

    # rate limit
    rl = check_rate_limit(user_id)
    if rl:
        bot.send_message(message.chat.id, rl, reply_markup=main_menu_keyboard(user_id))
        return

    # circuit breaker (LM Studio недоступна)
    if CB.is_open():
        bot.send_message(
            message.chat.id,
            "LLM временно недоступна (перегруз/ошибка). Попробуй чуть позже.",
            reply_markup=main_menu_keyboard(user_id),
        )
        return

    with STATE_LOCK:
        if user_id not in chat_histories:
            init_history(user_id)
    refresh_system_prompt_in_history(user_id)

    job_id = next_job_id()
    status_msg = bot.reply_to(message, "⏳ Добавляю в очередь…", reply_markup=stop_keyboard(job_id))

    has_img = message_has_image(message)
    try:
        if has_img and message.photo:
            file_id = message.photo[-1].file_id
            caption = build_photo_caption(message.caption)
            store_user_photo(user_id, file_id=file_id, caption=caption, job_id=job_id)
        else:
            text = (message.text or "").strip()
            if not text:
                safe_delete(message.chat.id, status_msg.message_id)
                bot.send_message(message.chat.id, "Пустое сообщение.", reply_markup=main_menu_keyboard(user_id))
                return
            store_user_text(user_id, text=text, job_id=job_id)
    except Exception as e:
        record_error(f"history write error: {type(e).__name__}: {e}")
        safe_delete(message.chat.id, status_msg.message_id)
        bot.send_message(message.chat.id, f"Ошибка записи истории: {e}", reply_markup=main_menu_keyboard(user_id))
        return

    # Оценка для приоритета
    try:
        snap = snapshot_history_for_job(user_id, job_id)
        prompt_cost = token_estimator.estimate_messages(snap)
        used_cost = get_token_status(user_id).used
    except Exception as e:
        record_error(f"priority estimate failed: {type(e).__name__}: {e}")
        prompt_cost = TOKEN_LIMIT // 2
        used_cost = TOKEN_LIMIT // 2

    pr = compute_priority(
        user_id=user_id,
        prompt_tokens_estimate=prompt_cost,
        used_tokens_estimate=used_cost,
        has_image=has_img,
    )

    job = Job(
        job_id=job_id,
        user_id=user_id,
        chat_id=message.chat.id,
        status_message_id=status_msg.message_id,
        created_at=time.time(),
        priority=pr,
        has_image=has_img,
    )

    ok = enqueue_job(job)
    if not ok:
        remove_user_message_by_job(user_id, job_id)
        safe_delete(message.chat.id, status_msg.message_id)
        bot.send_message(
            message.chat.id,
            f"Очередь переполнена (лимит {MAX_PENDING_PER_USER}). Подожди или /stop текущую генерацию.",
            reply_markup=main_menu_keyboard(user_id),
        )
        return

    qs = compute_queue_status_for_job(user_id, job_id)
    lines = [
        "📌 Запрос поставлен в очередь.",
        f"🧷 Job ID: {job_id}",
        f"⭐ Приоритет: {qs.priority}",
        f"👤 У тебя впереди задач: {qs.user_ahead} (активная: {'да' if qs.user_has_active else 'нет'})",
        f"🔢 Твоя позиция у тебя: {qs.user_position}",
        f"⚙️ Active(global): {qs.global_active}/{MAX_ACTIVE_GLOBAL}",
        f"📥 Pending(global): {qs.global_pending}",
        f"🤖 LM Studio model: {resolve_lmstudio_model_id()}",
        "Можно отменить кнопкой ниже.",
    ]

    safe_edit_text(message.chat.id, status_msg.message_id, "\n".join(lines), reply_markup=stop_keyboard(job_id))


# =============================================================================
# GRACEFUL SHUTDOWN
# =============================================================================

def graceful_shutdown(reason: str) -> None:
    global ACCEPTING_JOBS
    if SHUTDOWN_EVENT.is_set():
        return
    logger.info("Shutting down: %s", reason)
    ACCEPTING_JOBS = False
    SHUTDOWN_EVENT.set()

    # best-effort: cancel all pending/active jobs (don’t block forever)
    try:
        with SCHED_LOCK:
            for jid, j in list(jobs.items()):
                if j.done or j.canceled:
                    continue
                j.cancel_event.set()
    except Exception:
        pass

    try:
        bot.stop_polling()
    except Exception:
        pass

    # flush JSON
    try:
        settings_store.save()
    except Exception:
        pass
    try:
        history_store.save()
    except Exception:
        pass


def _sig_handler(signum: int, _frame: Any) -> None:
    graceful_shutdown(f"signal {signum}")


atexit.register(lambda: graceful_shutdown("atexit"))

try:
    signal.signal(signal.SIGINT, _sig_handler)
except Exception:
    pass
try:
    signal.signal(signal.SIGTERM, _sig_handler)
except Exception:
    pass


# =============================================================================
# BOOT
# =============================================================================

if __name__ == "__main__":
    logger.info(
        "BOT READY ✔ owner=%s base_url=%s workers=%d max_active_global=%d skip_pending=%s",
        BOT_OWNER_ID,
        BASE_URL,
        WORKER_COUNT,
        MAX_ACTIVE_GLOBAL,
        SKIP_PENDING_UPDATES,
    )

    # Polling with safer defaults
    try:
        bot.polling(
            non_stop=True,
            skip_pending=SKIP_PENDING_UPDATES,
            timeout=POLLING_TIMEOUT,
            long_polling_timeout=LONG_POLLING_TIMEOUT,
        )
    except TypeError:
        # older telebot without long_polling_timeout
        bot.polling(
            non_stop=True,
            skip_pending=SKIP_PENDING_UPDATES,
            timeout=POLLING_TIMEOUT,
        )