from __future__ import annotations

from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from assistant_core import JarvisAssistant
from constants import INDEX_FILE, LISTEN_DURATION_SECONDS, MEMORY_FILE, NO_CACHE_HEADERS, RETRY_PROMPTS

assistant = JarvisAssistant(MEMORY_FILE)
app = FastAPI(title="Jarvis API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CommandRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=400)


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=140)


def api_response(reply: str) -> Dict[str, Any]:
    payload = assistant.get_state_snapshot()
    payload["reply"] = reply
    payload["history"] = assistant.get_recent_history(limit=20)
    return payload


@app.get("/")
def index() -> RedirectResponse:
    return RedirectResponse("/index.html", status_code=307, headers=NO_CACHE_HEADERS)


@app.get("/index.html")
def index_file() -> FileResponse:
    if not INDEX_FILE.exists():
        raise HTTPException(status_code=404, detail="index.html not found.")
    assistant.reset_conversation()
    return FileResponse(INDEX_FILE, headers=NO_CACHE_HEADERS)


@app.get("/_sdk/data_sdk.js")
@app.get("/_sdk/element_sdk.js")
@app.get("/cdn-cgi/challenge-platform/scripts/jsd/main.js")
def compatibility_script_stub() -> PlainTextResponse:
    return PlainTextResponse("// compatibility script stub\n", media_type="application/javascript")


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/state")
def get_state() -> Dict[str, Any]:
    payload = assistant.get_state_snapshot()
    payload["history"] = assistant.get_recent_history(limit=20)
    return payload


@app.get("/api/analytics")
def get_analytics() -> Dict[str, Any]:
    return assistant.get_analytics_snapshot()


@app.post("/api/reset-chat")
def reset_chat() -> Dict[str, Any]:
    assistant.reset_conversation()
    payload = assistant.get_state_snapshot()
    payload["history"] = []
    payload["reply"] = ""
    return payload


@app.post("/api/command")
def command(req: CommandRequest) -> Dict[str, Any]:
    message = req.message.strip()
    if not message:
        return api_response(RETRY_PROMPTS[0])

    if not assistant.awake and not assistant.is_wake_phrase(message):
        return api_response("Jarvis is sleeping. Say wake up or jarvis first.")

    reply = assistant.execute_text_command(message, speak_output=True) or "Done."
    return api_response(reply)


@app.post("/api/speak")
def speak(req: SpeakRequest) -> Dict[str, Any]:
    text = req.text.strip()
    if not text:
        return {"ok": False}
    assistant.speak(text)
    return {"ok": True}


@app.post("/api/listen-command")
def listen_command() -> Dict[str, Any]:
    heard = assistant.capture_speech(LISTEN_DURATION_SECONDS).lower().strip()
    if not heard:
        payload = assistant.get_state_snapshot()
        payload["heard"] = ""
        payload["reply"] = ""
        payload["history"] = assistant.get_recent_history(limit=20)
        return payload

    if not assistant.awake and not assistant.is_wake_phrase(heard):
        payload = api_response("Jarvis is sleeping. Say wake up or jarvis first.")
        payload["heard"] = heard
        return payload

    reply = assistant.execute_text_command(heard, speak_output=True, from_voice=True) or "Done."
    payload = api_response(reply)
    payload["heard"] = heard
    return payload


@app.post("/api/sleep")
def sleep() -> Dict[str, Any]:
    reply = assistant.execute_text_command("go to sleep", speak_output=True) or "Entering sleep mode now."
    return api_response(reply)


@app.post("/api/wake")
def wake() -> Dict[str, Any]:
    reply = assistant.execute_text_command("wake up", speak_output=True) or "I am awake now. Listening."
    return api_response(reply)
