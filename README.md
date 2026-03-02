# Jarvis AI Assistant

Voice-enabled desktop assistant with FastAPI backend, browser UI, CLI mode, persistent memory, workflow automation, and analytics.

The main app is in `AI ML Project/`.

## Features

- Voice input via microphone (`sounddevice` + Google SpeechRecognition)
- Text-to-speech replies (`pyttsx3`)
- Sleep/wake behavior with wake-word listener
- Tone modes: `formal`, `friendly`, `sarcastic`
- Smart memory:
- User facts (`remember`, `forget`, `what is my ...`)
- People memory (`remember person rohit is backend engineer`)
- Project tracking (`set project jarvis status in testing`)
- Notes (`add note ...`, `show notes`)
- Task management (`add task`, `show tasks`, `complete task N`)
- Daily briefing (`daily briefing`, `day overview`)
- Workflow macros (`create macro`, `list macros`, `run macro`, `delete macro`)
- Messaging drafts with confirmation:
- Email draft flow (`confirm` / `cancel`)
- File assistant:
- `find file ...`
- `search files for ...`
- `summarize file ...`
- `open file ...`
- PC automation:
- Clipboard copy/read
- Open Task Manager / CMD / PowerShell
- Show desktop / restore windows
- Lock workstation with confirmation
- Web + Wikipedia lookup support
- Intent inference fallback for natural phrasing
- Usage analytics (commands today, total commands, top intents)

## Project Structure

```text
Jarvis/
  README.md
  AI ML Project/
    main.py
    api_server.py
    assistant_core.py
    storage.py
    constants.py
    index.html
    memory.json
    requirements.txt
    .venv/
```

## Code Layout

- `main.py`: Thin launcher (CLI mode or web server startup)
- `api_server.py`: FastAPI app and API routes
- `assistant_core.py`: `JarvisAssistant` class and command handling logic
- `storage.py`: Memory normalization/persistence and utility helpers
- `constants.py`: Shared constants and configuration values

## Requirements

- Python 3.10+
- Windows (system automation commands are Windows-oriented)
- Working microphone and speakers
- Internet connection for speech recognition and Wikipedia/search features

## Setup

```powershell
cd "AI ML Project"
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Run

### Web mode

```powershell
cd "AI ML Project"
python main.py
```

- Starts FastAPI at `127.0.0.1:8000` (auto-falls to next free port up to `8019`)
- Opens browser UI at `/index.html`

### CLI voice mode

```powershell
cd "AI ML Project"
python main.py --cli
```

## Command Examples

- `wake up`
- `go to sleep`
- `what can you do`
- `daily briefing`
- `add task finish backend refactor`
- `show tasks`
- `complete task 1`
- `create macro startup: open chrome; show tasks; daily briefing`
- `run macro startup`
- `remember person rohit is backend engineer`
- `what do you know about rohit`
- `set project jarvis status integrating analytics`
- `show projects`
- `add note check API latency tomorrow`
- `show notes`
- `find file main`
- `search files for fastapi`
- `summarize file main.py`
- `copy to clipboard hello from jarvis`
- `show clipboard`
- `send email to test@example.com subject hello body this is a draft`
- `confirm`
- `cancel`

## API Endpoints

- `GET /api/health`
- `GET /api/state`
- `GET /api/analytics`
- `POST /api/reset-chat`
- `POST /api/command`
- `POST /api/speak`
- `POST /api/listen-command`
- `POST /api/sleep`
- `POST /api/wake`

## Notes

- Persistent data is saved in `AI ML Project/memory.json`.
- UI analytics cards are fed by backend analytics in `/api/state` and `/api/analytics`.
- File assistant operations are scoped to project files and skip large/system folders.
- Messaging flows open drafts for review; final send remains user-controlled.
