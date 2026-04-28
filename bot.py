import asyncio
import os

from aiohttp import web
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from books import BOOKS

load_dotenv()

bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher()

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


def after_result_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Підібрати ще 3", callback_data="more:yes")],
            [InlineKeyboardButton(text="📌 Мої збережені", callback_data="saved:show")],
            [InlineKeyboardButton(text="Інший жанр", callback_data="restart:yes")]
        ]
    )


def book_action_keyboard(book_index):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❤️ Зберегти", callback_data=f"save:{book_index}")],
            [InlineKeyboardButton(text="🔎 Схожі книги", callback_data=f"similar:{book_index}")],
            [InlineKeyboardButton(text="🗑 Видалити зі збережених", callback_data=f"delete_saved:{book_index}")]
        ]
    )


def find_books(user_id):
    answers = user_answers.get(user_id, {})
    genre = answers.get("genre")
    level = answers.get("level", "any")
    shown = user_shown_books.get(user_id, [])

    books = [b for b in BOOKS if b.get("genre") == genre]

    if level != "any":
        filtered = [b for b in books if b.get("level") == level]
        if len(filtered) >= 3:
            books = filtered

    books = [
        b for b in books
        if b.get("title") not in shown
    ]

    return books[:3]


async def send_books(callback, books):
    user_id = callback.from_user.id
    last_sent_books[user_id] = books

    for index, book in enumerate(books):
        text = (
            f"📚 {book.get('title')}\n"
            f"👤 Автор: {book.get('author')}\n\n"
            f"📝 Опис:\n{book.get('description')}"
        )

        await callback.message.answer(
            text,
            reply_markup=book_action_keyboard(index)
        )

    await callback.message.answer(
        "Що робимо далі?",
        reply_markup=after_result_keyboard()
    )


@dp.message(CommandStart())
async def start(message: types.Message):
    user_id = message.from_user.id

    if user_id not in user_saved_books:
        user_saved_books[user_id] = []

    if user_id not in user_shown_books:
        user_shown_books[user_id] = []

    user_answers[user_id] = {}

    await message.answer(
        "Привіт 📚\nЯ допоможу підібрати книгу.\n\nОбери жанр:",
        reply_markup=make_keyboard("genre")
    )


@dp.callback_query()
async def handle_buttons(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data

    if user_id not in user_answers:
        user_answers[user_id] = {}

    if user_id not in user_shown_books:
        user_shown_books[user_id] = []

    if user_id not in user_saved_books:
        user_saved_books[user_id] = []

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
        ]

        if not similar_books:
            await callback.message.answer("Схожих книг поки не знайшла 😢")
            await callback.answer()
            return

        similar_books = similar_books[:3]

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
                "Поки що більше варіантів не знайшла 📚\nСпробуй інший жанр.",
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

        books = find_books(user_id)

        if not books:
            await callback.message.answer(
                "Немає варіантів 😢\nСпробуй інший жанр.",
                reply_markup=after_result_keyboard()
            )
            return

        for book in books:
            user_shown_books[user_id].append(book.get("title"))

        await send_books(callback, books)
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
