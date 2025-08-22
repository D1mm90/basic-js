import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
	CallbackQuery,
	InlineKeyboardButton,
	InlineKeyboardMarkup,
	KeyboardButton,
	Message,
	ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

from items import ITEMS, STOCK

# ------------------ CONFIG ------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

DATA_FILE = os.path.join(os.path.dirname(__file__), "data.json")
ORDERS_FILE = os.path.join(os.path.dirname(__file__), "orders.json")

if not BOT_TOKEN:
	raise RuntimeError("BOT_TOKEN is not set in .env")

# ------------------ STATE ------------------
# Per-user session data
# basket: items to subtract (order) or add (return), depending on mode
# return_date: ISO date string 'YYYY-MM-DD'
# mode: 'order' | 'return'
user_sessions: Dict[int, Dict] = {}

router = Router()

# ------------------ PERSISTENCE ------------------

def load_stock() -> Dict[str, int]:
	if os.path.exists(DATA_FILE):
		with open(DATA_FILE, "r", encoding="utf-8") as f:
			try:
				data = json.load(f)
			except json.JSONDecodeError:
				data = {}
	else:
		data = {}
	# ensure defaults for missing items
	merged = STOCK.copy()
	for name, qty in data.items():
		merged[name] = int(qty)
	return merged


def save_stock(stock: Dict[str, int]) -> None:
	with open(DATA_FILE, "w", encoding="utf-8") as f:
		json.dump(stock, f, ensure_ascii=False, indent=2)


def append_order_record(record: Dict) -> None:
	records: List[Dict]
	if os.path.exists(ORDERS_FILE):
		try:
			with open(ORDERS_FILE, "r", encoding="utf-8") as f:
				records = json.load(f)
		except Exception:
			records = []
	else:
		records = []
	records.append(record)
	with open(ORDERS_FILE, "w", encoding="utf-8") as f:
		json.dump(records, f, ensure_ascii=False, indent=2)

# ------------------ KEYBOARDS ------------------

def main_menu_kb() -> ReplyKeyboardMarkup:
	return ReplyKeyboardMarkup(
		keyboard=[
			[KeyboardButton(text="📦 Собрать заказ")],
			[KeyboardButton(text="📊 Остатки")],
			[KeyboardButton(text="📥 Сдача оборудования")],
		],
		resize_keyboard=True,
		one_time_keyboard=False,
	)


def order_menu_kb() -> InlineKeyboardMarkup:
	kb = InlineKeyboardMarkup(inline_keyboard=[
		[InlineKeyboardButton(text="🎛 Аппаратура", callback_data="order_items_page_0")],
		[InlineKeyboardButton(text="📅 Дата возврата", callback_data="order_date_open")],
		[InlineKeyboardButton(text="✅ Подтвердить", callback_data="order_confirm")],
		[InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
	])
	return kb


def return_menu_kb() -> InlineKeyboardMarkup:
	kb = InlineKeyboardMarkup(inline_keyboard=[
		[InlineKeyboardButton(text="📅 Выбрать дату", callback_data="return_date_open")],
		[InlineKeyboardButton(text="🎛 Выбрать позиции", callback_data="return_items_page_0")],
		[InlineKeyboardButton(text="✅ Принять возврат", callback_data="return_confirm")],
		[InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
	])
	return kb


def date_keyboard(prefix: str) -> InlineKeyboardMarkup:
	# prefix: 'order' or 'return'
	today = datetime.today()
	rows: List[List[InlineKeyboardButton]] = []
	row: List[InlineKeyboardButton] = []
	for i in range(1, 8):
		day = today + timedelta(days=i)
		row.append(InlineKeyboardButton(text=day.strftime("%d.%m"), callback_data=f"{prefix}_date_{day.strftime('%Y-%m-%d')}"))
		if len(row) == 3:
			rows.append(row)
			row = []
	if row:
		rows.append(row)
	rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{prefix}_date_back")])
	return InlineKeyboardMarkup(inline_keyboard=rows)


def paginate(total: int, per_page: int, page: int) -> Tuple[int, int, int]:
	pages = max(1, (total + per_page - 1) // per_page)
	page = max(0, min(page, pages - 1))
	start = page * per_page
	end = min(total, start + per_page)
	return start, end, pages


def items_keyboard(prefix: str, page: int) -> InlineKeyboardMarkup:
	# prefix: 'order' or 'return'
	per_page = 10
	start, end, pages = paginate(len(ITEMS), per_page, page)
	rows: List[List[InlineKeyboardButton]] = []
	for idx in range(start, end):
		item = ITEMS[idx]
		rows.append([
			InlineKeyboardButton(text=f"➕ {item}", callback_data=f"{prefix}_add_{idx}"),
			InlineKeyboardButton(text=f"➖ {item}", callback_data=f"{prefix}_remove_{idx}"),
		])
	nav: List[InlineKeyboardButton] = []
	if page > 0:
		nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}_items_page_{page-1}"))
	if page < pages - 1:
		nav.append(InlineKeyboardButton(text="➡️", callback_data=f"{prefix}_items_page_{page+1}"))
	if nav:
		rows.append(nav)
	rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{prefix}_items_back")])
	return InlineKeyboardMarkup(inline_keyboard=rows)


# ------------------ HELPERS ------------------

def ensure_session(user_id: int) -> Dict:
	if user_id not in user_sessions:
		user_sessions[user_id] = {
			"basket": {},
			"return_date": None,
			"mode": "order",
		}
	return user_sessions[user_id]


def render_basket_text(basket: Dict[str, int], title: str) -> str:
	lines = [title]
	any_items = False
	for name, qty in basket.items():
		if qty > 0:
			lines.append(f"{name} × {qty}")
			any_items = True
	if not any_items:
		lines.append("пока пусто…")
	return "\n".join(lines)


# ------------------ HANDLERS ------------------

@router.message(CommandStart())
async def on_start(message: Message) -> None:
	await message.answer("Привет! 👋\nЯ бот для управления складом оборудования.", reply_markup=main_menu_kb())


@router.message(F.text == "📦 Собрать заказ")
async def start_order(message: Message) -> None:
	session = ensure_session(message.from_user.id)
	session["basket"] = {}
	session["return_date"] = None
	session["mode"] = "order"
	await message.answer("Собери заказ 🛠", reply_markup=order_menu_kb())


@router.message(F.text == "📊 Остатки")
async def show_stock_entry(message: Message) -> None:
	stock = load_stock()
	page = 0
	per_page = 20
	start, end, pages = paginate(len(stock), per_page, page)
	items = list(stock.items())[start:end]
	text_lines = ["📊 Остатки:" ] + [f"{name}: {qty}" for name, qty in items]
	kb = InlineKeyboardMarkup(inline_keyboard=[
		[
			InlineKeyboardButton(text="➡️", callback_data="stock_page_1")
		] if (len(stock) > per_page) else [],
	])
	await message.answer("\n".join(text_lines), reply_markup=kb if len(stock) > per_page else None)


@router.message(F.text == "📥 Сдача оборудования")
async def start_return(message: Message) -> None:
	session = ensure_session(message.from_user.id)
	session["basket"] = {}
	session["return_date"] = None
	session["mode"] = "return"
	await message.answer("Сдача оборудования 📥", reply_markup=return_menu_kb())


# ---- STOCK PAGINATION ----

@router.callback_query(F.data.startswith("stock_page_"))
async def stock_page(callback: CallbackQuery) -> None:
	stock = load_stock()
	per_page = 20
	try:
		page = int(callback.data.split("_")[-1])
	except Exception:
		page = 0
	start, end, pages = paginate(len(stock), per_page, page)
	items = list(stock.items())[start:end]
	text_lines = ["📊 Остатки:"] + [f"{name}: {qty}" for name, qty in items]
	nav: List[InlineKeyboardButton] = []
	if page > 0:
		nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"stock_page_{page-1}"))
	if page < pages - 1:
		nav.append(InlineKeyboardButton(text="➡️", callback_data=f"stock_page_{page+1}"))
	kb = InlineKeyboardMarkup(inline_keyboard=[nav] if nav else [])
	try:
		await callback.message.edit_text("\n".join(text_lines), reply_markup=kb if nav else None)
	except Exception:
		await callback.message.answer("\n".join(text_lines), reply_markup=kb if nav else None)
	await callback.answer()


# ---- ORDER FLOW ----

@router.callback_query(F.data == "back_main")
async def back_main(callback: CallbackQuery) -> None:
	await callback.message.answer("Главное меню 📋", reply_markup=main_menu_kb())
	await callback.answer()


@router.callback_query(F.data == "order_items_back")
async def order_items_back(callback: CallbackQuery) -> None:
	await callback.message.edit_text("Собери заказ 🛠", reply_markup=order_menu_kb())
	await callback.answer()


@router.callback_query(F.data.startswith("order_items_page_"))
async def order_items_page(callback: CallbackQuery) -> None:
	page = int(callback.data.rsplit("_", 1)[-1])
	await callback.message.edit_text("Выбери аппаратуру 🎛", reply_markup=items_keyboard("order", page))
	await callback.answer()


@router.callback_query(F.data.startswith("order_add_"))
async def order_add(callback: CallbackQuery) -> None:
	idx = int(callback.data.rsplit("_", 1)[-1])
	user_id = callback.from_user.id
	session = ensure_session(user_id)
	item = ITEMS[idx]
	session["basket"][item] = session["basket"].get(item, 0) + 1
	text = render_basket_text(session["basket"], "📝 Текущий заказ:")
	# keep current page if possible
	await callback.message.edit_text(text, reply_markup=items_keyboard("order", 0))
	await callback.answer()


@router.callback_query(F.data.startswith("order_remove_"))
async def order_remove(callback: CallbackQuery) -> None:
	idx = int(callback.data.rsplit("_", 1)[-1])
	user_id = callback.from_user.id
	session = ensure_session(user_id)
	item = ITEMS[idx]
	if session["basket"].get(item, 0) > 0:
		session["basket"][item] -= 1
	text = render_basket_text(session["basket"], "📝 Текущий заказ:")
	await callback.message.edit_text(text, reply_markup=items_keyboard("order", 0))
	await callback.answer()


@router.callback_query(F.data == "order_date_open")
async def order_date_open(callback: CallbackQuery) -> None:
	await callback.message.edit_text("Выбери дату возврата 📅", reply_markup=date_keyboard("order"))
	await callback.answer()


@router.callback_query(F.data == "order_date_back")
async def order_date_back(callback: CallbackQuery) -> None:
	await callback.message.edit_text("Собери заказ 🛠", reply_markup=order_menu_kb())
	await callback.answer()


@router.callback_query(F.data.startswith("order_date_"))
async def order_date_set(callback: CallbackQuery) -> None:
	date_str = callback.data.split("_", 2)[2]
	session = ensure_session(callback.from_user.id)
	session["return_date"] = date_str
	await callback.message.edit_text(f"📅 Дата возврата: {date_str}", reply_markup=order_menu_kb())
	await callback.answer()


@router.callback_query(F.data == "order_confirm")
async def order_confirm(callback: CallbackQuery) -> None:
	user_id = callback.from_user.id
	session = ensure_session(user_id)
	order = {k: v for k, v in session["basket"].items() if v > 0}
	if not order:
		await callback.answer("Заказ пуст 🚫", show_alert=True)
		return
	stock = load_stock()
	# validate
	for item, qty in order.items():
		if stock.get(item, 0) < qty:
			await callback.answer(f"❌ Недостаточно: {item}", show_alert=True)
			return
	# apply
	for item, qty in order.items():
		stock[item] = stock.get(item, 0) - qty
	save_stock(stock)
	append_order_record({
		"type": "order",
		"user_id": user_id,
		"username": callback.from_user.username,
		"basket": order,
		"return_date": session.get("return_date"),
		"timestamp": datetime.utcnow().isoformat(),
	})
	# reset
	session["basket"] = {}
	session["return_date"] = None
	await callback.message.edit_text("✅ Заказ подтвержден!", reply_markup=None)
	await callback.message.answer("Главное меню 📋", reply_markup=main_menu_kb())
	await callback.answer()


# ---- RETURN FLOW ----

@router.callback_query(F.data == "return_items_back")
async def return_items_back(callback: CallbackQuery) -> None:
	await callback.message.edit_text("Сдача оборудования 📥", reply_markup=return_menu_kb())
	await callback.answer()


@router.callback_query(F.data == "return_date_open")
async def return_date_open(callback: CallbackQuery) -> None:
	await callback.message.edit_text("Выбери дату возврата 📅", reply_markup=date_keyboard("return"))
	await callback.answer()


@router.callback_query(F.data == "return_date_back")
async def return_date_back(callback: CallbackQuery) -> None:
	await callback.message.edit_text("Сдача оборудования 📥", reply_markup=return_menu_kb())
	await callback.answer()


@router.callback_query(F.data.startswith("return_date_"))
async def return_date_set(callback: CallbackQuery) -> None:
	date_str = callback.data.split("_", 2)[2]
	session = ensure_session(callback.from_user.id)
	session["return_date"] = date_str
	await callback.message.edit_text(f"📅 Дата возврата выбрана: {date_str}", reply_markup=return_menu_kb())
	await callback.answer()


@router.callback_query(F.data.startswith("return_items_page_"))
async def return_items_page(callback: CallbackQuery) -> None:
	page = int(callback.data.rsplit("_", 1)[-1])
	await callback.message.edit_text("Выбери позиции для возврата 📦", reply_markup=items_keyboard("return", page))
	await callback.answer()


@router.callback_query(F.data.startswith("return_add_"))
async def return_add(callback: CallbackQuery) -> None:
	idx = int(callback.data.rsplit("_", 1)[-1])
	session = ensure_session(callback.from_user.id)
	item = ITEMS[idx]
	session["basket"][item] = session["basket"].get(item, 0) + 1
	text = render_basket_text(session["basket"], "📝 К возврату:")
	await callback.message.edit_text(text, reply_markup=items_keyboard("return", 0))
	await callback.answer()


@router.callback_query(F.data.startswith("return_remove_"))
async def return_remove(callback: CallbackQuery) -> None:
	idx = int(callback.data.rsplit("_", 1)[-1])
	session = ensure_session(callback.from_user.id)
	item = ITEMS[idx]
	if session["basket"].get(item, 0) > 0:
		session["basket"][item] -= 1
	text = render_basket_text(session["basket"], "📝 К возврату:")
	await callback.message.edit_text(text, reply_markup=items_keyboard("return", 0))
	await callback.answer()


@router.callback_query(F.data == "return_confirm")
async def return_confirm(callback: CallbackQuery) -> None:
	session = ensure_session(callback.from_user.id)
	ret = {k: v for k, v in session["basket"].items() if v > 0}
	if not ret:
		await callback.answer("Корзина пуста 🚫", show_alert=True)
		return
	stock = load_stock()
	for item, qty in ret.items():
		stock[item] = stock.get(item, 0) + qty
	save_stock(stock)
	append_order_record({
		"type": "return",
		"user_id": callback.from_user.id,
		"username": callback.from_user.username,
		"basket": ret,
		"return_date": session.get("return_date"),
		"timestamp": datetime.utcnow().isoformat(),
	})
	session["basket"] = {}
	session["return_date"] = None
	await callback.message.edit_text("✅ Возврат принят!", reply_markup=None)
	await callback.message.answer("Главное меню 📋", reply_markup=main_menu_kb())
	await callback.answer()


# ------------------ ENTRYPOINT ------------------

async def main() -> None:
	bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
	dp = Dispatcher()
	dp.include_router(router)
	await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
	asyncio.run(main())