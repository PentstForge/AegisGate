#!/usr/bin/env python3
import json
import logging
import os
import requests
import time

log = logging.getLogger("dns_alerts")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_API = "https://api.telegram.org/bot{}/sendMessage"

ALERT_SEVERITY = {"critical": "🚨", "high": "⚠️", "medium": "🔶", "low": "ℹ️", "info": "ℹ️"}


def _get_settings():
    try:
        from .dns_db import get_setting
        return (get_setting("telegram_alerts_enabled", False),
                get_setting("telegram_chat_id", ""))
    except Exception:
        return (False, "")


def _send_telegram(chat_id, message):
    if not TELEGRAM_BOT_TOKEN:
        return False, "No TELEGRAM_BOT_TOKEN"
    url = TELEGRAM_API.format(TELEGRAM_BOT_TOKEN)
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=10)
        if r.status_code == 200:
            return True, "OK"
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, str(e)


def send_dns_alert(subject, severity="info", details=None):
    enabled, chat_id = _get_settings()
    if not enabled:
        return False, "Alerts disabled"
    target_chat = chat_id or TELEGRAM_CHAT_ID
    if not target_chat:
        return False, "No chat ID"
    icon = ALERT_SEVERITY.get(severity, "ℹ️")
    msg = f"{icon} <b>AegisDNS Alert</b>\n<b>{subject}</b>\n"
    if details:
        if isinstance(details, dict):
            for k, v in details.items():
                msg += f"<b>{k}:</b> {v}\n"
        elif isinstance(details, str):
            msg += details + "\n"
    msg += f"\n<i>Severity: {severity} | {time.strftime('%Y-%m-%d %H:%M:%S')}</i>"
    return _send_telegram(target_chat, msg)


def alert_new_unknown_client(ip, mac, hostname=None):
    details = {"IP": ip, "MAC": mac}
    if hostname:
        details["Hostname"] = hostname
    return send_dns_alert("New Unknown Client Detected", "medium", details)


def alert_dns_bypass_attempt(client_ip, domain=None):
    details = {"Client": client_ip}
    if domain:
        details["Domain"] = domain
    return send_dns_alert("DNS Bypass Attempt Blocked", "high", details)


def alert_doh_bypass_blocked(client_ip, domain=None):
    details = {"Client": client_ip}
    if domain:
        details["Domain"] = domain
    return send_dns_alert("DoH Bypass Blocked", "medium", details)


def alert_dns_service_error(service="dnsmasq", error=""):
    details = {"Service": service, "Error": error[:200]}
    return send_dns_alert("DNS Service Error", "critical", details)