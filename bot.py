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

openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
AI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

user_answers = {}
user_shown_books = {}
user_saved_books = {}
last_sent_books = {}
last_user_query = {}

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
    },
}


def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📚 Обрати жанр")],
            [KeyboardButton(text="📌 Мої збережені книги")],
        ],
        resize_keyboard=True,
    )


def make_keyboard(step):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=f"{step}:{code}")]
            for code, text in OPTIONS[step].items()
        ]
    )


def after_result_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Підібрати ще 3", callback_data="more:yes")],
            [InlineKeyboardButton(text="📌 Мої збережені", callback_data="saved:show")],
            [InlineKeyboardButton(text="Інший жанр", callback_data="restart:yes")],
        ]
    )


def book_action_keyboard(book_index, book=None):
    buttons = []

    buy_link = (book or {}).get("buy_link")
    if buy_link:
        buttons.append([InlineKeyboardButton(text="🛒 Дивитися на Yakaboo", url=buy_link)])

    ksd_link = (book or {}).get("ksd_link")
    if ksd_link:
        buttons.append([InlineKeyboardButton(text="🛒 Дивитися на КСД", url=ksd_link)])

    buttons.extend([
        [InlineKeyboardButton(text="❤️ Зберегти", callback_data=f"save:{book_index}")],
        [InlineKeyboardButton(text="🔎 Схожі книги", callback_data=f"similar:{book_index}")],
        [InlineKeyboardButton(text="🗑 Видалити зі збережених", callback_data=f"delete_saved:{book_index}")],
        [InlineKeyboardButton(text="ℹ️ Детальніше про книгу", callback_data=f"details:{book_index}")],
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def user_filter_query(user_id):
    answers = user_answers.get(user_id, {})
    return f"""
Користувач обрав:
жанр: {answers.get("genre")}
настрій: {answers.get("mood")}
складність: {answers.get("level")}
уникати: {answers.get("avoid")}
"""


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


def preselect_books(query, limit=30, user_id=None):
    shown = set(user_shown_books.get(user_id, [])) if user_id else set()

    scored = sorted(
        BOOKS,
        key=lambda b: local_score(query, b),
        reverse=True,
    )

    not_shown = [b for b in scored if b.get("title") not in shown]
    return (not_shown or scored)[:limit]


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


def book_to_text(book, ai_description=None):
    text = (
        f"📚 {book.get('title')}\n"
        f"👤 Автор: {book.get('author')}\n"
        f"🏷 Жанр: {book.get('genre')}\n\n"
    )

    if ai_description:
        text += f"📝 Опис:\n{ai_description}"
    else:
        text += "📝 Опис:\nAI-опис не згенерувався. Спробуй написати запит трохи детальніше."

    return text


async def send_books(callback_or_message, books, descriptions=None):
    user_id = callback_or_message.from_user.id
    last_sent_books[user_id] = books
    descriptions = descriptions or {}

    message = callback_or_message.message if hasattr(callback_or_message, "message") else callback_or_message

    for index, book in enumerate(books):
        text = book_to_text(book, descriptions.get(book.get("title")))
        image = book.get("image") or book.get("image_url") or book.get("cover") or book.get("cover_url")

        if image:
            await message.answer_photo(
                photo=image,
                caption=text[:1024],
                reply_markup=book_action_keyboard(index, book),
            )
            if len(text) > 1024:
                await message.answer(text[1024:])
        else:
            await message.answer(
                text,
                reply_markup=book_action_keyboard(index, book),
            )

    await message.answer(
        "Можеш написати запит своїми словами або обрати дію нижче.",
        reply_markup=after_result_keyboard(),
    )


async def ai_recommend_books(query, user_id):
    candidates = preselect_books(query, limit=30, user_id=user_id)

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
Ти розумний AI-книжковий консультант українською мовою.

Завдання:
- Обери рівно 3 книги тільки зі списку CATALOG.
- Не вигадуй книжки, авторів або посилання.
- Для КОЖНОЇ книги напиши індивідуальний опис на 2-3 речення.
- Опис має бути живий, природний і персоналізований під запит користувача.
- Не копіюй description із CATALOG. Напиши новий текст своїми словами.
- Не додавай посилання в текст.

USER_REQUEST:
{query}

CATALOG:
{json.dumps(catalog, ensure_ascii=False)}

Поверни тільки JSON без markdown:
{{
  "items": [
    {{"id": 0, "description": "2-3 унікальні речення під запит користувача"}},
    {{"id": 1, "description": "2-3 унікальні речення під запит користувача"}},
    {{"id": 2, "description": "2-3 унікальні речення під запит користувача"}}
  ]
}}
"""

    try:
        response = await openai_client.responses.create(
            model=AI_MODEL,
            input=prompt,
            temperature=0.7,
        )

        raw = response.output_text.strip()
        data = json.loads(raw)

        selected = []
        descriptions = {}

        for item in data.get("items", []):
            idx = int(item.get("id"))
            if 0 <= idx < len(candidates):
                book = candidates[idx]
                title = book.get("title")
                if title not in [b.get("title") for b in selected]:
                    selected.append(book)
                    descriptions[title] = item.get("description") or item.get("reason") or ""

        if selected:
            return selected[:3], descriptions

    except Exception as e:
        print(f"AI error: {e}")

    fallback = candidates[:3]
    fallback_descriptions = {
        book.get("title"): (
            f"Ця книга може підійти під твій запит за жанром, настроєм і загальною атмосферою. "
            f"Вона дає зрозумілу точку входу в тему та може стати хорошим варіантом для наступного читання."
        )
        for book in fallback
    }
    return fallback, fallback_descriptions


async def ai_answer_text(user_text):
    try:
        response = await openai_client.responses.create(
            model=AI_MODEL,
            input=f"""
Ти дружній AI-книжковий консультант українською мовою.
Відповідай просто, корисно і без довгих пояснень.
Якщо користувач питає про книгу — допоможи розібратися.

Повідомлення користувача:
{user_text}
""",
            temperature=0.7,
        )
        return response.output_text.strip()
    except Exception as e:
        print(f"AI chat error: {e}")
        return "Зараз не можу нормально відповісти через помилку AI 😢 Спробуй ще раз трохи пізніше."


async def ai_book_details(book, user_text=""):
    try:
        response = await openai_client.responses.create(
            model=AI_MODEL,
            input=f"""
Напиши українською детальніший опис книги на 4-6 речень.
Не вигадуй фактів, яких не знаєш. Поясни атмосферу, кому може сподобатися і чому варто звернути увагу.

Книга:
Назва: {book.get("title")}
Автор: {book.get("author")}
Жанр: {book.get("genre")}
Базовий опис: {book.get("description")}

Додатковий запит користувача:
{user_text}
""",
            temperature=0.7,
        )
        return response.output_text.strip()
    except Exception as e:
        print(f"AI details error: {e}")
        return "Не вдалося згенерувати детальний опис 😢"


def is_book_request(text):
    keywords = [
        "книг", "чит", "порад", "підбер", "роман", "детектив", "фентезі",
        "класик", "жанр", "щось", "хочу", "люблю", "не люблю", "автор",
        "легк", "складн", "мотивац", "психолог", "кохан", "жах", "пригод",
    ]
    t = text.lower()
    return any(k in t for k in keywords)


@dp.message(CommandStart())
async def start(message: types.Message):
    user_id = message.from_user.id

    user_saved_books.setdefault(user_id, [])
    user_shown_books.setdefault(user_id, [])
    user_answers[user_id] = {}

    await message.answer(
        "Привіт 📚\n"
        "Я твій особистий AI-помічник для підбору книг.\n\n"
        "Можу підібрати книгу відповідно до вподобань читача: жанру, настрою, складності та тем, яких краще уникати.\n"
        "Можеш одразу написати, що хочеш почитати, або натиснути кнопку нижче.",
        reply_markup=main_menu_keyboard(),
    )

    await message.answer(
        "Обери жанр:",
        reply_markup=make_keyboard("genre"),
    )


@dp.message()
async def handle_text_request(message: types.Message):
    user_id = message.from_user.id
    text = (message.text or "").strip()

    if not text:
        return

    user_saved_books.setdefault(user_id, [])
    user_shown_books.setdefault(user_id, [])
    user_answers.setdefault(user_id, {})

    if text == "📚 Обрати жанр":
        user_answers[user_id] = {}
        await message.answer(
            "Обери жанр:",
            reply_markup=make_keyboard("genre"),
        )
        return

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

    if is_book_request(text):
        last_user_query[user_id] = text
        await message.answer("🤖 Підбираю книги...")

        books, descriptions = await ai_recommend_books(text, user_id)

        if not books:
            await message.answer("Не знайшла відповідних книг 😢 Спробуй описати бажання трохи інакше.")
            return

        for book in books:
            title = book.get("title")
            if title not in user_shown_books[user_id]:
                user_shown_books[user_id].append(title)

        await send_books(message, books, descriptions)
        return

    await message.answer("🤖 Думаю...")
    reply = await ai_answer_text(text)
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

    if data.startswith("details:"):
        index = int(data.split(":")[1])
        books = last_sent_books.get(user_id, [])

        if index >= len(books):
            await callback.answer("Не знайшла цю книгу")
            return

        await callback.answer("Готую детальніший опис ℹ️")
        book = books[index]
        details = await ai_book_details(book, last_user_query.get(user_id, ""))
        await callback.message.answer(
            f"ℹ️ Детальніше про «{book.get('title')}»\n\n{details}",
            reply_markup=book_action_keyboard(index, book),
        )
        return

    if data.startswith("similar:"):
        index = int(data.split(":")[1])
        books = last_sent_books.get(user_id, [])

        if index >= len(books):
            await callback.answer("Не знайшла цю книгу")
            return

        selected_book = books[index]
        query = (
            f"Підбери 3 книги, схожі на «{selected_book.get('title')}» "
            f"автора {selected_book.get('author')}. Жанр: {selected_book.get('genre')}."
        )
        last_user_query[user_id] = query

        await callback.answer("Шукаю схожі книги 🔎")
        similar_books, descriptions = await ai_recommend_books(query, user_id)

        similar_books = [
            book for book in similar_books
            if book.get("title") != selected_book.get("title")
        ][:3]

        if not similar_books:
            await callback.message.answer("Схожих книг поки не знайшла 😢")
            return

        for book in similar_books:
            title = book.get("title")
            if title not in user_shown_books[user_id]:
                user_shown_books[user_id].append(title)

        await send_books(callback, similar_books, descriptions)
        return

    if data == "restart:yes":
        user_answers[user_id] = {}

        await callback.message.answer(
            "Обери жанр:",
            reply_markup=make_keyboard("genre"),
        )
        await callback.answer()
        return

    if data == "more:yes":
        await callback.answer("Шукаю книги 📚")

        query = last_user_query.get(user_id) or user_filter_query(user_id) or "Підбери ще 3 книги"
        books, descriptions = await ai_recommend_books(query, user_id)

        if not books:
            await callback.message.answer(
                "Поки що більше варіантів не знайшла 📚\nСпробуй інший жанр або напиши запит текстом.",
                reply_markup=after_result_keyboard(),
            )
            return

        for book in books:
            title = book.get("title")
            if title not in user_shown_books[user_id]:
                user_shown_books[user_id].append(title)

        await send_books(callback, books, descriptions)
        return

    step, value = data.split(":")
    user_answers[user_id][step] = value

    if step == "genre":
        await callback.message.answer(
            "Який настрій книги?",
            reply_markup=make_keyboard("mood"),
        )
        await callback.answer()
        return

    if step == "mood":
        await callback.message.answer(
            "Яка складність?",
            reply_markup=make_keyboard("level"),
        )
        await callback.answer()
        return

    if step == "level":
        await callback.message.answer(
            "Чого краще уникати?",
            reply_markup=make_keyboard("avoid"),
        )
        await callback.answer()
        return

    if step == "avoid":
        await callback.answer("Шукаю книги 📚")

        query = user_filter_query(user_id)
        last_user_query[user_id] = query

        books, descriptions = await ai_recommend_books(query, user_id)

        if not books:
            await callback.message.answer(
                "Немає варіантів 😢\nСпробуй інший жанр або напиши запит текстом.",
                reply_markup=after_result_keyboard(),
            )
            return

        for book in books:
            title = book.get("title")
            if title not in user_shown_books[user_id]:
                user_shown_books[user_id].append(title)

        await send_books(callback, books, descriptions)
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
