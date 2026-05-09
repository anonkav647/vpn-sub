"""
Проверка пинга серверов из ключей.
Извлекает хост из VLESS/SS ключей и пингует.
"""

import asyncio
import re
import socket
import time
import urllib.parse
from typing import Optional

from config import format_country


def extract_host_from_key(key: str) -> Optional[str]:
    """Извлечь хост из VPN-ключа"""
    key = key.strip()

    # vless://uuid@host:port?params#name
    if key.startswith("vless://"):
        try:
            after_proto = key[8:]  # убираем vless://
            if "@" in after_proto:
                after_at = after_proto.split("@", 1)[1]
                host_port = after_at.split("?", 1)[0].split("#", 1)[0]
                host = host_port.rsplit(":", 1)[0]
                # Убираем [] для IPv6
                host = host.strip("[]")
                return host
        except (IndexError, ValueError):
            pass

    # ss://base64@host:port#name  или  ss://base64#name
    if key.startswith("ss://"):
        try:
            after_proto = key[5:]
            if "@" in after_proto:
                after_at = after_proto.split("@", 1)[1]
                host_port = after_at.split("#", 1)[0]
                host = host_port.rsplit(":", 1)[0]
                host = host.strip("[]")
                return host
            else:
                # ss://base64#name — декодировать base64
                import base64
                b64_part = after_proto.split("#", 1)[0]
                # Добавляем padding
                padding = 4 - len(b64_part) % 4
                if padding != 4:
                    b64_part += "=" * padding
                decoded = base64.b64decode(b64_part).decode("utf-8", errors="ignore")
                # method:pass@host:port
                if "@" in decoded:
                    after_at = decoded.split("@", 1)[1]
                    host = after_at.rsplit(":", 1)[0]
                    return host
        except (IndexError, ValueError, Exception):
            pass

    # trojan://pass@host:port?params#name
    if key.startswith("trojan://"):
        try:
            after_proto = key[9:]
            if "@" in after_proto:
                after_at = after_proto.split("@", 1)[1]
                host_port = after_at.split("?", 1)[0].split("#", 1)[0]
                host = host_port.rsplit(":", 1)[0]
                host = host.strip("[]")
                return host
        except (IndexError, ValueError):
            pass

    return None


async def tcp_ping(host: str, port: int = 443, timeout: float = 5.0) -> Optional[float]:
    """TCP ping — более надёжный чем ICMP (не нужны root права)"""
    try:
        start = time.time()
        # Резолвим DNS
        loop = asyncio.get_event_loop()
        addr_info = await loop.getaddrinfo(host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)

        if not addr_info:
            return None

        family, socktype, proto, canonname, sockaddr = addr_info[0]

        # Пробуем подключиться
        fut = asyncio.open_connection(sockaddr[0], sockaddr[1])
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        elapsed = (time.time() - start) * 1000  # мс

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        return round(elapsed, 1)

    except (asyncio.TimeoutError, OSError, ConnectionRefusedError, Exception):
        return None


async def ping_all_servers(servers: list) -> list:
    """Пропинговать все серверы и вернуть результаты"""
    results = []

    tasks = []
    for server in servers:
        host = extract_host_from_key(server["key"])
        tasks.append((server, host))

    for server, host in tasks:
        if host:
            ping_ms = await tcp_ping(host)
            results.append({
                "name": server["name"],
                "host": host,
                "protocol": server.get("protocol", "?"),
                "ping": ping_ms,
                "status": "✅" if ping_ms is not None else "❌"
            })
        else:
            results.append({
                "name": server["name"],
                "host": "N/A",
                "protocol": server.get("protocol", "?"),
                "ping": None,
                "status": "⚠️"
            })

    return results


def format_ping_results(results: list) -> str:
    """Форматировать результаты пинга"""
    if not results:
        return "📡 Нет серверов для проверки"

    lines = ["📡 <b>Результаты проверки серверов:</b>\n"]

    # Сортируем: сначала рабочие по пингу, потом нерабочие
    working = sorted([r for r in results if r["ping"] is not None], key=lambda x: x["ping"])
    failed = [r for r in results if r["ping"] is None]

    for r in working:
        flag_name = format_country(r["name"])
        ping = r["ping"]

        if ping < 100:
            bar = "🟢"
        elif ping < 200:
            bar = "🟡"
        elif ping < 500:
            bar = "🟠"
        else:
            bar = "🔴"

        lines.append(
            f"{r['status']} {flag_name}\n"
            f"   {bar} Пинг: <b>{ping}ms</b> | {r['protocol'].upper()}\n"
            f"   🌐 {r['host']}"
        )

    for r in failed:
        flag_name = format_country(r["name"])
        lines.append(
            f"{r['status']} {flag_name}\n"
            f"   🔴 Недоступен | {r['protocol'].upper()}\n"
            f"   🌐 {r['host']}"
        )

    total = len(results)
    online = len(working)
    lines.append(f"\n📊 Итого: {online}/{total} серверов онлайн")

    return "\n".join(lines)