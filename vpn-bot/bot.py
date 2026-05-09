"""
Telegram Bot — Админ-панель для управления VPN подписками.
"""

import logging
import asyncio
from datetime import datetime

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes
)

from config import BOT_TOKEN, ADMIN_ID, get_flag, format_country
import database as db
from subscription_renderer import render_subscription_content, render_subscription_info
from github_manager import upload_subscription_file, delete_subscription_file, get_subscription_url
from ping_checker import ping_all_servers, format_ping_results

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# === Состояния ConversationHandler ===
# Создание подписки
CREATE_NAME, CREATE_DESC, CREATE_EXPIRE = range(3)
# Добавление сервера
ADD_SERVER_NAME, ADD_SERVER_KEY, ADD_SERVER_PROTO = range(10, 13)
# Редактирование
EDIT_FIELD, EDIT_VALUE = range(20, 22)


def admin_only(func):
    """Декоратор: только для админа"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            await update.message.reply_text("⛔ Доступ запрещён.")
            return ConversationHandler.END if hasattr(func, '__wrapped_conv__') else None
        return await func(update, context)
    return wrapper


def admin_only_callback(func):
    """Декоратор для callback query"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            await update.callback_query.answer("⛔ Доступ запрещён.", show_alert=True)
            return
        return await func(update, context)
    return wrapper


# ============================
# ГЛАВНОЕ МЕНЮ
# ============================

@admin_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ Создать подписку", callback_data="create_sub")],
        [InlineKeyboardButton("📋 Мои подписки", callback_data="list_subs")],
        [InlineKeyboardButton("📡 Проверить пинг", callback_data="ping_all")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🔐 <b>VPN Admin Panel</b>\n\n"
        "Добро пожаловать в панель управления VPN-подписками.\n"
        "Выберите действие:",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )


@admin_only
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню (алиас /start)"""
    await start(update, context)


@admin_only_callback
async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вернуться в главное меню через callback"""
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("➕ Создать подписку", callback_data="create_sub")],
        [InlineKeyboardButton("📋 Мои подписки", callback_data="list_subs")],
        [InlineKeyboardButton("📡 Проверить пинг", callback_data="ping_all")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "🔐 <b>VPN Admin Panel</b>\n\n"
        "Выберите действие:",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )


# ============================
# СОЗДАНИЕ ПОДПИСКИ
# ============================

@admin_only_callback
async def create_sub_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "📝 <b>Создание новой подписки</b>\n\n"
        "Введите <b>название</b> подписки:\n"
        "Например: <i>Premium VPN</i> или <i>Подписка Ивана</i>\n\n"
        "/cancel — отменить",
        parse_mode="HTML"
    )
    return CREATE_NAME


async def create_sub_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    context.user_data["new_sub_name"] = update.message.text.strip()

    await update.message.reply_text(
        "📄 Введите <b>описание</b> подписки:\n"
        "Например: <i>VPN для работы, 5 серверов</i>\n\n"
        "/cancel — отменить",
        parse_mode="HTML"
    )
    return CREATE_DESC


async def create_sub_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    context.user_data["new_sub_desc"] = update.message.text.strip()

    await update.message.reply_text(
        "📅 Введите <b>дату окончания</b> подписки:\n"
        "Формат: <code>ГГГГ-ММ-ДД</code>\n"
        "Например: <code>2025-12-31</code>\n\n"
        "/cancel — отменить",
        parse_mode="HTML"
    )
    return CREATE_EXPIRE


async def create_sub_expire(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    date_str = update.message.text.strip()

    # Валидация даты
    try:
        expire_date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        await update.message.reply_text(
            "❌ Неверный формат даты!\n"
            "Используйте: <code>ГГГГ-ММ-ДД</code>\n"
            "Например: <code>2025-12-31</code>",
            parse_mode="HTML"
        )
        return CREATE_EXPIRE

    name = context.user_data["new_sub_name"]
    desc = context.user_data["new_sub_desc"]

    # Создаём подписку в базе
    sub = db.create_subscription(name, desc, date_str)

    # Создаём пустой файл на GitHub
    content = render_subscription_content([], name, desc)
    try:
        url = upload_subscription_file(
            sub["github_filename"],
            content,
            f"Create subscription: {name}"
        )
    except Exception as e:
        url = f"Ошибка GitHub: {e}"
        logger.error(f"GitHub upload error: {e}")

    sub_url = get_subscription_url(sub["github_filename"])

    keyboard = [
        [InlineKeyboardButton("🖥 Добавить сервер", callback_data=f"add_server_{sub['id']}")],
        [InlineKeyboardButton("📋 Все подписки", callback_data="list_subs")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
    ]

    await update.message.reply_text(
        f"✅ <b>Подписка создана!</b>\n\n"
        f"📝 Название: {name}\n"
        f"📄 Описание: {desc}\n"
        f"📅 До: {date_str}\n"
        f"🆔 ID: <code>{sub['id']}</code>\n\n"
        f"🔗 <b>Ссылка подписки:</b>\n"
        f"<code>{sub_url}</code>\n\n"
        f"👆 Эту ссылку нужно вставить в Happ\n\n"
        f"Теперь добавьте серверы (VLESS/Shadowsocks ключи)!",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

    # Чистим user_data
    context.user_data.pop("new_sub_name", None)
    context.user_data.pop("new_sub_desc", None)

    return ConversationHandler.END


async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❌ Действие отменено.",
        parse_mode="HTML"
    )
    context.user_data.clear()
    return ConversationHandler.END


# ============================
# СПИСОК ПОДПИСОК
# ============================

@admin_only_callback
async def list_subs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    subs = db.get_all_subscriptions()

    if not subs:
        keyboard = [
            [InlineKeyboardButton("➕ Создать подписку", callback_data="create_sub")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
        ]
        await query.edit_message_text(
            "📋 <b>Подписки</b>\n\n"
            "У вас пока нет подписок.\n"
            "Нажмите кнопку ниже, чтобы создать первую!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    keyboard = []
    for sub_id, sub in subs.items():
        # Статус
        try:
            expire = datetime.strptime(sub["expire_date"], "%Y-%m-%d")
            days_left = (expire - datetime.now()).days
            if days_left < 0:
                status = "⛔"
            elif days_left <= 3:
                status = "⚠️"
            else:
                status = "✅"
        except ValueError:
            status = "❓"

        servers_count = len(sub.get("servers", []))
        btn_text = f"{status} {sub['name']} ({servers_count} серв.)"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"view_sub_{sub_id}")])

    keyboard.append([InlineKeyboardButton("➕ Создать подписку", callback_data="create_sub")])
    keyboard.append([InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")])

    await query.edit_message_text(
        "📋 <b>Ваши подписки:</b>\n\n"
        "Нажмите на подписку для управления:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


# ============================
# ПРОСМОТР ПОДПИСКИ
# ============================

@admin_only_callback
async def view_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    sub_id = query.data.replace("view_sub_", "")
    sub = db.get_subscription(sub_id)

    if not sub:
        await query.edit_message_text("❌ Подписка не найдена.")
        return

    info = render_subscription_info(sub)
    sub_url = get_subscription_url(sub["github_filename"])

    keyboard = [
        [InlineKeyboardButton("🖥 Добавить сервер", callback_data=f"add_server_{sub_id}")],
        [InlineKeyboardButton("🗑 Удалить сервер", callback_data=f"del_server_{sub_id}")],
        [InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_sub_{sub_id}")],
        [InlineKeyboardButton("🔗 Получить ссылку", callback_data=f"get_link_{sub_id}")],
        [InlineKeyboardButton("🔄 Обновить на GitHub", callback_data=f"sync_sub_{sub_id}")],
        [InlineKeyboardButton("📡 Пинг серверов", callback_data=f"ping_sub_{sub_id}")],
        [InlineKeyboardButton("🗑 Удалить подписку", callback_data=f"confirm_del_sub_{sub_id}")],
        [InlineKeyboardButton("◀️ Назад", callback_data="list_subs")],
    ]

    await query.edit_message_text(
        info,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


# ============================
# ДОБАВЛЕНИЕ СЕРВЕРА
# ============================

@admin_only_callback
async def add_server_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    sub_id = query.data.replace("add_server_", "")
    context.user_data["adding_server_to"] = sub_id

    await query.edit_message_text(
        "🖥 <b>Добавление сервера</b>\n\n"
        "Введите <b>название страны/сервера</b>:\n"
        "Например: <i>Германия</i>, <i>США</i>, <i>Нидерланды</i>\n\n"
        "Флаг подставится автоматически! 🇩🇪🇺🇸🇳🇱\n\n"
        "/cancel — отменить",
        parse_mode="HTML"
    )
    return ADD_SERVER_NAME


async def add_server_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    name = update.message.text.strip()
    formatted = format_country(name)
    context.user_data["server_name"] = name

    await update.message.reply_text(
        f"Отлично! Сервер будет отображаться как: <b>{formatted}</b>\n\n"
        f"Теперь отправьте <b>VPN-ключ</b> (VLESS или Shadowsocks):\n\n"
        f"Пример VLESS:\n"
        f"<code>vless://uuid@host:port?params#name</code>\n\n"
        f"Пример Shadowsocks:\n"
        f"<code>ss://base64@host:port#name</code>\n\n"
        f"/cancel — отменить",
        parse_mode="HTML"
    )
    return ADD_SERVER_KEY


async def add_server_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    key = update.message.text.strip()

    # Автоопределение протокола
    if key.startswith("vless://"):
        protocol = "vless"
    elif key.startswith("ss://"):
        protocol = "shadowsocks"
    elif key.startswith("trojan://"):
        protocol = "trojan"
    elif key.startswith("vmess://"):
        protocol = "vmess"
    else:
        await update.message.reply_text(
            "⚠️ Не удалось определить протокол.\n"
            "Ключ должен начинаться с:\n"
            "• <code>vless://</code>\n"
            "• <code>ss://</code>\n"
            "• <code>trojan://</code>\n"
            "• <code>vmess://</code>\n\n"
            "Попробуйте ещё раз или /cancel для отмены",
            parse_mode="HTML"
        )
        return ADD_SERVER_KEY

    context.user_data["server_key"] = key
    context.user_data["server_protocol"] = protocol

    sub_id = context.user_data["adding_server_to"]
    server_name = context.user_data["server_name"]
    formatted = format_country(server_name)

    # Сохраняем в базу
    success = db.add_server_to_subscription(sub_id, server_name, key, protocol)

    if success:
        # Синхронизируем с GitHub
        sub = db.get_subscription(sub_id)
        servers = db.get_servers_of_subscription(sub_id)
        content = render_subscription_content(servers, sub["name"], sub["description"])

        try:
            upload_subscription_file(
                sub["github_filename"],
                content,
                f"Add server: {server_name}"
            )
            sync_status = "✅ Синхронизировано с GitHub"
        except Exception as e:
            sync_status = f"⚠️ Ошибка синхронизации: {e}"
            logger.error(f"GitHub sync error: {e}")

        keyboard = [
            [InlineKeyboardButton("🖥 Добавить ещё сервер", callback_data=f"add_server_{sub_id}")],
            [InlineKeyboardButton("👁 Просмотр подписки", callback_data=f"view_sub_{sub_id}")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
        ]

        await update.message.reply_text(
            f"✅ <b>Сервер добавлен!</b>\n\n"
            f"🖥 {formatted}\n"
            f"📡 Протокол: {protocol.upper()}\n"
            f"🔄 {sync_status}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text("❌ Ошибка добавления сервера.")

    # Чистим
    context.user_data.pop("server_name", None)
    context.user_data.pop("server_key", None)
    context.user_data.pop("server_protocol", None)
    context.user_data.pop("adding_server_to", None)

    return ConversationHandler.END


# ============================
# УДАЛЕНИЕ СЕРВЕРА
# ============================

@admin_only_callback
async def del_server_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    sub_id = query.data.replace("del_server_", "")
    servers = db.get_servers_of_subscription(sub_id)

    if not servers:
        await query.edit_message_text(
            "📭 В этой подписке нет серверов.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад", callback_data=f"view_sub_{sub_id}")]
            ]),
            parse_mode="HTML"
        )
        return

    keyboard = []
    for srv in servers:
        formatted = format_country(srv["name"])
        btn_text = f"🗑 {formatted} [{srv['protocol'].upper()}]"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"rm_srv_{sub_id}_{srv['id']}")])

    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data=f"view_sub_{sub_id}")])

    await query.edit_message_text(
        "🗑 <b>Удаление сервера</b>\n\n"
        "Выберите сервер для удаления:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


@admin_only_callback
async def remove_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.replace("rm_srv_", "").split("_")
    sub_id = parts[0]
    srv_id = parts[1]

    success = db.remove_server_from_subscription(sub_id, srv_id)

    if success:
        # Синхронизируем
        sub = db.get_subscription(sub_id)
        servers = db.get_servers_of_subscription(sub_id)
        content = render_subscription_content(servers, sub["name"], sub["description"])

        try:
            upload_subscription_file(sub["github_filename"], content, f"Remove server from {sub['name']}")
        except Exception as e:
            logger.error(f"GitHub sync error: {e}")

        await query.answer("✅ Сервер удалён!", show_alert=True)
    else:
        await query.answer("❌ Ошибка удаления!", show_alert=True)

    # Возвращаемся к просмотру подписки
    sub = db.get_subscription(sub_id)
    if sub:
        info = render_subscription_info(sub)
        keyboard = [
            [InlineKeyboardButton("🖥 Добавить сервер", callback_data=f"add_server_{sub_id}")],
            [InlineKeyboardButton("🗑 Удалить сервер", callback_data=f"del_server_{sub_id}")],
            [InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_sub_{sub_id}")],
            [InlineKeyboardButton("🔗 Получить ссылку", callback_data=f"get_link_{sub_id}")],
            [InlineKeyboardButton("🔄 Обновить на GitHub", callback_data=f"sync_sub_{sub_id}")],
            [InlineKeyboardButton("📡 Пинг серверов", callback_data=f"ping_sub_{sub_id}")],
            [InlineKeyboardButton("🗑 Удалить подписку", callback_data=f"confirm_del_sub_{sub_id}")],
            [InlineKeyboardButton("◀️ Назад", callback_data="list_subs")],
        ]
        await query.edit_message_text(info, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


# ============================
# ПОЛУЧИТЬ ССЫЛКУ
# ============================

@admin_only_callback
async def get_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    sub_id = query.data.replace("get_link_", "")
    sub = db.get_subscription(sub_id)

    if not sub:
        await query.answer("❌ Подписка не найдена!", show_alert=True)
        return

    url = get_subscription_url(sub["github_filename"])

    keyboard = [
        [InlineKeyboardButton("◀️ Назад", callback_data=f"view_sub_{sub_id}")],
    ]

    await query.edit_message_text(
        f"🔗 <b>Ссылка подписки</b>\n\n"
        f"📝 {sub['name']}\n\n"
        f"<code>{url}</code>\n\n"
        f"👆 Скопируйте и вставьте в <b>Happ</b>\n\n"
        f"📱 <b>Инструкция для клиента:</b>\n"
        f"1. Откройте Happ\n"
        f"2. Нажмите ➕ (добавить)\n"
        f"3. Выберите «Подписка»\n"
        f"4. Вставьте ссылку выше\n"
        f"5. Нажмите «Сохранить» ✅",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


# ============================
# СИНХРОНИЗАЦИЯ С GITHUB
# ============================

@admin_only_callback
async def sync_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🔄 Синхронизация...")

    sub_id = query.data.replace("sync_sub_", "")
    sub = db.get_subscription(sub_id)

    if not sub:
        await query.answer("❌ Подписка не найдена!", show_alert=True)
        return

    servers = db.get_servers_of_subscription(sub_id)
    content = render_subscription_content(servers, sub["name"], sub["description"])

    try:
        url = upload_subscription_file(
            sub["github_filename"],
            content,
            f"Sync: {sub['name']} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        await query.answer("✅ Успешно синхронизировано!", show_alert=True)
    except Exception as e:
        await query.answer(f"❌ Ошибка: {e}", show_alert=True)
        logger.error(f"Sync error: {e}")


# ============================
# ПИНГ
# ============================

@admin_only_callback
async def ping_sub_servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("📡 Проверяю серверы...")

    sub_id = query.data.replace("ping_sub_", "")
    servers = db.get_servers_of_subscription(sub_id)

    if not servers:
        await query.edit_message_text(
            "📭 Нет серверов для проверки.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад", callback_data=f"view_sub_{sub_id}")]
            ])
        )
        return

    await query.edit_message_text("📡 <b>Проверяю серверы, подождите...</b>", parse_mode="HTML")

    results = await ping_all_servers(servers)
    text = format_ping_results(results)

    keyboard = [
        [InlineKeyboardButton("🔄 Обновить", callback_data=f"ping_sub_{sub_id}")],
        [InlineKeyboardButton("◀️ Назад", callback_data=f"view_sub_{sub_id}")],
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


@admin_only_callback
async def ping_all_subs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("📡 Проверяю все серверы...")

    subs = db.get_all_subscriptions()

    all_servers = []
    for sub_id, sub in subs.items():
        for srv in sub.get("servers", []):
            all_servers.append(srv)

    if not all_servers:
        await query.edit_message_text(
            "📭 Нет серверов для проверки.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]
            ])
        )
        return

    await query.edit_message_text("📡 <b>Проверяю все серверы, подождите...</b>", parse_mode="HTML")

    results = await ping_all_servers(all_servers)
    text = format_ping_results(results)

    keyboard = [
        [InlineKeyboardButton("🔄 Обновить", callback_data="ping_all")],
        [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


# ============================
# РЕДАКТИРОВАНИЕ ПОДПИСКИ
# ============================

@admin_only_callback
async def edit_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    sub_id = query.data.replace("edit_sub_", "")
    sub = db.get_subscription(sub_id)

    if not sub:
        await query.answer("❌ Подписка не найдена!", show_alert=True)
        return

    keyboard = [
        [InlineKeyboardButton("📝 Название", callback_data=f"editf_name_{sub_id}")],
        [InlineKeyboardButton("📄 Описание", callback_data=f"editf_description_{sub_id}")],
        [InlineKeyboardButton("📅 Дата окончания", callback_data=f"editf_expire_date_{sub_id}")],
        [InlineKeyboardButton("◀️ Назад", callback_data=f"view_sub_{sub_id}")],
    ]

    await query.edit_message_text(
        f"✏️ <b>Редактирование подписки</b>\n\n"
        f"📝 Название: {sub['name']}\n"
        f"📄 Описание: {sub['description']}\n"
        f"📅 До: {sub['expire_date']}\n\n"
        f"Что изменить?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


@admin_only_callback
async def edit_field_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # editf_FIELD_SUBID
    data = query.data.replace("editf_", "")
    # name_abc123 или expire_date_abc123
    # Нужно аккуратно парсить
    parts = data.split("_")

    # Последняя часть — sub_id (8 символов)
    sub_id = parts[-1]
    field = "_".join(parts[:-1])

    context.user_data["edit_sub_id"] = sub_id
    context.user_data["edit_field"] = field

    field_names = {
        "name": "название",
        "description": "описание",
        "expire_date": "дату окончания (ГГГГ-ММ-ДД)"
    }

    await query.edit_message_text(
        f"✏️ Введите новое <b>{field_names.get(field, field)}</b>:\n\n"
        f"/cancel — отменить",
        parse_mode="HTML"
    )
    return EDIT_VALUE


async def edit_field_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    sub_id = context.user_data.get("edit_sub_id")
    field = context.user_data.get("edit_field")
    value = update.message.text.strip()

    if field == "expire_date":
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            await update.message.reply_text(
                "❌ Неверный формат! Используйте: <code>ГГГГ-ММ-ДД</code>",
                parse_mode="HTML"
            )
            return EDIT_VALUE

    sub = db.update_subscription(sub_id, **{field: value})

    if sub:
        # Синхронизируем с GitHub
        servers = db.get_servers_of_subscription(sub_id)
        content = render_subscription_content(servers, sub["name"], sub["description"])
        try:
            upload_subscription_file(sub["github_filename"], content, f"Edit {field}: {sub['name']}")
        except Exception as e:
            logger.error(f"GitHub sync error: {e}")

        keyboard = [
            [InlineKeyboardButton("👁 Просмотр", callback_data=f"view_sub_{sub_id}")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
        ]

        await update.message.reply_text(
            f"✅ <b>Подписка обновлена!</b>\n\n"
            f"📝 {sub['name']}\n"
            f"📄 {sub['description']}\n"
            f"📅 До: {sub['expire_date']}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text("❌ Ошибка обновления.")

    context.user_data.pop("edit_sub_id", None)
    context.user_data.pop("edit_field", None)

    return ConversationHandler.END


# ============================
# УДАЛЕНИЕ ПОДПИСКИ
# ============================

@admin_only_callback
async def confirm_delete_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    sub_id = query.data.replace("confirm_del_sub_", "")
    sub = db.get_subscription(sub_id)

    if not sub:
        await query.answer("❌ Не найдена!", show_alert=True)
        return

    keyboard = [
        [
            InlineKeyboardButton("✅ Да, удалить", callback_data=f"do_del_sub_{sub_id}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"view_sub_{sub_id}"),
        ],
    ]

    await query.edit_message_text(
        f"⚠️ <b>Вы уверены?</b>\n\n"
        f"Подписка <b>{sub['name']}</b> будет удалена навсегда!\n"
        f"Файл с GitHub тоже будет удалён.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


@admin_only_callback
async def do_delete_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    sub_id = query.data.replace("do_del_sub_", "")
    sub = db.get_subscription(sub_id)

    if sub:
        # Удаляем с GitHub
        try:
            delete_subscription_file(sub["github_filename"], f"Delete: {sub['name']}")
        except Exception as e:
            logger.error(f"GitHub delete error: {e}")

        # Удаляем из базы
        db.delete_subscription(sub_id)

        keyboard = [
            [InlineKeyboardButton("📋 Мои подписки", callback_data="list_subs")],
            [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
        ]

        await query.edit_message_text(
            f"🗑 Подписка <b>{sub['name']}</b> удалена.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    else:
        await query.answer("❌ Не найдена!", show_alert=True)


# ============================
# MAIN
# ============================

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Conversation: создание подписки
    create_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(create_sub_start, pattern="^create_sub$")],
        states={
            CREATE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_sub_name)],
            CREATE_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_sub_desc)],
            CREATE_EXPIRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_sub_expire)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        per_message=False,
    )

    # Conversation: добавление сервера
    add_server_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_server_start, pattern=r"^add_server_")],
        states={
            ADD_SERVER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_server_name)],
            ADD_SERVER_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_server_key)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        per_message=False,
    )

    # Conversation: редактирование
    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_field_start, pattern=r"^editf_")],
        states={
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_field_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        per_message=False,
    )

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))

    # Conversations (порядок важен!)
    app.add_handler(create_conv)
    app.add_handler(add_server_conv)
    app.add_handler(edit_conv)

    # Callback handlers
    app.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(list_subs, pattern="^list_subs$"))
    app.add_handler(CallbackQueryHandler(view_sub, pattern=r"^view_sub_"))
    app.add_handler(CallbackQueryHandler(del_server_list, pattern=r"^del_server_"))
    app.add_handler(CallbackQueryHandler(remove_server, pattern=r"^rm_srv_"))
    app.add_handler(CallbackQueryHandler(get_link, pattern=r"^get_link_"))
    app.add_handler(CallbackQueryHandler(sync_sub, pattern=r"^sync_sub_"))
    app.add_handler(CallbackQueryHandler(ping_sub_servers, pattern=r"^ping_sub_"))
    app.add_handler(CallbackQueryHandler(ping_all_subs, pattern="^ping_all$"))
    app.add_handler(CallbackQueryHandler(edit_sub, pattern=r"^edit_sub_"))
    app.add_handler(CallbackQueryHandler(confirm_delete_sub, pattern=r"^confirm_del_sub_"))
    app.add_handler(CallbackQueryHandler(do_delete_sub, pattern=r"^do_del_sub_"))

    logger.info("🚀 Bot started!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()