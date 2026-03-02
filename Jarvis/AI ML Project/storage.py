from __future__ import annotations

import json
import re
import socket
from pathlib import Path
from typing import Any, Dict, List

from constants import (
    FACT_KEY_ALIASES,
    MAX_MACRO_ACTIONS,
    MAX_PORT_SCAN,
    MAX_HISTORY,
    SUPPORTED_TONES,
)


def normalize_fact_key(key: str) -> str:
    clean_key = re.sub(r"[^\w\s]", " ", key.strip().lower())
    clean_key = re.sub(r"\s+", " ", clean_key).strip()
    clean_key = re.sub(r"^(?:my|the)\s+", "", clean_key)
    return FACT_KEY_ALIASES.get(clean_key, clean_key)


def default_memory() -> Dict[str, Any]:
    return {
        "user_profile": {"name": "Mayuri"},
        "facts": {},
        "preferences": {"tone": "friendly", "language_mode": "hinglish"},
        "personality": {"sass_level": 0.55, "traits": ["helpful", "witty", "loyal"]},
        "session": {
            "last_topic": "",
            "last_interaction_date": "",
            "last_greeting_date": "",
            "last_mood": "neutral",
            "recent_topics": [],
        },
        "tasks": [],
        "macros": {
            "work mode": [
                "open chrome",
                "open notepad",
                "show tasks",
                "daily briefing",
            ],
            "focus mode": [
                "mute volume",
                "show tasks",
            ],
        },
        "people": {},
        "contacts": {},
        "projects": {},
        "notes": [],
        "analytics": {
            "total_commands": 0,
            "commands_by_day": {},
            "intent_counts": {},
            "reply_tags": {},
        },
        "conversation_history": [],
    }


def normalize_memory(raw: Any) -> Dict[str, Any]:
    memory = default_memory()
    if not isinstance(raw, dict):
        return memory

    for section in (
        "user_profile",
        "facts",
        "preferences",
        "personality",
        "session",
        "macros",
        "people",
        "contacts",
        "projects",
        "analytics",
    ):
        if isinstance(raw.get(section), dict):
            memory[section].update(raw[section])

    if isinstance(raw.get("conversation_history"), list):
        memory["conversation_history"] = raw["conversation_history"][-MAX_HISTORY:]

    tasks: List[Dict[str, Any]] = []
    if isinstance(raw.get("tasks"), list):
        for item in raw["tasks"]:
            if isinstance(item, dict) and "text" in item:
                tasks.append(
                    {
                        "text": str(item["text"]),
                        "done": bool(item.get("done", False)),
                        "created_at": str(item.get("created_at", "")),
                    }
                )
            elif isinstance(item, str):
                tasks.append(
                    {
                        "text": item.strip(),
                        "done": False,
                        "created_at": "",
                    }
                )
    memory["tasks"] = tasks

    notes: List[Dict[str, str]] = []
    if isinstance(raw.get("notes"), list):
        for item in raw["notes"]:
            if isinstance(item, dict):
                text = str(item.get("text", "")).strip()
                if text:
                    notes.append(
                        {
                            "text": text,
                            "created_at": str(item.get("created_at", "")),
                        }
                    )
            elif isinstance(item, str):
                text = item.strip()
                if text:
                    notes.append(
                        {
                            "text": text,
                            "created_at": "",
                        }
                    )
    memory["notes"] = notes[-200:]

    clean_people: Dict[str, str] = {}
    if isinstance(memory.get("people"), dict):
        for key, value in memory["people"].items():
            name = " ".join(str(key).lower().split())
            info = str(value.get("summary", "") if isinstance(value, dict) else value).strip()
            if name and info:
                clean_people[name] = info
    memory["people"] = clean_people

    clean_contacts: Dict[str, str] = {}
    if isinstance(memory.get("contacts"), dict):
        for key, value in memory["contacts"].items():
            name = " ".join(str(key).lower().split())
            raw_phone = str(value.get("phone", "") if isinstance(value, dict) else value).strip()
            phone = re.sub(r"[^\d+]", "", raw_phone)
            if name and re.fullmatch(r"\+?\d{8,15}", phone):
                clean_contacts[name] = phone
    memory["contacts"] = clean_contacts

    clean_projects: Dict[str, str] = {}
    if isinstance(memory.get("projects"), dict):
        for key, value in memory["projects"].items():
            project = " ".join(str(key).lower().split())
            status = str(value.get("status", "") if isinstance(value, dict) else value).strip()
            if project and status:
                clean_projects[project] = status
    memory["projects"] = clean_projects

    clean_macros: Dict[str, List[str]] = {}
    if isinstance(memory.get("macros"), dict):
        for key, value in memory["macros"].items():
            macro_name = " ".join(str(key).lower().split())
            if not macro_name:
                continue
            actions: List[str] = []
            if isinstance(value, list):
                actions = [str(item).strip() for item in value if str(item).strip()]
            elif isinstance(value, str):
                actions = [part.strip() for part in re.split(r"\s*;\s*", value) if part.strip()]
            if actions:
                clean_macros[macro_name] = actions[:MAX_MACRO_ACTIONS]
    if clean_macros:
        memory["macros"] = clean_macros

    normalized_facts: Dict[str, str] = {}
    for key, value in memory.get("facts", {}).items():
        normalized_key = normalize_fact_key(str(key))
        if not normalized_key:
            continue
        clean_value = str(value).strip()
        if clean_value:
            normalized_facts[normalized_key] = clean_value
    memory["facts"] = normalized_facts

    known_top_keys = set(memory.keys())
    for key, value in raw.items():
        if key in known_top_keys:
            continue
        if isinstance(value, (str, int, float, bool)):
            normalized_key = normalize_fact_key(str(key))
            if normalized_key:
                memory["facts"][normalized_key] = str(value).strip()

    tone = str(memory["preferences"].get("tone", "friendly")).lower().strip()
    memory["preferences"]["tone"] = tone if tone in SUPPORTED_TONES else "friendly"

    if memory["preferences"].get("language_mode") not in {"hinglish", "english"}:
        memory["preferences"]["language_mode"] = "hinglish"

    if not isinstance(memory["session"].get("recent_topics"), list):
        memory["session"]["recent_topics"] = []

    sass_level = memory["personality"].get("sass_level", 0.55)
    if not isinstance(sass_level, (int, float)):
        sass_level = 0.55
    memory["personality"]["sass_level"] = max(0.0, min(1.0, float(sass_level)))

    if not memory["user_profile"].get("name"):
        memory["user_profile"]["name"] = "Mayuri"

    analytics = memory.get("analytics", {})
    if not isinstance(analytics, dict):
        analytics = {}
    total_commands = analytics.get("total_commands", 0)
    if not isinstance(total_commands, int):
        total_commands = 0
    analytics["total_commands"] = max(0, total_commands)

    for bucket in ("commands_by_day", "intent_counts", "reply_tags"):
        source = analytics.get(bucket, {})
        clean_bucket: Dict[str, int] = {}
        if isinstance(source, dict):
            for key, value in source.items():
                clean_key = str(key).strip().lower()
                if not clean_key:
                    continue
                if isinstance(value, int) and value > 0:
                    clean_bucket[clean_key] = value
        analytics[bucket] = clean_bucket
    memory["analytics"] = analytics

    return memory


def load_memory(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return default_memory()
    try:
        with path.open("r", encoding="utf-8") as file:
            raw = json.load(file)
    except (OSError, json.JSONDecodeError):
        return default_memory()
    return normalize_memory(raw)


def save_memory(path: Path, memory: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(memory, file, indent=4, ensure_ascii=True)


def safe_eval_expression(expression: str) -> float:
    cleaned = expression.strip()
    if not re.fullmatch(r"[0-9+\-*/().%\s]+", cleaned):
        raise ValueError("Only numeric expressions are allowed.")
    if len(cleaned) > 60:
        raise ValueError("Expression too long.")
    return eval(cleaned, {"__builtins__": {}}, {})


def find_available_port(host: str, preferred_port: int, max_scan: int = MAX_PORT_SCAN) -> int:
    for port in range(preferred_port, preferred_port + max_scan):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"No available port found in range {preferred_port}-{preferred_port + max_scan - 1}.")
