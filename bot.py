import asyncio
import json
import os
from difflib import SequenceMatcher

from aiohttp import web
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from openai import AsyncOpenAI

from books import BOOKS

load_dotenv()

bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()

# Додай OPENAI_API_KEY у Render → Environment
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
AI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

user_answers = {}
user_shown_books = {}
user_saved_books = {}
last_sent_books = {}

OPTIONS = {
    "genre": {
        "romance": "Роман",
        "detective": "Детектив",
        "fantasy": "Фентезі",
        "popular_science": "Науково-популярна література",
        "children": "Дитяча література",
        "classic": "Класика",
    },
    "mood": {
        "easy": "Легка",
        "romantic": "Романтична",
        "funny": "Весела",
        "deep": "Глибока",
        "dark": "Темна",
        "any": "Не важливо",
    },
    "level": {
        "easy": "Легка",
        "medium": "Середня",
        "hard": "Складна",
        "any": "Не важливо",
    },
    "avoid": {
        "violence": "Насильство",
        "sad": "Сумний фінал",
        "hard_language": "Складна мова",
        "easy_language": "Легка мова",
        "romance": "Романтика",
        "none": "Нічого, все ок",
    }
}


def make_keyboard(step):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=f"{step}:{code}")]
            for code, text in OPTIONS[step].items()
        ]
    )

def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📚 Обрати жанр")],
            [KeyboardButton(text="📌 Мої збережені книги")],
        ],
        resize_keyboard=True
    )
    
def after_result_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Підібрати ще 3", callback_data="more:yes")],
            [InlineKeyboardButton(text="📌 Мої збережені", callback_data="saved:show")],
            [InlineKeyboardButton(text="Інший жанр", callback_data="restart:yes")]
        ]
    )


def book_action_keyboard(book_index, book=None):
    buttons = [
        [InlineKeyboardButton(text="❤️ Зберегти", callback_data=f"save:{book_index}")],
        [InlineKeyboardButton(text="🔎 Схожі книги", callback_data=f"similar:{book_index}")],
        [InlineKeyboardButton(text="🗑 Видалити зі збережених", callback_data=f"delete_saved:{book_index}")]
    ]

    buy_link = (book or {}).get("buy_link")
    if buy_link:
        buttons.insert(0, [InlineKeyboardButton(text="🛒 Дивитися на Yakaboo", url=buy_link)])

    ksd_link = (book or {}).get("ksd_link")
    if ksd_link:
        buttons.insert(1, [InlineKeyboardButton(text="🛒 Дивитися на КСД", url=ksd_link)])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def find_books(user_id):
    answers = user_answers.get(user_id, {})
    genre = answers.get("genre")
    mood = answers.get("mood", "any")
    level = answers.get("level", "any")
    avoid = answers.get("avoid", "none")
    shown = user_shown_books.get(user_id, [])

    books = [b for b in BOOKS if b.get("genre") == genre]

    if mood != "any":
        filtered = [b for b in books if b.get("mood") == mood]
        if len(filtered) >= 3:
            books = filtered

    if level != "any":
        filtered = [b for b in books if b.get("level") == level]
        if len(filtered) >= 3:
            books = filtered

    if avoid == "romance":
        books = [b for b in books if b.get("genre") != "romance"]

    books = [b for b in books if b.get("title") not in shown]
    return books[:3]


def book_to_text(book, ai_reason=None):
    text = (
        f"📚 {book.get('title')}\n"
        f"👤 Автор: {book.get('author')}\n"
        f"🏷 Жанр: {book.get('genre')}\n\n"
    )

    if ai_reason:
        text += f"📝 Опис:\n{ai_reason}"
    else:
        text += "📝 Опис:\nОпис поки не згенеровано."

    return text


async def send_books(callback_or_message, books, reasons=None):
    user_id = callback_or_message.from_user.id
    last_sent_books[user_id] = books
    reasons = reasons or {}

    message = callback_or_message.message if hasattr(callback_or_message, "message") else callback_or_message

    for index, book in enumerate(books):
        text = book_to_text(book, reasons.get(book.get("title")))
        image = book.get("image")

        if image:
            await message.answer_photo(
                photo=image,
                caption=text[:1024],
                reply_markup=book_action_keyboard(index, book)
            )
            if len(text) > 1024:
                await message.answer(text[1024:])
        else:
            await message.answer(
                text,
                reply_markup=book_action_keyboard(index, book)
            )

    await message.answer(
        "Можеш написати запит своїми словами, наприклад: «хочу легкий детектив без жорстокості», або обрати дію нижче.",
        reply_markup=after_result_keyboard()
    )


def local_score(query, book):
    q = query.lower()
    fields = " ".join([
        str(book.get("title", "")),
        str(book.get("author", "")),
        str(book.get("genre", "")),
        str(book.get("mood", "")),
        str(book.get("level", "")),
        str(book.get("description", "")),
    ]).lower()

    score = 0
    for word in q.split():
        if len(word) > 2 and word in fields:
            score += 3

    score += SequenceMatcher(None, q, fields[:500]).ratio()
    return score


def preselect_books(query, limit=30):
    scored = sorted(
        BOOKS,
        key=lambda b: local_score(query, b),
        reverse=True
    )
    return scored[:limit]


async def ai_recommend_books(query, user_id):
    candidates = preselect_books(query, limit=30)

    catalog = []
    for i, book in enumerate(candidates):
        catalog.append({
            "id": i,
            "title": book.get("title"),
            "author": book.get("author"),
            "genre": book.get("genre"),
            "mood": book.get("mood"),
            "level": book.get("level"),
            "description": book.get("description"),
        })

    prompt = f"""
Ти розумний книжковий консультант.

Завдання:
- Обери рівно 3 книги зі списку CATALOG.
- Для КОЖНОЇ книги напиши унікальний опис (2-3 речення).
- Опис має бути:
  • природний
  • цікавий
  • персоналізований під запит користувача
- Не копіюй текст із каталогу
- Не вигадуй нові книги — тільки з CATALOG

USER_REQUEST:
{query}

CATALOG:
{json.dumps(catalog, ensure_ascii=False)}

Поверни тільки JSON:

{{
  "items": [
    {{
      "id": 0,
      "reason": "2-3 речення унікального опису"
    }},
    {{
      "id": 1,
      "reason": "2-3 речення унікального опису"
    }},
    {{
      "id": 2,
      "reason": "2-3 речення унікального опису"
    }}
  ]
}}
"""

    try:
        response = await openai_client.responses.create(
            model=AI_MODEL,
            input=prompt,
            temperature=0.4,
        )

        raw = response.output_text.strip()
        data = json.loads(raw)

        selected = []
        reasons = {}

        for item in data.get("items", []):
            idx = int(item.get("id"))
            if 0 <= idx < len(candidates):
                book = candidates[idx]
                if book.get("title") not in [b.get("title") for b in selected]:
                    selected.append(book)
                    reasons[book.get("title")] = item.get("reason", "")

        if selected:
            return selected[:3], reasons

    except Exception as e:
        print(f"AI error: {e}")

    # Резервний варіант, якщо AI недоступний або ключ не додано
    fallback = [
        b for b in candidates
        if b.get("title") not in user_shown_books.get(user_id, [])
    ][:3]
    return fallback, {}


@dp.message(CommandStart())
async def start(message: types.Message):
    user_id = message.from_user.id

    user_saved_books.setdefault(user_id, [])
    user_shown_books.setdefault(user_id, [])
    user_answers[user_id] = {}

    await message.answer(
    "Привіт 📚\n"
    "Я твій особистий AI-помічник для підбору книг.\n\n"
    "Можеш одразу написати, яку книгу хочеш, або натиснути кнопку нижче.",
    reply_markup=main_menu_keyboard()
)

    await message.answer(
    "Обери жанр:",
    reply_markup=make_keyboard("genre")
)


@dp.message()
async def handle_text_request(message: types.Message):
    user_id = message.from_user.id
    text = (message.text or "").strip()

    if not text:
        return

    user_saved_books.setdefault(user_id, [])
    user_shown_books.setdefault(user_id, [])

    # 📚 Обрати жанр
    if text == "📚 Обрати жанр":
        user_answers[user_id] = {}
        await message.answer(
            "Обери жанр:",
            reply_markup=make_keyboard("genre")
        )
        return

    # 📌 Збережені книги
    if text == "📌 Мої збережені книги":
        saved = user_saved_books.get(user_id, [])

        if not saved:
            await message.answer("У тебе ще немає збережених книг 📌")
            return

        result = "📌 Твої збережені книги:\n\n"
        for i, book in enumerate(saved, start=1):
            result += f"{i}. {book.get('title')} — {book.get('author')}\n"

        await message.answer(result)
        return

    # 🤖 AI відповідає
    await message.answer("🤖 Думаю...")

    try:
        response = await openai_client.responses.create(
            model=AI_MODEL,
            input=f"""
Ти дружній AI-книжковий консультант.

Якщо запит про книги — допоможи підібрати.
Якщо це питання — відповідай як людина, просто і зрозуміло.

Повідомлення:
{text}
"""
        )

        reply = response.output[0].content[0].text

    except Exception as e:
        print("AI error:", e)
        reply = "Щось пішло не так 😢"

    await message.answer(reply)
@dp.callback_query()
async def handle_buttons(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data

    user_answers.setdefault(user_id, {})
    user_shown_books.setdefault(user_id, [])
    user_saved_books.setdefault(user_id, [])

    if data == "saved:show":
        saved = user_saved_books.get(user_id, [])

        if not saved:
            await callback.message.answer("У тебе ще немає збережених книг 📌")
            await callback.answer()
            return

        text = "📌 Твої збережені книги:\n\n"
        for i, book in enumerate(saved, start=1):
            text += f"{i}. {book.get('title')} — {book.get('author')}\n"
            if book.get("buy_link"):
                text += f"   Yakaboo: {book.get('buy_link')}\n"

        await callback.message.answer(text)
        await callback.answer()
        return

    if data.startswith("save:"):
        index = int(data.split(":")[1])
        books = last_sent_books.get(user_id, [])

        if index >= len(books):
            await callback.answer("Не знайшла цю книгу")
            return

        book = books[index]
        already_saved = any(
            saved_book.get("title") == book.get("title")
            for saved_book in user_saved_books[user_id]
        )

        if not already_saved:
            user_saved_books[user_id].append(book)
            await callback.answer("Книгу збережено ❤️")
        else:
            await callback.answer("Ця книга вже збережена 📌")
        return

    if data.startswith("delete_saved:"):
        index = int(data.split(":")[1])
        books = last_sent_books.get(user_id, [])

        if index >= len(books):
            await callback.answer("Не знайшла цю книгу")
            return

        book = books[index]
        user_saved_books[user_id] = [
            saved_book for saved_book in user_saved_books[user_id]
            if saved_book.get("title") != book.get("title")
        ]

        await callback.answer("Видалено зі збережених 🗑")
        return

    if data.startswith("similar:"):
        index = int(data.split(":")[1])
        books = last_sent_books.get(user_id, [])

        if index >= len(books):
            await callback.answer("Не знайшла цю книгу")
            return

        selected_book = books[index]
        genre = selected_book.get("genre")

        similar_books = [
            book for book in BOOKS
            if book.get("genre") == genre
            and book.get("title") != selected_book.get("title")
            and book.get("title") not in user_shown_books.get(user_id, [])
        ][:3]

        if not similar_books:
            await callback.message.answer("Схожих книг поки не знайшла 😢")
            await callback.answer()
            return

        for book in similar_books:
            user_shown_books[user_id].append(book.get("title"))

        await callback.answer("Шукаю схожі книги 🔎")
        await send_books(callback, similar_books)
        return

    if data == "restart:yes":
        user_answers[user_id] = {}

        await callback.message.answer(
            "Обери жанр:",
            reply_markup=make_keyboard("genre")
        )
        await callback.answer()
        return

    if data == "more:yes":
        await callback.answer("Шукаю книги 📚")
        books = find_books(user_id)

        if not books:
            await callback.message.answer(
                "Поки що більше варіантів не знайшла 📚\nСпробуй інший жанр або напиши запит текстом.",
                reply_markup=after_result_keyboard()
            )
            return

        for book in books:
            user_shown_books[user_id].append(book.get("title"))

        await send_books(callback, books)
        return

    step, value = data.split(":")
    user_answers[user_id][step] = value

    if step == "genre":
        await callback.message.answer(
            "Який настрій книги?",
            reply_markup=make_keyboard("mood")
        )
        await callback.answer()
        return

    if step == "mood":
        await callback.message.answer(
            "Яка складність?",
            reply_markup=make_keyboard("level")
        )
        await callback.answer()
        return

    if step == "level":
        await callback.message.answer(
            "Чого краще уникати?",
            reply_markup=make_keyboard("avoid")
        )
        await callback.answer()
        return

    if step == "avoid":
        await callback.answer("Шукаю книги 📚")

        query = f"""
Користувач обрав:
жанр: {user_answers[user_id].get("genre")}
настрій: {user_answers[user_id].get("mood")}
складність: {user_answers[user_id].get("level")}
уникати: {user_answers[user_id].get("avoid")}
"""

        books, reasons = await ai_recommend_books(query, user_id)

        if not books:
            await callback.message.answer(
                "Немає варіантів 😢\nСпробуй інший жанр або напиши запит текстом.",
                reply_markup=after_result_keyboard()
            )
            return

        for book in books:
            user_shown_books[user_id].append(book.get("title"))

        await send_books(callback, books, reasons)
        return


async def health_check(request):
    return web.Response(text="Bot is running")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()


async def main():
    await start_web_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
