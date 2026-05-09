import json
import os
import uuid
from datetime import datetime
from typing import Optional

DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "subscriptions.json")


def _ensure_data_dir():
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({"subscriptions": {}}, f, ensure_ascii=False, indent=2)


def _load() -> dict:
    _ensure_data_dir()
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict):
    _ensure_data_dir()
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def create_subscription(name: str, description: str, expire_date: str) -> dict:
    """Создать новую подписку"""
    data = _load()
    sub_id = str(uuid.uuid4())[:8]

    sub = {
        "id": sub_id,
        "name": name,
        "description": description,
        "expire_date": expire_date,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "servers": [],
        "active": True,
        "github_filename": f"{sub_id}.txt"
    }

    data["subscriptions"][sub_id] = sub
    _save(data)
    return sub


def get_subscription(sub_id: str) -> Optional[dict]:
    data = _load()
    return data["subscriptions"].get(sub_id)


def get_all_subscriptions() -> dict:
    data = _load()
    return data["subscriptions"]


def update_subscription(sub_id: str, **kwargs) -> Optional[dict]:
    data = _load()
    if sub_id not in data["subscriptions"]:
        return None

    for key, value in kwargs.items():
        if key in data["subscriptions"][sub_id]:
            data["subscriptions"][sub_id][key] = value

    _save(data)
    return data["subscriptions"][sub_id]


def delete_subscription(sub_id: str) -> bool:
    data = _load()
    if sub_id in data["subscriptions"]:
        del data["subscriptions"][sub_id]
        _save(data)
        return True
    return False


def add_server_to_subscription(sub_id: str, server_name: str, server_key: str, protocol: str) -> bool:
    """Добавить сервер (ключ) в подписку"""
    data = _load()
    if sub_id not in data["subscriptions"]:
        return False

    server = {
        "id": str(uuid.uuid4())[:6],
        "name": server_name,
        "key": server_key,
        "protocol": protocol,
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    data["subscriptions"][sub_id]["servers"].append(server)
    _save(data)
    return True


def remove_server_from_subscription(sub_id: str, server_id: str) -> bool:
    data = _load()
    if sub_id not in data["subscriptions"]:
        return False

    servers = data["subscriptions"][sub_id]["servers"]
    data["subscriptions"][sub_id]["servers"] = [s for s in servers if s["id"] != server_id]
    _save(data)
    return True


def get_servers_of_subscription(sub_id: str) -> list:
    data = _load()
    if sub_id not in data["subscriptions"]:
        return []
    return data["subscriptions"][sub_id]["servers"]


def is_subscription_expired(sub_id: str) -> bool:
    sub = get_subscription(sub_id)
    if not sub:
        return True
    try:
        expire = datetime.strptime(sub["expire_date"], "%Y-%m-%d")
        return datetime.now() > expire
    except ValueError:
        return False