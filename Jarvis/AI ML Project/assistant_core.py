from __future__ import annotations

import ctypes
import datetime as dt
import difflib
import os
import random
import re
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import quote_plus

import numpy as np
import pyttsx3
import sounddevice as sd
import speech_recognition as sr
import wikipedia

from constants import (
    AUTO_MEMORY_FACT_KEYS,
    BASE_DIR,
    CANCEL_WORDS,
    COMMAND_HINT_WORDS,
    CONFIRM_WORDS,
    EXIT_COMMANDS,
    FALLBACK_PROMPTS,
    FILE_SCAN_EXCLUDE_DIRS,
    FILE_SCAN_TEXT_EXTENSIONS,
    GLOBAL_FILE_SCAN_EXCLUDE_DIRS,
    IMPLICIT_SEARCH_BLOCKLIST,
    LISTEN_DURATION_SECONDS,
    MAX_FILE_READ_CHARS,
    MAX_FILE_SCAN_FILES,
    MAX_FILE_SCAN_RESULTS,
    MAX_GLOBAL_FILE_SCAN_FILES,
    MAX_HISTORY,
    MAX_MACRO_ACTIONS,
    RETRY_PROMPTS,
    SAMPLE_RATE,
    SLEEP_COMMANDS,
    SUPPORTED_TONES,
    VERBOSE_LOGS,
    VOICE_PREFIX_CORRECTIONS,
    WAKE_COOLDOWN_SECONDS,
    WAKE_DURATION_SECONDS,
    WAKE_ONLY_COMMANDS,
    WINDOWS_DRIVE_LETTERS,
)
from storage import default_memory, load_memory, normalize_fact_key, safe_eval_expression, save_memory


class JarvisAssistant:
    def __init__(self, memory_path: Path) -> None:
        self.memory_path = memory_path
        self.memory = load_memory(memory_path)
        self.memory_lock = threading.Lock()
        self.tts_lock = threading.Lock()
        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = True
        self.recognizer.energy_threshold = 220
        self.recognizer.pause_threshold = 0.8
        self.recognizer.non_speaking_duration = 0.45
        self.recognizer.operation_timeout = 8
        self.thread_state = threading.local()
        self.thread_state.voice_output = True
        self.awake = True
        self.listening = True
        self.sleep_started_at = 0.0
        self.pending_command = ""
        self.pending_wake_response = ""
        self.pending_confirmation: Dict[str, Any] = {}
        self.active_macro_stack: List[str] = []
        self.command_lock = threading.Lock()
        self.last_response_text = ""

    @property
    def tone(self) -> str:
        tone = str(self.memory["preferences"].get("tone", "friendly")).lower()
        return tone if tone in SUPPORTED_TONES else "friendly"

    @property
    def sass_level(self) -> float:
        value = self.memory["personality"].get("sass_level", 0.55)
        if isinstance(value, (int, float)):
            return max(0.0, min(1.0, float(value)))
        return 0.55

    def persist_memory(self) -> None:
        with self.memory_lock:
            save_memory(self.memory_path, self.memory)

    def log(self, message: str) -> None:
        if VERBOSE_LOGS:
            print(message)

    def is_voice_output_enabled(self) -> bool:
        return bool(getattr(self.thread_state, "voice_output", True))

    def speak(self, text: str) -> None:
        with self.tts_lock:
            engine = pyttsx3.init()
            voices = engine.getProperty("voices") or []
            if len(voices) >= 3:
                engine.setProperty("voice", voices[2].id)
            elif voices:
                engine.setProperty("voice", voices[0].id)
            engine.setProperty("rate", 155)
            engine.setProperty("volume", 1.0)
            engine.say(text)
            engine.runAndWait()
            engine.stop()
        time.sleep(0.2)

    def speak_retry_prompt(self) -> None:
        primary = RETRY_PROMPTS[0]
        self.last_response_text = primary
        self.speak(primary)

    def remember_turn(self, role: str, text: str, tag: str = "general") -> None:
        event = {
            "role": role,
            "text": text,
            "tag": tag,
            "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        }
        with self.memory_lock:
            history = self.memory.get("conversation_history", [])
            history.append(event)
            self.memory["conversation_history"] = history[-MAX_HISTORY:]
            self.memory["session"]["last_interaction_date"] = dt.date.today().isoformat()

            analytics = self.memory.setdefault("analytics", default_memory()["analytics"])
            today_key = dt.date.today().isoformat()
            if role == "user":
                analytics["total_commands"] = int(analytics.get("total_commands", 0)) + 1
                by_day = analytics.setdefault("commands_by_day", {})
                by_day[today_key] = int(by_day.get(today_key, 0)) + 1
                intent_key = self.classify_intent_label(text)
                intent_counts = analytics.setdefault("intent_counts", {})
                intent_counts[intent_key] = int(intent_counts.get(intent_key, 0)) + 1
            elif role == "assistant":
                reply_tags = analytics.setdefault("reply_tags", {})
                tag_key = str(tag).strip().lower() or "general"
                reply_tags[tag_key] = int(reply_tags.get(tag_key, 0)) + 1
        self.persist_memory()

    def remember_topic(self, topic: str) -> None:
        clean_topic = topic.strip()
        if not clean_topic:
            return
        with self.memory_lock:
            self.memory["session"]["last_topic"] = clean_topic
            topics = self.memory["session"].get("recent_topics", [])
            topics = [clean_topic] + [t for t in topics if t != clean_topic]
            self.memory["session"]["recent_topics"] = topics[:8]
        self.persist_memory()

    @staticmethod
    def classify_intent_label(text: str) -> str:
        query = str(text).strip().lower()
        if not query:
            return "unknown"

        rules = [
            ("sleep_wake", r"\b(wake|sleep|stand by|resume|i am back)\b"),
            ("exit", r"\b(exit|quit|shutdown|terminate|turn off)\b"),
            ("tone", r"\b(tone|formal|friendly|sarcastic)\b"),
            ("personality", r"\b(sass|traits|personality)\b"),
            ("memory", r"\b(remember|forget|who am i|what is my|my name is)\b"),
            ("task", r"\b(task|todo|complete task|show tasks|my tasks)\b"),
            ("briefing", r"\b(brief|daily briefing|morning briefing)\b"),
            ("macro", r"\b(macro|work mode|focus mode)\b"),
            ("message", r"\b(email|whatsapp|mail|message)\b"),
            ("file", r"\b(file|files|summarize file|open file)\b"),
            ("system", r"\b(open|volume|clipboard|lock|task manager|powershell|cmd)\b"),
            ("search", r"\b(search|google|youtube|amazon)\b"),
            ("wiki", r"\b(who is|what is|tell me about|define)\b"),
            ("time_date", r"\b(time|date|day)\b"),
            ("calc", r"\b(calculate|[0-9]+\s*[\+\-\*\/%])\b"),
        ]
        for label, pattern in rules:
            if re.search(pattern, query):
                return label
        return "general"

    @staticmethod
    def top_counts(bucket: Dict[str, int], limit: int = 4) -> List[Dict[str, Any]]:
        ordered = sorted(bucket.items(), key=lambda item: item[1], reverse=True)
        return [{"name": name, "count": count} for name, count in ordered[:limit]]

    def get_analytics_snapshot(self) -> Dict[str, Any]:
        with self.memory_lock:
            analytics = self.memory.get("analytics", {})
            total = int(analytics.get("total_commands", 0))
            by_day = dict(analytics.get("commands_by_day", {}))
            intents = dict(analytics.get("intent_counts", {}))
            reply_tags = dict(analytics.get("reply_tags", {}))

        today_key = dt.date.today().isoformat()
        commands_today = int(by_day.get(today_key, 0))
        active_days = len([day for day, count in by_day.items() if int(count) > 0])

        return {
            "total_commands": total,
            "commands_today": commands_today,
            "active_days": active_days,
            "top_intents": self.top_counts(intents, limit=5),
            "top_reply_tags": self.top_counts(reply_tags, limit=5),
        }

    def style_reply(self, message: str, context: str = "general") -> str:
        text = message.strip()
        tone = self.tone

        if context in {"wiki", "history", "abilities"} and len(text) > 130:
            if tone == "formal":
                return f"Certainly. {text}"
            if tone == "sarcastic":
                return f"Fine. {text}"
            return f"Bilkul. {text}"

        prefixes = {
            "formal": ["Certainly,", "Ji,", "Understood,"],
            "friendly": ["Bilkul,", "Haan,", "Theek hai,"],
            "sarcastic": ["Haan haan,", "Of course,", "Naturally,"],
        }
        suffixes = {
            "friendly": ["Aur kuch?", "Chalo, next command?", "Ready for the next one."],
            "sarcastic": [
                "Efficient enough for you?",
                "Another masterpiece command incoming?",
                "I live to serve your dramatic workflow.",
            ],
        }

        final = f"{random.choice(prefixes[tone])} {text}"
        if tone in suffixes:
            chance = 0.35 if tone == "friendly" else self.sass_level
            if random.random() < chance:
                final = f"{final} {random.choice(suffixes[tone])}"
        return final

    def respond(self, message: str, *, styled: bool = True, tag: str = "general") -> None:
        final = self.style_reply(message, context=tag) if styled else message
        self.last_response_text = final
        if self.is_voice_output_enabled():
            self.speak(final)
        self.remember_turn("assistant", final, tag=tag)

    def greet_user(self) -> None:
        now = dt.datetime.now()
        today = now.date().isoformat()
        name = self.memory["user_profile"].get("name", "Mayuri")
        hour = now.hour

        if hour < 12:
            time_greeting = "Good morning"
        elif hour < 18:
            time_greeting = "Good afternoon"
        else:
            time_greeting = "Good evening"

        last_seen_str = str(self.memory["session"].get("last_interaction_date", ""))
        last_topic = str(self.memory["session"].get("last_topic", ""))
        last_greet = str(self.memory["session"].get("last_greeting_date", ""))

        if last_greet == today:
            greeting = f"Welcome back {name}. I am ready in {self.tone} mode."
        else:
            greeting = f"{time_greeting} {name}. Jarvis online in {self.tone} mode."
            if last_seen_str:
                try:
                    last_seen = dt.date.fromisoformat(last_seen_str)
                    gap = (now.date() - last_seen).days
                    if gap == 1:
                        greeting += " We spoke yesterday."
                    elif gap > 1:
                        greeting += f" It has been {gap} days since our last chat."
                except ValueError:
                    pass
            if last_topic:
                greeting += f" Last remembered topic: {last_topic}."

        self.respond(greeting, styled=True, tag="greeting")

        with self.memory_lock:
            self.memory["session"]["last_greeting_date"] = today
        self.persist_memory()

    @staticmethod
    def preprocess_recording(recording: np.ndarray) -> np.ndarray:
        if recording.size == 0:
            return np.array([], dtype=np.int16)

        samples = np.squeeze(recording)
        if samples.ndim != 1:
            samples = samples.reshape(-1)
        samples = samples.astype(np.int16, copy=False)

        # Trim long silence at the edges so recognition focuses on spoken content.
        abs_samples = np.abs(samples.astype(np.int32))
        speech_points = np.where(abs_samples > 260)[0]
        if speech_points.size:
            pad_start = int(0.12 * SAMPLE_RATE)
            pad_end = int(0.20 * SAMPLE_RATE)
            start = max(0, int(speech_points[0]) - pad_start)
            end = min(len(samples), int(speech_points[-1]) + pad_end)
            samples = samples[start:end]

        peak = int(np.max(np.abs(samples.astype(np.int32)))) if samples.size else 0
        if 0 < peak < 12000:
            gain = min(4.0, 12000.0 / peak)
            boosted = np.clip(samples.astype(np.float32) * gain, -32768, 32767)
            samples = boosted.astype(np.int16)

        return samples

    @staticmethod
    def score_transcript(text: str) -> float:
        words = re.findall(r"[a-z0-9']+", text.lower())
        if not words:
            return -1.0
        score = float(len(words) * 2)
        if len(words) >= 3:
            score += 2.0
        if words[0] in COMMAND_HINT_WORDS:
            score += 3.0
        if any(word in COMMAND_HINT_WORDS for word in words):
            score += 1.5
        score += min(len(text), 40) / 20.0
        return score

    def recognize_candidates(self, audio_data: sr.AudioData, language: str) -> List[str]:
        result = self.recognizer.recognize_google(audio_data, language=language, show_all=True)
        if isinstance(result, str):
            clean = result.strip()
            return [clean] if clean else []
        if not isinstance(result, dict):
            return []
        candidates: List[str] = []
        for item in result.get("alternative", []):
            if not isinstance(item, dict):
                continue
            transcript = str(item.get("transcript", "")).strip()
            if transcript:
                candidates.append(transcript)
        return candidates

    def capture_speech(self, duration_seconds: int) -> str:
        try:
            recording = sd.rec(
                int(duration_seconds * SAMPLE_RATE),
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
            )
            sd.wait()
        except Exception as err:
            self.log(f"Audio capture error: {err}")
            return ""

        prepared = self.preprocess_recording(recording)
        if prepared.size < int(0.18 * SAMPLE_RATE):
            return ""

        audio_data = sr.AudioData(prepared.tobytes(), SAMPLE_RATE, 2)
        candidates: List[str] = []
        try:
            for language in ("en-IN", "en-US"):
                try:
                    candidates.extend(self.recognize_candidates(audio_data, language))
                except sr.UnknownValueError:
                    continue
        except sr.UnknownValueError:
            return ""
        except sr.RequestError:
            self.respond("Speech recognition service is unavailable right now.", styled=False, tag="error")
            return ""

        unique_candidates = []
        seen = set()
        for text in candidates:
            clean = " ".join(text.split()).strip()
            key = clean.lower()
            if clean and key not in seen:
                seen.add(key)
                unique_candidates.append(clean)

        if not unique_candidates:
            return ""

        best_text = max(unique_candidates, key=self.score_transcript)
        return best_text.strip()

    def take_command(self) -> str:
        print("Listening...")
        self.speak("Listening.")
        query = self.capture_speech(LISTEN_DURATION_SECONDS)
        if query:
            print(f"You: {query}")
        return query.lower().strip()

    def wake_word_listener(self) -> None:
        while self.listening:
            if self.awake:
                time.sleep(0.9)
                continue

            if self.sleep_started_at and (time.time() - self.sleep_started_at) < WAKE_COOLDOWN_SECONDS:
                time.sleep(0.2)
                continue

            heard_text = self.capture_speech(WAKE_DURATION_SECONDS).lower()
            if self.is_wake_phrase(heard_text):
                remainder = self.strip_wake_prefix(heard_text)
                self.awake = True
                self.sleep_started_at = 0.0
                if remainder and remainder not in WAKE_ONLY_COMMANDS:
                    self.pending_command = remainder
                    self.pending_wake_response = "I am awake now. Executing your command."
                else:
                    self.pending_wake_response = "I am awake now. Listening."

    def set_tone(self, tone: str) -> None:
        with self.memory_lock:
            self.memory["preferences"]["tone"] = tone
        self.persist_memory()

    @staticmethod
    def is_wake_phrase(text: str) -> bool:
        clean_text = re.sub(r"[^\w\s]", " ", text.strip().lower())
        clean_text = re.sub(r"\s+", " ", clean_text).strip()
        if not clean_text:
            return False
        if clean_text in WAKE_ONLY_COMMANDS:
            return True
        if clean_text.startswith("jarvis ") or clean_text.startswith("hey jarvis "):
            return True
        if clean_text == "wake" or clean_text.startswith("wake "):
            return True
        if clean_text.startswith("wake up"):
            return True
        if clean_text.startswith("wakeup"):
            return True
        return False

    @staticmethod
    def strip_wake_prefix(text: str) -> str:
        clean_text = text.strip().lower()
        patterns = (
            r"^(?:hey\s+)?jarvis[\s,]+",
            r"^wake(?:\s+up)?(?:\s+jarvis)?[\s,]*",
            r"^wakeup(?:\s+jarvis)?[\s,]*",
            r"^resume[\s,]*",
            r"^i am back[\s,]*",
        )
        output = clean_text
        for pattern in patterns:
            output = re.sub(pattern, "", output).strip()
        return output

    @staticmethod
    def repair_common_voice_transcript(query: str) -> str:
        words = query.strip().lower().split()
        if not words:
            return ""
        replacement = VOICE_PREFIX_CORRECTIONS.get(words[0])
        if not replacement:
            return " ".join(words)
        words[0] = replacement
        return " ".join(words)

    @staticmethod
    def should_implicit_voice_search(query: str) -> bool:
        words = query.strip().lower().split()
        if len(words) < 2:
            return False
        first = words[0]
        if first in IMPLICIT_SEARCH_BLOCKLIST:
            return False
        if re.search(r"\b(?:https?://|www\.)", query):
            return False
        return True

    @staticmethod
    def is_sleep_phrase(text: str) -> bool:
        clean_text = text.strip().lower()
        if clean_text in SLEEP_COMMANDS:
            return True
        return bool(
            re.search(r"\b(?:go to|go|enter)?\s*sleep(?: mode)?\b", clean_text)
            or re.search(r"\bstand\s*(?:by|bye|up)\b", clean_text)
            or re.search(r"\bstandby\b", clean_text)
            or re.search(r"\bgo offline\b", clean_text)
        )

    @staticmethod
    def is_exit_phrase(text: str) -> bool:
        clean_text = text.strip().lower()
        if clean_text in EXIT_COMMANDS:
            return True
        return bool(
            re.search(r"\b(?:exit|quit|shutdown|terminate)\b", clean_text)
            or re.search(r"\b(?:stop|close|turn off)\s+jarvis\b", clean_text)
        )

    def build_conversation_summary(self, limit: int = 5) -> str:
        history = self.memory.get("conversation_history", [])
        user_turns = [event["text"] for event in history if event.get("role") == "user"]
        if not user_turns:
            return "No conversation history yet. We just started."

        recent = user_turns[-limit:]
        if len(recent) == 1:
            return f"You last said: {recent[0]}"
        packed = " | ".join(recent)
        return f"Recent conversation snapshot: {packed}"

    def build_daily_briefing(self) -> str:
        now = dt.datetime.now()
        today_label = now.strftime("%A, %d %B %Y")
        with self.memory_lock:
            tasks = list(self.memory.get("tasks", []))
            recent_topics = list(self.memory.get("session", {}).get("recent_topics", []))
            mood = str(self.memory.get("session", {}).get("last_mood", "neutral"))
            notes_count = len(self.memory.get("notes", []))
            projects_count = len(self.memory.get("projects", {}))

        pending_tasks = [task.get("text", "").strip() for task in tasks if not task.get("done")]
        pending_preview = pending_tasks[:3]
        topic_preview = recent_topics[:3]

        parts = [f"Daily briefing for {today_label}."]
        parts.append(f"Current tone mode is {self.tone}. Mood marker is {mood}.")
        parts.append(f"You have {len(pending_tasks)} pending tasks and {projects_count} tracked projects.")
        if pending_preview:
            parts.append("Top pending tasks: " + " | ".join(pending_preview) + ".")
        if topic_preview:
            parts.append("Recent topics: " + " | ".join(topic_preview) + ".")
        parts.append(f"You also have {notes_count} saved notes.")
        return " ".join(parts)

    def split_macro_actions(self, actions_blob: str) -> List[str]:
        primary_split = re.split(r"\s*(?:;|,\s*then\s+)\s*", actions_blob.strip(), flags=re.IGNORECASE)
        if len(primary_split) <= 1:
            primary_split = [part.strip() for part in actions_blob.split(",")]
        actions = [action.strip() for action in primary_split if action.strip()]
        return actions[:MAX_MACRO_ACTIONS]

    @staticmethod
    def normalize_macro_name(raw_name: str) -> str:
        return " ".join(re.sub(r"[^\w\s-]", " ", raw_name.strip().lower()).split())

    def handle_confirmation_actions(self, query: str) -> bool:
        if not self.pending_confirmation:
            return False

        clean_query = query.strip().lower()
        if clean_query in {"pending action", "what is pending", "show pending action"}:
            action_name = str(self.pending_confirmation.get("action", "unknown action"))
            self.respond(f"Pending action: {action_name}. Say confirm or cancel.", styled=False, tag="confirm")
            return True

        if clean_query in CONFIRM_WORDS or clean_query.startswith("confirm"):
            pending = dict(self.pending_confirmation)
            self.pending_confirmation = {}
            action = str(pending.get("action", "")).strip().lower()

            if action == "open_url":
                url = str(pending.get("url", "")).strip()
                if not url:
                    self.respond("Pending action had no target URL.", styled=False, tag="confirm")
                    return True
                webbrowser.open(url)
                success = str(
                    pending.get(
                        "success_message",
                        "Confirmed. Draft opened. Please review and send manually.",
                    )
                )
                self.respond(success, styled=False, tag="confirm")
                return True

            if action == "lock_workstation":
                os.system("rundll32.exe user32.dll,LockWorkStation")
                self.respond("Locking workstation now.", styled=False, tag="confirm")
                return True

            self.respond("Confirmed.", styled=False, tag="confirm")
            return True

        if clean_query in CANCEL_WORDS or clean_query.startswith("cancel"):
            self.pending_confirmation = {}
            self.respond("Cancelled the pending action.", styled=False, tag="confirm")
            return True

        return False

    def resolve_contact_phone(self, target: str) -> tuple[str, str]:
        clean_target = " ".join(target.lower().split())
        contacts = self.memory.get("contacts", {})
        if not isinstance(contacts, dict) or not clean_target:
            return "", ""

        exact_phone = str(contacts.get(clean_target, "")).strip()
        if re.fullmatch(r"\+?\d{8,15}", exact_phone):
            return clean_target, exact_phone

        # Try partial match for cases like first-name only requests.
        for name, value in contacts.items():
            norm_name = " ".join(str(name).lower().split())
            phone = str(value).strip()
            if not re.fullmatch(r"\+?\d{8,15}", phone):
                continue
            if clean_target in norm_name or norm_name in clean_target:
                return norm_name, phone

        # Fuzzy match for small spelling differences: rajnish vs rajneesh.
        normalized_names = [" ".join(str(name).lower().split()) for name in contacts.keys()]
        best = difflib.get_close_matches(clean_target, normalized_names, n=1, cutoff=0.68)
        if not best:
            return "", ""
        best_name = best[0]
        best_phone = str(contacts.get(best_name, "")).strip()
        if not re.fullmatch(r"\+?\d{8,15}", best_phone):
            return "", ""
        return best_name, best_phone

    def handle_message_actions(self, query: str) -> bool:
        email_full = re.search(
            r"^(?:draft|send)?\s*email(?:\s+to)?\s+([a-z0-9_.+\-]+@[a-z0-9.\-]+\.[a-z]{2,})\s+subject\s+(.+?)\s+(?:body|message)\s+(.+)$",
            query,
        )
        email_body_only = re.search(
            r"^(?:draft|send)?\s*email(?:\s+to)?\s+([a-z0-9_.+\-]+@[a-z0-9.\-]+\.[a-z]{2,})\s+(?:body|message)\s+(.+)$",
            query,
        )
        if email_full or email_body_only:
            target = ""
            subject = "Message from Jarvis"
            body = ""
            if email_full:
                target = email_full.group(1).strip()
                subject = email_full.group(2).strip()
                body = email_full.group(3).strip()
            elif email_body_only:
                target = email_body_only.group(1).strip()
                body = email_body_only.group(2).strip()

            if not target or not body:
                self.respond("Please provide an email address and message body.", styled=False, tag="message")
                return True

            mailto_url = f"mailto:{target}?subject={quote_plus(subject)}&body={quote_plus(body)}"
            self.pending_confirmation = {
                "action": "open_url",
                "url": mailto_url,
                "success_message": f"Email draft opened for {target}. Review and send manually.",
            }
            self.respond(
                f"Prepared email draft to {target}. Say confirm to open draft or cancel to discard.",
                styled=False,
                tag="message",
            )
            return True

        whatsapp_match = re.search(
            r"^(?:send\s+)?(?:whatsapp|whats\s*app)(?:\s+to)?\s+(.+)$",
            query,
        )
        if whatsapp_match:
            payload = whatsapp_match.group(1).strip()
            explicit = re.search(r"^(.+?)\s+message\s+(.+)$", payload)
            if explicit:
                target = explicit.group(1).strip()
                message = explicit.group(2).strip()
            else:
                parts = payload.split()
                if len(parts) >= 2:
                    target = parts[0].strip()
                    message = " ".join(parts[1:]).strip()
                else:
                    target = payload
                    message = ""

            phone = re.sub(r"[^\d+]", "", target)
            matched_contact = ""
            if not re.fullmatch(r"\+?\d{8,15}", phone):
                matched_contact, phone = self.resolve_contact_phone(target)
            if not re.fullmatch(r"\+?\d{8,15}", phone):
                self.respond(
                    "I need a saved contact number for that name. Say: remember contact rajnish is +911234567890",
                    styled=False,
                    tag="message",
                )
                return True
            if not message:
                self.respond("Please provide a WhatsApp message.", styled=False, tag="message")
                return True

            url = f"https://web.whatsapp.com/send?phone={quote_plus(phone)}&text={quote_plus(message)}"
            self.pending_confirmation = {
                "action": "open_url",
                "url": url,
                "success_message": f"WhatsApp draft opened for {phone}. Please press send manually.",
            }
            if matched_contact:
                self.respond(
                    f"Prepared WhatsApp draft for {matched_contact} ({phone}). Say confirm to open draft or cancel to discard.",
                    styled=False,
                    tag="message",
                )
            else:
                self.respond(
                    f"Prepared WhatsApp draft for {phone}. Say confirm to open draft or cancel to discard.",
                    styled=False,
                    tag="message",
                )
            return True

        return False

    def handle_daily_briefing(self, query: str) -> bool:
        if not re.search(
            r"\b(daily briefing|morning briefing|brief me|day plan|today plan|day overview|today overview|daily update)\b",
            query,
        ):
            return False
        self.respond(self.build_daily_briefing(), tag="briefing")
        return True

    def handle_macros(self, query: str) -> bool:
        list_match = re.search(r"\b(list|show|view)\s+macros\b", query)
        if list_match:
            macros = self.memory.get("macros", {})
            if not macros:
                self.respond("No macros saved yet.", tag="macro")
                return True
            names = " | ".join(sorted(macros.keys()))
            self.respond(f"Saved macros: {names}.", tag="macro")
            return True

        create_match = re.search(r"^(?:create|add|save)\s+macro\s+(.+?)\s*[:\-]\s*(.+)$", query)
        if create_match:
            name = self.normalize_macro_name(create_match.group(1))
            actions = self.split_macro_actions(create_match.group(2))
            if not name or not actions:
                self.respond("Macro name or actions are missing.", tag="macro")
                return True
            with self.memory_lock:
                self.memory.setdefault("macros", {})[name] = actions
            self.persist_memory()
            self.respond(f"Macro {name} saved with {len(actions)} actions.", tag="macro")
            return True

        delete_match = re.search(r"^(?:delete|remove)\s+macro\s+(.+)$", query)
        if delete_match:
            name = self.normalize_macro_name(delete_match.group(1))
            with self.memory_lock:
                existed = name in self.memory.get("macros", {})
                if existed:
                    self.memory["macros"].pop(name, None)
            if existed:
                self.persist_memory()
                self.respond(f"Macro {name} removed.", tag="macro")
            else:
                self.respond(f"Macro {name} not found.", tag="macro")
            return True

        run_match = re.search(r"^(?:run|execute)\s+macro\s+(.+)$", query)
        mode_match = re.search(r"^start\s+(.+?)\s+mode$", query)
        if run_match or mode_match:
            raw_name = run_match.group(1) if run_match else mode_match.group(1)
            name = self.normalize_macro_name(raw_name)
            macros = self.memory.get("macros", {})

            if name not in macros and f"{name} mode" in macros:
                name = f"{name} mode"
            if name not in macros:
                self.respond(f"Macro {name} not found. Say list macros.", tag="macro")
                return True

            if name in self.active_macro_stack:
                self.respond("Macro recursion blocked for safety.", styled=False, tag="macro")
                return True

            actions = list(macros.get(name, []))[:MAX_MACRO_ACTIONS]
            if not actions:
                self.respond(f"Macro {name} has no actions.", tag="macro")
                return True

            self.active_macro_stack.append(name)
            previous_voice_output = self.is_voice_output_enabled()
            self.thread_state.voice_output = False
            completed = 0
            last_reply = ""
            try:
                for action in actions:
                    if not action:
                        continue
                    self.process_query(action, from_voice=False)
                    completed += 1
                    if self.last_response_text:
                        last_reply = self.last_response_text
            finally:
                self.thread_state.voice_output = previous_voice_output
                self.active_macro_stack.pop()

            if completed <= 0:
                self.respond(f"Macro {name} had nothing to run.", styled=False, tag="macro")
                return True

            if last_reply:
                self.respond(
                    f"Macro {name} executed {completed} actions. Last result: {last_reply}",
                    styled=False,
                    tag="macro",
                )
            else:
                self.respond(f"Macro {name} executed {completed} actions.", styled=False, tag="macro")
            return True

        return False

    def handle_advanced_memory(self, query: str) -> bool:
        person_set = re.search(r"^remember person\s+(.+?)\s+(?:as|is)\s+(.+)$", query)
        person_like = re.search(r"^remember\s+(.+?)\s+likes\s+(.+)$", query)
        project_set = re.search(r"^(?:remember|set)\s+project\s+(.+?)\s+(?:status|as|is)\s+(.+)$", query)
        contact_set = re.search(
            r"^(?:remember|save|set)\s+contact\s+(.+?)\s+(?:is|as)\s+(\+?\d[\d\s\-]{6,20})$",
            query,
        )
        note_add = re.search(r"^(?:add note|note)\s+(.+)$", query)

        if person_set:
            name = " ".join(person_set.group(1).lower().split())
            info = person_set.group(2).strip()
            if name and info:
                with self.memory_lock:
                    self.memory.setdefault("people", {})[name] = info
                self.persist_memory()
                self.respond(f"Saved person memory for {name}.", tag="memory")
                return True

        if person_like and not person_like.group(1).strip().startswith("my "):
            name = " ".join(person_like.group(1).lower().split())
            info = f"likes {person_like.group(2).strip()}"
            if name and info:
                with self.memory_lock:
                    previous = self.memory.setdefault("people", {}).get(name, "")
                    joined = f"{previous}; {info}".strip("; ").strip()
                    self.memory["people"][name] = joined
                self.persist_memory()
                self.respond(f"Noted preference for {name}.", tag="memory")
                return True

        if project_set:
            project = " ".join(project_set.group(1).lower().split())
            status = project_set.group(2).strip()
            if project and status:
                with self.memory_lock:
                    self.memory.setdefault("projects", {})[project] = status
                self.persist_memory()
                self.respond(f"Saved project status for {project}.", tag="memory")
                return True

        if contact_set:
            name = " ".join(contact_set.group(1).lower().split())
            phone = re.sub(r"[^\d+]", "", contact_set.group(2).strip())
            if not re.fullmatch(r"\+?\d{8,15}", phone):
                self.respond("Contact number format is invalid. Use full number with country code.", tag="memory")
                return True
            with self.memory_lock:
                self.memory.setdefault("contacts", {})[name] = phone
            self.persist_memory()
            self.respond(f"Saved contact {name} with number {phone}.", tag="memory")
            return True

        if note_add:
            note_text = note_add.group(1).strip()
            if note_text:
                with self.memory_lock:
                    self.memory.setdefault("notes", []).append(
                        {
                            "text": note_text,
                            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                        }
                    )
                    self.memory["notes"] = self.memory["notes"][-200:]
                self.persist_memory()
                self.respond("Note saved.", tag="memory")
                return True

        person_query = re.search(r"^(?:what do you know about|details about)\s+(.+)$", query)
        if person_query:
            name = " ".join(person_query.group(1).lower().split())
            details = self.memory.get("people", {}).get(name, "")
            if details:
                self.respond(f"Here is what I remember about {name}: {details}", tag="memory")
                return True
            return False

        if query in {"show people", "list people"}:
            people = self.memory.get("people", {})
            if not people:
                self.respond("No people memory saved yet.", tag="memory")
                return True
            lines = [f"{name}: {info}" for name, info in list(people.items())[:6]]
            self.respond("People memory: " + " | ".join(lines), tag="memory")
            return True

        if query in {"show projects", "list projects"}:
            projects = self.memory.get("projects", {})
            if not projects:
                self.respond("No tracked projects yet.", tag="memory")
                return True
            lines = [f"{name}: {status}" for name, status in list(projects.items())[:8]]
            self.respond("Project tracker: " + " | ".join(lines), tag="memory")
            return True

        if query in {"show contacts", "list contacts"}:
            contacts = self.memory.get("contacts", {})
            if not contacts:
                self.respond("No contacts saved yet.", tag="memory")
                return True
            lines = [f"{name}: {phone}" for name, phone in list(contacts.items())[:10]]
            self.respond("Saved contacts: " + " | ".join(lines), tag="memory")
            return True

        project_query = re.search(r"^(?:project status|status of project)\s+(.+)$", query)
        if project_query:
            project = " ".join(project_query.group(1).lower().split())
            status = self.memory.get("projects", {}).get(project, "")
            if status:
                self.respond(f"Project {project} status: {status}", tag="memory")
            else:
                self.respond(f"I do not have status for project {project}.", tag="memory")
            return True

        if query in {"show notes", "list notes", "my notes"}:
            notes = self.memory.get("notes", [])
            if not notes:
                self.respond("No notes saved yet.", tag="memory")
                return True
            preview = [note.get("text", "") for note in notes[-6:]]
            self.respond("Recent notes: " + " | ".join(preview), tag="memory")
            return True

        return False

    @staticmethod
    def is_path_in_root(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def scan_project_files(self) -> List[Path]:
        files: List[Path] = []
        for root, dirs, file_names in os.walk(BASE_DIR):
            dirs[:] = [folder for folder in dirs if folder.lower() not in FILE_SCAN_EXCLUDE_DIRS]
            for name in file_names:
                files.append(Path(root) / name)
                if len(files) >= MAX_FILE_SCAN_FILES:
                    return files
        return files

    @staticmethod
    def normalize_file_search_target(raw_target: str) -> str:
        target = raw_target.strip().lower()
        target = re.sub(r"^(?:name|named|called)\s+", "", target).strip()
        return target

    def get_computer_search_roots(self) -> List[Path]:
        if os.name == "nt":
            roots: List[Path] = []
            for drive_letter in WINDOWS_DRIVE_LETTERS:
                drive = Path(f"{drive_letter}:\\")
                if drive.exists():
                    roots.append(drive)
            return roots or [BASE_DIR]
        return [Path("/")]

    def get_priority_search_roots(self) -> List[Path]:
        home = Path.home()
        priority = [
            BASE_DIR,
            home,
            home / "Desktop",
            home / "Documents",
            home / "Downloads",
        ]
        output: List[Path] = []
        seen: set[str] = set()
        for root in priority:
            if not root.exists():
                continue
            key = str(root.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            output.append(root)
        return output

    def scan_roots_for_target(
        self,
        roots: List[Path],
        needle: str,
        scan_limit: int,
        scanned_start: int = 0,
        stop_on_first_match: bool = False,
        timeout_seconds: float = 0.0,
    ) -> Tuple[List[Path], int, bool]:
        matches: List[Path] = []
        scanned = scanned_start
        started_at = time.monotonic()
        for root in roots:
            if not root.exists():
                continue
            for current_root, dirs, file_names in os.walk(root, topdown=True, onerror=lambda _err: None):
                dirs[:] = [
                    folder
                    for folder in dirs
                    if folder.lower() not in GLOBAL_FILE_SCAN_EXCLUDE_DIRS
                    and folder.lower() not in FILE_SCAN_EXCLUDE_DIRS
                ]
                for file_name in file_names:
                    if timeout_seconds > 0 and (time.monotonic() - started_at) >= timeout_seconds:
                        return matches, scanned, True
                    scanned += 1
                    lower_name = file_name.lower()
                    full_path = Path(current_root) / file_name
                    if needle in lower_name or needle in str(full_path).lower():
                        matches.append(full_path)
                        if stop_on_first_match and matches:
                            return matches, scanned, False
                        if len(matches) >= MAX_FILE_SCAN_RESULTS:
                            return matches, scanned, False
                    if scanned >= scan_limit:
                        return matches, scanned, True
        return matches, scanned, False

    def scan_computer_files(self, target: str) -> Tuple[List[Path], bool]:
        needle = target.strip().lower()
        if not needle:
            return [], False

        priority_limit = max(120000, MAX_GLOBAL_FILE_SCAN_FILES // 4)
        priority_matches, scanned, priority_limit_hit = self.scan_roots_for_target(
            self.get_priority_search_roots(),
            needle,
            scan_limit=priority_limit,
            scanned_start=0,
            stop_on_first_match=True,
            timeout_seconds=8.0,
        )
        if priority_matches:
            return priority_matches, priority_limit_hit

        all_matches, _, global_limit_hit = self.scan_roots_for_target(
            self.get_computer_search_roots(),
            needle,
            scan_limit=MAX_GLOBAL_FILE_SCAN_FILES,
            scanned_start=scanned,
            timeout_seconds=20.0,
        )
        return all_matches, global_limit_hit or priority_limit_hit

    def resolve_project_path(self, path_text: str) -> Path | None:
        stripped = path_text.strip().strip('"').strip("'")
        if not stripped:
            return None
        candidate = Path(stripped)
        if not candidate.is_absolute():
            candidate = (BASE_DIR / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if not candidate.exists():
            return None
        if not self.is_path_in_root(candidate, BASE_DIR):
            return None
        return candidate

    def handle_file_assistant(self, query: str) -> bool:
        find_file_project = re.search(r"^(?:find file in project|locate file in project)\s+(.+)$", query)
        if find_file_project:
            target = self.normalize_file_search_target(find_file_project.group(1))
            if not target:
                self.respond("Tell me which filename to search in project.", styled=False, tag="file")
                return True
            matches = [
                path
                for path in self.scan_project_files()
                if target in path.name.lower()
            ][:MAX_FILE_SCAN_RESULTS]
            if not matches:
                self.respond("No matching files found in project.", styled=False, tag="file")
                return True
            refs = [str(path.relative_to(BASE_DIR)) for path in matches]
            self.respond("Project file matches: " + " | ".join(refs), styled=False, tag="file")
            return True

        find_file = re.search(r"^(?:find file|locate file)\s+(.+)$", query)
        if find_file:
            target = self.normalize_file_search_target(find_file.group(1))
            if not target:
                self.respond("Tell me which filename to search.", styled=False, tag="file")
                return True
            matches, limit_hit = self.scan_computer_files(target)
            if not matches:
                if limit_hit:
                    self.respond(
                        "No matching files found yet. Search limit reached, try a more specific name.",
                        styled=False,
                        tag="file",
                    )
                else:
                    self.respond("No matching files found on this computer.", styled=False, tag="file")
                return True
            refs = [str(path) for path in matches]
            self.respond("File matches: " + " | ".join(refs), styled=False, tag="file")
            return True

        search_files = re.search(r"^(?:search files for|find in files)\s+(.+)$", query)
        if search_files:
            needle = search_files.group(1).strip().lower()
            if not needle:
                self.respond("Tell me what text to search in files.", styled=False, tag="file")
                return True
            hits: List[str] = []
            for path in self.scan_project_files():
                if path.suffix.lower() not in FILE_SCAN_TEXT_EXTENSIONS:
                    continue
                try:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if len(content) > MAX_FILE_READ_CHARS:
                    content = content[:MAX_FILE_READ_CHARS]
                lower = content.lower()
                if needle not in lower:
                    continue
                line_no = 1 + lower[: lower.find(needle)].count("\n")
                hits.append(f"{path.relative_to(BASE_DIR)}:{line_no}")
                if len(hits) >= MAX_FILE_SCAN_RESULTS:
                    break
            if not hits:
                self.respond("No matching text found in scanned project files.", styled=False, tag="file")
                return True
            self.respond("Text matches: " + " | ".join(hits), styled=False, tag="file")
            return True

        summarize_file = re.search(r"^(?:summarize file|read file)\s+(.+)$", query)
        if summarize_file:
            path = self.resolve_project_path(summarize_file.group(1))
            if not path or not path.is_file():
                self.respond("File not found in project.", styled=False, tag="file")
                return True
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                self.respond("Unable to read that file.", styled=False, tag="file")
                return True
            sample = content[:MAX_FILE_READ_CHARS]
            lines = [line.strip() for line in sample.splitlines() if line.strip()][:5]
            summary = " | ".join(lines) if lines else "File is empty or non-text."
            self.respond(
                f"Summary of {path.relative_to(BASE_DIR)}: {summary}",
                styled=False,
                tag="file",
            )
            return True

        open_file = re.search(r"^open file\s+(.+)$", query)
        if open_file:
            path = self.resolve_project_path(open_file.group(1))
            if not path or not path.is_file():
                self.respond("File not found in project.", styled=False, tag="file")
                return True
            os.startfile(str(path))
            self.respond(f"Opening file {path.relative_to(BASE_DIR)}.", styled=False, tag="file")
            return True

        return False

    def handle_pc_automation(self, query: str) -> bool:
        copy_match = re.search(r"^(?:copy to clipboard|clipboard copy)\s+(.+)$", query)
        if copy_match:
            text = copy_match.group(1).strip()
            if not text:
                self.respond("Nothing to copy.", styled=False, tag="system")
                return True
            try:
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $input"],
                    input=text,
                    text=True,
                    check=True,
                )
                self.respond("Copied to clipboard.", styled=False, tag="system")
            except Exception:
                self.respond("Could not copy to clipboard.", styled=False, tag="system")
            return True

        if query in {"show clipboard", "what is on clipboard", "clipboard"}:
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    check=True,
                )
                clip = result.stdout.strip()
                if not clip:
                    self.respond("Clipboard is empty.", styled=False, tag="system")
                else:
                    preview = clip[:180]
                    self.respond(f"Clipboard contains: {preview}", styled=False, tag="system")
            except Exception:
                self.respond("Unable to read clipboard right now.", styled=False, tag="system")
            return True

        if "lock computer" in query or "lock workstation" in query or "lock screen" in query:
            self.pending_confirmation = {"action": "lock_workstation"}
            self.respond("Say confirm to lock the workstation, or cancel.", styled=False, tag="system")
            return True

        if "open task manager" in query:
            os.system("taskmgr")
            self.respond("Opening Task Manager.", styled=False, tag="system")
            return True

        if "open command prompt" in query or "open cmd" in query:
            os.system("start cmd")
            self.respond("Opening Command Prompt.", styled=False, tag="system")
            return True

        if "open powershell" in query:
            os.system("start powershell")
            self.respond("Opening PowerShell.", styled=False, tag="system")
            return True

        if "show desktop" in query or "minimize all windows" in query:
            user32 = ctypes.windll.user32
            key_up = 0x0002
            user32.keybd_event(0x5B, 0, 0, 0)  # Win
            user32.keybd_event(0x44, 0, 0, 0)  # D
            user32.keybd_event(0x44, 0, key_up, 0)
            user32.keybd_event(0x5B, 0, key_up, 0)
            self.respond("Desktop shown.", styled=False, tag="system")
            return True

        if "restore windows" in query:
            user32 = ctypes.windll.user32
            key_up = 0x0002
            user32.keybd_event(0x5B, 0, 0, 0)  # Win
            user32.keybd_event(0x10, 0, 0, 0)  # Shift
            user32.keybd_event(0x4D, 0, 0, 0)  # M
            user32.keybd_event(0x4D, 0, key_up, 0)
            user32.keybd_event(0x10, 0, key_up, 0)
            user32.keybd_event(0x5B, 0, key_up, 0)
            self.respond("Windows restored.", styled=False, tag="system")
            return True

        return False

    def infer_intent_query(self, query: str) -> str:
        clean = " ".join(re.sub(r"[^\w\s@.+-]", " ", query.lower()).split())
        if not clean:
            return query

        remind_match = re.search(r"^remind me to\s+(.+)$", clean)
        if remind_match:
            return f"add task {remind_match.group(1).strip()}"

        google_match = re.search(r"^(?:can you\s+)?google\s+(.+)$", clean)
        if google_match:
            return f"search {google_match.group(1).strip()}"

        youtube_match = re.search(r"^(?:can you\s+)?youtube\s+(.+)$", clean)
        if youtube_match:
            return f"search youtube for {youtube_match.group(1).strip()}"

        if re.search(r"\b(what.*(tasks|to do)|my workload|my plan)\b", clean):
            return "show tasks"

        if re.search(r"\b(brief me|day overview|today overview|daily update)\b", clean):
            return "daily briefing"

        if "open calc" in clean:
            return "open calculator"
        if "open note pad" in clean:
            return "open notepad"
        if "lock pc" in clean:
            return "lock computer"

        if clean.startswith("find code for "):
            return "search files for " + clean.replace("find code for ", "", 1).strip()

        candidate_queries = [
            "show tasks",
            "daily briefing",
            "what can you do",
            "show notes",
            "show projects",
            "open calculator",
            "open task manager",
            "list macros",
        ]
        best = max(candidate_queries, key=lambda item: difflib.SequenceMatcher(None, clean, item).ratio())
        score = difflib.SequenceMatcher(None, clean, best).ratio()
        if score >= 0.82:
            return best
        return query

    def dispatch_query_handlers(self, query: str, from_voice: bool = False) -> bool:
        if self.handle_confirmation_actions(query):
            return True
        if self.handle_tone_request(query):
            return True
        if self.handle_personality(query):
            return True
        if self.handle_advanced_memory(query):
            return True
        if self.handle_identity_and_memory(query):
            return True
        if self.handle_macros(query):
            return True
        if self.handle_tasks(query):
            return True
        if self.handle_daily_briefing(query):
            return True
        if self.handle_abilities_and_history(query):
            return True
        if self.handle_emotions(query):
            return True
        if self.handle_time_and_date(query):
            return True
        if self.handle_calculation(query):
            return True
        if self.handle_message_actions(query):
            return True
        if self.handle_file_assistant(query):
            return True
        if self.handle_wikipedia(query):
            return True
        if self.handle_open_and_system_actions(query):
            return True
        if self.handle_pc_automation(query):
            return True
        if self.handle_web_search(query, from_voice=from_voice):
            return True
        if self.handle_implicit_voice_search(query, from_voice=from_voice):
            return True
        return False

    def handle_identity_and_memory(self, query: str) -> bool:
        name_match = re.search(r"\b(?:my name is|call me)\s+([a-z][a-z\s]{1,30})$", query)
        if name_match:
            clean_name = " ".join(name_match.group(1).split()).title()
            with self.memory_lock:
                self.memory["user_profile"]["name"] = clean_name
            self.persist_memory()
            self.respond(f"Noted. I will call you {clean_name}.", tag="memory")
            return True

        if "what is my name" in query or "who am i" in query:
            user_name = self.memory["user_profile"].get("name", "Mayuri")
            self.respond(f"Your name is {user_name}.", tag="memory")
            return True

        remember_match = re.search(r"^remember (?:that )?(?:my )?(.+?) is (.+)$", query)
        if remember_match:
            key = normalize_fact_key(remember_match.group(1))
            value = remember_match.group(2).strip()
            if not key:
                return False
            with self.memory_lock:
                self.memory["facts"][key] = value
            self.persist_memory()
            self.respond(f"I will remember your {key} is {value}.", tag="memory")
            return True

        direct_fact_match = re.search(r"^(?:my\s+)?(.+?)\s+is\s+(.+)$", query)
        if direct_fact_match:
            key = normalize_fact_key(direct_fact_match.group(1))
            value = direct_fact_match.group(2).strip()
            if key in AUTO_MEMORY_FACT_KEYS and value:
                with self.memory_lock:
                    self.memory["facts"][key] = value
                self.persist_memory()
                self.respond(f"I will remember your {key} is {value}.", tag="memory")
                return True

        recall_match = re.search(r"^(?:what is|what's|tell me|when is|do you remember) my (.+)$", query)
        if recall_match:
            key = normalize_fact_key(recall_match.group(1))
            routing_blocklist = {"tasks", "todo", "to do", "workload", "plan", "schedule", "day"}
            if any(token in key for token in routing_blocklist):
                return False
            facts = self.memory.get("facts", {})
            if key in facts:
                self.respond(f"Your {key} is {facts[key]}.", tag="memory")
            else:
                self.respond(f"I do not have your {key} in memory yet.", tag="memory")
            return True

        forget_match = re.search(r"^forget (?:my )?(.+)$", query)
        if forget_match:
            key = normalize_fact_key(forget_match.group(1))
            if key in self.memory.get("facts", {}):
                with self.memory_lock:
                    self.memory["facts"].pop(key, None)
                self.persist_memory()
                self.respond(f"Done. I removed your {key} from memory.", tag="memory")
            else:
                self.respond(f"{key} was not in memory.", tag="memory")
            return True

        return False

    def handle_tone_request(self, query: str) -> bool:
        if re.search(r"\b(what|which).*(tone|mode)\b", query):
            self.respond(f"My current tone mode is {self.tone}.", tag="tone")
            return True

        tone_match = re.search(r"\b(formal|friendly|sarcastic)\b", query)
        if tone_match:
            should_switch = bool(
                re.search(r"\b(set|switch|change|use|be|make|enable)\b", query)
                or "tone" in query
                or "mode" in query
            )
            if should_switch:
                selected_tone = tone_match.group(1)
                self.set_tone(selected_tone)
                self.respond(f"Tone changed to {selected_tone}.", tag="tone")
                return True
        return False

    def handle_personality(self, query: str) -> bool:
        if "what are your traits" in query or "personality traits" in query:
            traits = ", ".join(self.memory["personality"].get("traits", []))
            self.respond(f"My personality traits are: {traits}.", tag="personality")
            return True

        if any(token in query for token in ("more sass", "increase sass", "be sassier")):
            with self.memory_lock:
                current = float(self.memory["personality"].get("sass_level", 0.55))
                self.memory["personality"]["sass_level"] = min(1.0, current + 0.15)
            self.persist_memory()
            value = self.memory["personality"]["sass_level"]
            self.respond(f"Sass level increased to {value:.2f}.", tag="personality")
            return True

        if any(token in query for token in ("less sass", "decrease sass", "be less sarcastic")):
            with self.memory_lock:
                current = float(self.memory["personality"].get("sass_level", 0.55))
                self.memory["personality"]["sass_level"] = max(0.0, current - 0.15)
            self.persist_memory()
            value = self.memory["personality"]["sass_level"]
            self.respond(f"Sass level reduced to {value:.2f}.", tag="personality")
            return True

        level_match = re.search(r"set sass level to\s*(0(?:\.\d+)?|1(?:\.0+)?)", query)
        if level_match:
            level = float(level_match.group(1))
            with self.memory_lock:
                self.memory["personality"]["sass_level"] = level
            self.persist_memory()
            self.respond(f"Sass level set to {level:.2f}.", tag="personality")
            return True

        if "what is your sass level" in query or "current sass level" in query:
            level = float(self.memory["personality"].get("sass_level", 0.55))
            self.respond(f"My current sass level is {level:.2f}.", tag="personality")
            return True

        return False

    def handle_tasks(self, query: str) -> bool:
        add_match = re.search(r"^(?:add task|remember task|todo)\s+(.+)$", query)
        if add_match:
            task_text = add_match.group(1).strip()
            with self.memory_lock:
                self.memory["tasks"].append(
                    {
                        "text": task_text,
                        "done": False,
                        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                    }
                )
            self.persist_memory()
            self.respond(f"Task added: {task_text}", tag="task")
            return True

        if "show tasks" in query or "my tasks" in query or "todo list" in query:
            tasks = self.memory.get("tasks", [])
            if not tasks:
                self.respond("Your task list is empty.", tag="task")
                return True

            lines = []
            for index, task in enumerate(tasks, start=1):
                status = "done" if task.get("done") else "pending"
                lines.append(f"{index}. {task.get('text', '')} ({status})")
            self.respond("Here is your task list: " + " | ".join(lines), tag="task")
            return True

        done_match = re.search(r"^(?:complete|finish|done) task (\d+)$", query)
        if done_match:
            position = int(done_match.group(1)) - 1
            tasks = self.memory.get("tasks", [])
            if 0 <= position < len(tasks):
                with self.memory_lock:
                    self.memory["tasks"][position]["done"] = True
                self.persist_memory()
                self.respond(f"Task {position + 1} marked complete.", tag="task")
            else:
                self.respond("That task number does not exist.", tag="task")
            return True

        return False

    def handle_abilities_and_history(self, query: str) -> bool:
        if (
            "what can you do" in query
            or "abilities" in query
            or query in {"help", "commands"}
        ):
            info = (
                "I can switch tone modes, remember personal facts, keep conversation history, "
                "reply in Hinglish style, manage tasks, run workflow macros, prepare WhatsApp and email drafts, "
                "track people and projects, search local files, control PC actions, search web, open apps, "
                "answer time and date, fetch quick Wikipedia summaries, and tune my sass level."
            )
            self.respond(info, tag="abilities")
            return True

        if (
            "conversation history" in query
            or "what were we talking about" in query
            or "what did i ask" in query
            or "recap" in query
            or "summary of chat" in query
        ):
            self.respond(self.build_conversation_summary(), tag="history")
            return True

        if "tell me more" in query:
            topic = self.memory["session"].get("last_topic", "")
            if topic:
                try:
                    summary = wikipedia.summary(topic, sentences=4, auto_suggest=False)
                    self.respond(summary, tag="wiki")
                except wikipedia.exceptions.WikipediaException:
                    self.respond("I could not fetch more details right now.", tag="wiki")
            else:
                self.respond("No recent topic available. Ask me about something first.", tag="wiki")
            return True

        return False

    def handle_emotions(self, query: str) -> bool:
        negative_words = {"sad", "upset", "tired", "angry", "stressed", "low"}
        positive_words = {"happy", "excited", "good", "great", "awesome", "fine"}

        if any(word in query for word in negative_words):
            with self.memory_lock:
                self.memory["session"]["last_mood"] = "low"
            self.persist_memory()
            self.respond("That sounds rough. Main yahin hoon, bolte raho.", tag="emotion")
            return True

        if any(word in query for word in positive_words):
            with self.memory_lock:
                self.memory["session"]["last_mood"] = "positive"
            self.persist_memory()
            self.respond("Nice energy. Keep it going.", tag="emotion")
            return True

        return False

    def handle_time_and_date(self, query: str) -> bool:
        if re.search(r"\btime\b", query):
            now_time = dt.datetime.now().strftime("%I:%M %p")
            self.respond(f"The current time is {now_time}.", tag="time")
            return True

        if re.search(r"\bdate\b|\bday\b", query):
            today = dt.datetime.now().strftime("%d %B %Y")
            self.respond(f"Today is {today}.", tag="date")
            return True

        return False

    def handle_calculation(self, query: str) -> bool:
        if query.startswith("calculate "):
            expression = query.replace("calculate ", "", 1).strip()
        elif query.startswith("what is "):
            expression = query.replace("what is ", "", 1).strip().rstrip("?")
            if not re.fullmatch(r"[0-9+\-*/().%\s]+", expression):
                return False
        else:
            return False

        try:
            result = safe_eval_expression(expression)
            self.respond(f"{expression} equals {result}.", tag="calc")
        except (ValueError, ZeroDivisionError):
            self.respond("I could not calculate that expression safely.", tag="calc")
        return True

    def handle_wikipedia(self, query: str) -> bool:
        prefixes = ("who is ", "what is ", "tell me about ", "define ")
        topic = ""
        for prefix in prefixes:
            if query.startswith(prefix):
                topic = query.replace(prefix, "", 1).strip()
                break
        if not topic:
            return False
        if re.search(r"^(?:my\s+)?(?:workload|tasks?|todo|to do|plan|schedule|day)\b", topic):
            return False

        try:
            summary = wikipedia.summary(topic, sentences=2, auto_suggest=False, redirect=True)
            self.remember_topic(topic)
            self.respond(summary, tag="wiki")
        except wikipedia.DisambiguationError as err:
            options = ", ".join(err.options[:4])
            self.respond(f"{topic} has multiple meanings: {options}. Be more specific.", tag="wiki")
        except wikipedia.PageError:
            self.respond(f"I could not find a reliable page for {topic}.", tag="wiki")
        except wikipedia.exceptions.WikipediaException:
            self.respond("Wikipedia is not reachable right now.", tag="wiki")
        return True

    def handle_open_and_system_actions(self, query: str) -> bool:
        if "open youtube" in query:
            webbrowser.open("https://www.youtube.com")
            self.respond("Opening YouTube.", styled=False, tag="system")
            return True

        if "open google" in query:
            webbrowser.open("https://www.google.com")
            self.respond("Opening Google.", styled=False, tag="system")
            return True

        if "open amazon" in query:
            webbrowser.open("https://www.amazon.com")
            self.respond("Opening Amazon.", styled=False, tag="system")
            return True

        if "open chrome" in query:
            try:
                os.startfile("chrome")
                self.respond("Opening Chrome.", styled=False, tag="system")
            except OSError:
                webbrowser.open("https://www.google.com")
                self.respond("Chrome command failed, opening browser via web.", styled=False, tag="system")
            return True

        if "open notepad" in query:
            os.system("notepad")
            self.respond("Opening Notepad.", styled=False, tag="system")
            return True

        if "open calculator" in query:
            os.system("calc")
            self.respond("Opening Calculator.", styled=False, tag="system")
            return True

        if "open desktop" in query:
            desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
            if desktop.exists():
                os.startfile(str(desktop))
                self.respond("Opening Desktop folder.", styled=False, tag="system")
            else:
                self.respond("Desktop path not found.", styled=False, tag="system")
            return True

        if "open downloads" in query:
            downloads = Path(os.environ.get("USERPROFILE", "")) / "Downloads"
            if downloads.exists():
                os.startfile(str(downloads))
                self.respond("Opening Downloads folder.", styled=False, tag="system")
            else:
                self.respond("Downloads path not found.", styled=False, tag="system")
            return True

        if "mute volume" in query:
            ctypes.windll.user32.keybd_event(0xAD, 0, 0, 0)
            self.respond("Volume muted.", styled=False, tag="system")
            return True

        if "volume up" in query:
            for _ in range(5):
                ctypes.windll.user32.keybd_event(0xAF, 0, 0, 0)
            self.respond("Volume increased.", styled=False, tag="system")
            return True

        if "volume down" in query:
            for _ in range(5):
                ctypes.windll.user32.keybd_event(0xAE, 0, 0, 0)
            self.respond("Volume decreased.", styled=False, tag="system")
            return True

        return False

    def handle_web_search(self, query: str, from_voice: bool = False) -> bool:
        youtube_match = re.search(
            r"^(?:open youtube (?:and )?search(?: for)?|search (?:on )?youtube for|youtube search(?: for)?|play)\s+(.+?)(?: on youtube)?$",
            query,
        )
        if not youtube_match:
            youtube_match = re.search(r"^search (.+) on youtube$", query)
        if youtube_match:
            target = youtube_match.group(1).strip()
            if not target:
                self.respond("Please tell me what to search on YouTube.", styled=False, tag="search")
                return True
            url = f"https://www.youtube.com/results?search_query={quote_plus(target)}"
            webbrowser.open(url)
            self.remember_topic(target)
            self.respond(f"Searching YouTube for {target}.", styled=False, tag="search")
            return True

        amazon_match = re.search(
            r"^(?:search amazon for|search on amazon for|amazon search(?: for)?|find on amazon|open amazon (?:and )?search(?: for)?)\s+(.+)$",
            query,
        )
        if not amazon_match:
            amazon_match = re.search(r"^search (.+) on amazon$", query)
        if amazon_match:
            target = amazon_match.group(1).strip()
            if not target:
                self.respond("Please tell me what to search on Amazon.", styled=False, tag="search")
                return True
            url = f"https://www.amazon.com/s?k={quote_plus(target)}"
            webbrowser.open(url)
            self.remember_topic(target)
            self.respond(f"Searching Amazon for {target}.", styled=False, tag="search")
            return True

        google_match = re.search(
            r"^(?:search google for|search on google for|google search(?: for)?|google|search)\s+(.+)$",
            query,
        )
        if not google_match:
            google_match = re.search(r"^search (.+) on google$", query)
        if not google_match:
            return False

        target = google_match.group(1).strip()
        target = re.sub(r"\s+on\s+google$", "", target).strip()
        if not target:
            self.respond("Please tell me what to search on Google.", styled=False, tag="search")
            return True

        url = f"https://www.google.com/search?q={quote_plus(target)}"
        webbrowser.open(url)
        self.remember_topic(target)
        self.respond(f"Searching Google for {target}.", styled=False, tag="search")
        return True

    def handle_implicit_voice_search(self, query: str, from_voice: bool) -> bool:
        if not from_voice:
            return False
        if not self.should_implicit_voice_search(query):
            return False
        target = query.strip()
        if not target:
            return False
        url = f"https://www.google.com/search?q={quote_plus(target)}"
        webbrowser.open(url)
        self.remember_topic(target)
        self.respond(f"Searching Google for {target}.", styled=False, tag="search")
        return True

    def fallback_response(self, query: str) -> None:
        last_topic = self.memory["session"].get("last_topic", "")
        if any(word in query for word in {"it", "that", "this"}) and last_topic:
            self.respond(
                f"If you mean {last_topic}, say tell me more or search {last_topic}.",
                tag="fallback",
            )
            return
        self.respond(random.choice(FALLBACK_PROMPTS), tag="fallback")

    def process_query(self, query: str, from_voice: bool = False) -> None:
        raw_query = query.strip().lower()
        clean_query = raw_query
        if from_voice:
            clean_query = self.repair_common_voice_transcript(clean_query)
        if not clean_query:
            self.speak_retry_prompt()
            return

        self.log(f"Processing: {clean_query}")
        self.remember_turn("user", raw_query, tag="query")

        if not self.awake and self.is_wake_phrase(clean_query):
            self.awake = True
            self.sleep_started_at = 0.0
            wake_remainder = self.strip_wake_prefix(clean_query)
            if not wake_remainder or wake_remainder in WAKE_ONLY_COMMANDS:
                self.respond("I am awake now. Ready for your command.", styled=False, tag="wake")
                return
            clean_query = wake_remainder

        normalized_query = self.strip_wake_prefix(clean_query)
        if not normalized_query:
            normalized_query = clean_query

        if normalized_query in WAKE_ONLY_COMMANDS:
            if self.awake:
                self.respond("I am already awake and listening.", styled=False, tag="wake")
            else:
                self.awake = True
                self.sleep_started_at = 0.0
                self.respond("I am awake now. Ready for your command.", styled=False, tag="wake")
            return

        if self.is_sleep_phrase(normalized_query):
            self.awake = False
            self.sleep_started_at = time.time()
            self.respond("Entering sleep mode now.", styled=False, tag="sleep")
            return

        if normalized_query in {"wake up", "resume", "i am back"}:
            if self.awake:
                self.respond("I am already awake and listening.", styled=False, tag="wake")
            else:
                self.awake = True
                self.sleep_started_at = 0.0
                self.respond("I am awake now. Ready for your command.", styled=False, tag="wake")
            return

        if self.is_exit_phrase(normalized_query):
            self.respond("Shutting down. See you soon.", styled=False, tag="shutdown")
            self.listening = False
            return

        if self.dispatch_query_handlers(normalized_query, from_voice=from_voice):
            return

        inferred_query = self.infer_intent_query(normalized_query)
        if inferred_query != normalized_query:
            self.log(f"Inferred intent: {normalized_query} -> {inferred_query}")
            if self.dispatch_query_handlers(inferred_query, from_voice=from_voice):
                return

        self.fallback_response(inferred_query)

    def execute_text_command(self, query: str, speak_output: bool = False, from_voice: bool = False) -> str:
        with self.command_lock:
            before = len(self.memory.get("conversation_history", []))
            previous_voice_output = self.is_voice_output_enabled()
            self.thread_state.voice_output = speak_output
            try:
                self.process_query(query, from_voice=from_voice)
            finally:
                self.thread_state.voice_output = previous_voice_output
            if self.last_response_text:
                return self.last_response_text

            history = self.memory.get("conversation_history", [])
            if len(history) > before:
                for event in reversed(history[before:]):
                    if event.get("role") == "assistant":
                        return str(event.get("text", "")).strip()
            return ""

    def get_recent_history(self, limit: int = 30) -> List[Dict[str, str]]:
        with self.memory_lock:
            history = self.memory.get("conversation_history", [])[-limit:]

        formatted: List[Dict[str, str]] = []
        for item in history:
            role = str(item.get("role", "assistant"))
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            formatted.append({"role": role, "text": text})
        return formatted

    def reset_conversation(self) -> None:
        with self.memory_lock:
            self.memory["conversation_history"] = []
            if "session" in self.memory and isinstance(self.memory["session"], dict):
                self.memory["session"]["last_topic"] = ""
        self.last_response_text = ""
        self.persist_memory()

    def get_state_snapshot(self) -> Dict[str, Any]:
        with self.memory_lock:
            facts_count = len(self.memory.get("facts", {}))
            tasks_count = len(self.memory.get("tasks", []))
            history_count = len(self.memory.get("conversation_history", []))
            tone = self.memory.get("preferences", {}).get("tone", "friendly")
            notes_count = len(self.memory.get("notes", []))
            project_count = len(self.memory.get("projects", {}))

        compute = min(98, 35 + (history_count % 60))
        mem = min(98, 30 + (facts_count * 4 + tasks_count * 3 + notes_count + project_count * 2))
        latency = 18 + (history_count % 21)

        return {
            "awake": self.awake,
            "sleeping": not self.awake,
            "listening": self.listening,
            "tone": tone,
            "pending_confirmation": bool(self.pending_confirmation),
            "status": "sleeping" if not self.awake else "online",
            "stats": {
                "compute": compute,
                "memory": mem,
                "latency_ms": latency,
            },
            "analytics": self.get_analytics_snapshot(),
        }

    def run(self) -> None:
        self.greet_user()
        threading.Thread(target=self.wake_word_listener, daemon=True).start()
        print("Jarvis running...")

        while self.listening:
            if self.awake:
                if self.pending_wake_response:
                    wake_text = self.pending_wake_response
                    self.pending_wake_response = ""
                    self.respond(wake_text, styled=False, tag="wake")
                if self.pending_command:
                    queued = self.pending_command
                    self.pending_command = ""
                    print(f"You: {queued}")
                    self.process_query(queued, from_voice=True)
                    continue
                command = self.take_command()
                if command:
                    self.process_query(command, from_voice=True)
                else:
                    self.speak_retry_prompt()
            else:
                time.sleep(0.9)

