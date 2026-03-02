from __future__ import annotations

import string
from pathlib import Path

MEMORY_FILE = Path("memory.json")
BASE_DIR = Path(__file__).resolve().parent
INDEX_FILE = BASE_DIR / "index.html"
SAMPLE_RATE = 16000
LISTEN_DURATION_SECONDS = 8
WAKE_DURATION_SECONDS = 4
MAX_HISTORY = 80
SUPPORTED_TONES = ("formal", "friendly", "sarcastic")
VERBOSE_LOGS = False
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8000
MAX_PORT_SCAN = 20

RETRY_PROMPTS = [
    "I can't hear you clearly. Please say it again.",
    "Could not catch that clearly. Ek baar phir bolo.",
    "I missed that. Please repeat.",
    "Audio unclear. Try once more.",
]

FALLBACK_PROMPTS = [
    "I did not map that command yet. Try rephrasing in simple words.",
    "Samjha nahi. You can ask for time, search, memory, or tasks.",
    "That request is unclear. Say help or ask what I can do.",
]

SLEEP_COMMANDS = {
    "bye",
    "sleep",
    "go to sleep",
    "stand by",
    "sleep mode",
    "sleep jarvis",
    "jarvis sleep",
    "go offline",
    "stand up",
}

WAKE_ONLY_COMMANDS = {
    "jarvis",
    "hey jarvis",
    "wake",
    "wake up",
    "wake up jarvis",
    "wake jarvis",
    "jarvis wake up",
    "jarvis wake",
    "resume",
    "i am back",
    "wakeup",
}

WAKE_COOLDOWN_SECONDS = 2.5

FACT_KEY_ALIASES = {
    "birth day": "birthday",
    "birth date": "birthday",
    "birthdate": "birthday",
    "date of birth": "birthday",
    "dob": "birthday",
    "bday": "birthday",
}

AUTO_MEMORY_FACT_KEYS = {
    "birthday",
}

VOICE_PREFIX_CORRECTIONS = {
    "earth": "search",
    "torch": "search",
    "church": "search",
    "sirch": "search",
    "serch": "search",
    "seach": "search",
    "sarch": "search",
}

COMMAND_HINT_WORDS = {
    "search",
    "open",
    "play",
    "google",
    "youtube",
    "amazon",
    "wikipedia",
    "remember",
    "forget",
    "wake",
    "sleep",
    "time",
    "date",
    "calculate",
    "who",
    "what",
    "tell",
}

IMPLICIT_SEARCH_BLOCKLIST = {
    "hello",
    "hi",
    "hey",
    "thanks",
    "thank",
    "good",
    "morning",
    "afternoon",
    "evening",
    "jarvis",
    "wake",
    "sleep",
    "exit",
    "quit",
    "my",
    "i",
    "im",
    "i'm",
    "set",
    "switch",
    "change",
    "use",
    "remember",
    "forget",
    "send",
    "email",
    "mail",
    "message",
    "whatsapp",
    "contact",
    "add",
    "todo",
    "complete",
    "done",
    "finish",
    "help",
    "who",
    "what",
    "when",
    "why",
    "how",
    "tell",
    "open",
    "search",
    "google",
    "youtube",
    "amazon",
    "play",
    "calculate",
}

EXIT_COMMANDS = {
    "exit",
    "quit",
    "shutdown",
    "stop jarvis",
    "exit jarvis",
    "close jarvis",
    "terminate",
    "turn off",
}

CONFIRM_WORDS = {"yes", "confirm", "do it", "proceed", "go ahead", "send it"}
CANCEL_WORDS = {"no", "cancel", "stop", "abort", "never mind"}
FILE_SCAN_EXCLUDE_DIRS = {"venv", ".venv", "__pycache__", ".git", ".idea", ".vscode"}
FILE_SCAN_TEXT_EXTENSIONS = {
    ".py",
    ".txt",
    ".md",
    ".json",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
}
MAX_FILE_SCAN_RESULTS = 6
MAX_FILE_SCAN_FILES = 1200
MAX_GLOBAL_FILE_SCAN_FILES = 1200000
MAX_FILE_READ_CHARS = 6000
MAX_MACRO_ACTIONS = 8
WINDOWS_DRIVE_LETTERS = tuple(string.ascii_uppercase)
GLOBAL_FILE_SCAN_EXCLUDE_DIRS = {
    "$recycle.bin",
    "$winreagent",
    "$windows.~bt",
    "$windows.~ws",
    "programdata",
    "system volume information",
    "windows",
    "winsxs",
}
