# Message Notification Router

An AI-powered routing system for WhatsApp messages built for the **HackerRank Orchestrate** challenge. For every incoming message it decides whether to `notify` (interrupt the user now), `digest` (queue for later), or `mute` (suppress).

The system handles text, image posters/screenshots, and voice notes. Routing is personalised per user using their engagement history, group membership, business relationships, and sender trust signals.

---

## How It Works

```
messages.csv + 11 context CSVs
        │
        ▼
 context_loader.py        — loads all dataset tables into O(1) indexed lookups
        │
        ▼
 signal_extractor.py      — rule-based pre-filter: scam OTPs, chain-forwards,
                            prompt-injection attempts, urgency, business trust
        │
  hard mute? ─────────────── yes ──▶ skip LLM, emit mute row
        │ no
        ▼
 media_handler.py         — encodes images as base64; describes voice notes
        │
        ▼
 llm_router.py            — builds structured context prompt, calls LLM,
                            parses and validates JSON response
   ┌────┴─────┐
 Ollama    Google
 (local)   Gemini 2.0 Flash
        │
        ▼
 main.py                  — orchestrates pipeline, writes dataset/output.csv,
                            runs post-write validation
```

### Routing decision logic

| Trigger | Decision |
|---|---|
| Prompt injection detected | **hard mute** / `scam` |
| OTP/account-block pressure + suspicious domain | **hard mute** / `scam` |
| Chain-forward + `forwarded_count ≥ 6` | **hard mute** / `forward` |
| Heavily-forwarded greeting user always dismisses | **hard mute** / `greeting` |
| Direct `@mention` + urgency keyword | **hard notify** / `urgent` |
| Everything else | LLM reasons over full structured context |

The LLM receives: message text, conversation type, forwarded count, user engagement stats (open/reply/dismiss/report rates, DND window), group metadata and the user's role/activity in that group, business verification + domain match, user-business relationship history, and the 5 most relevant historical messages with the user's interaction events (opened, replied, dismissed, reported). Four few-shot examples are included in the prompt.

---

## Repository Layout

```
.
├── AGENTS.md                        # AI coding tool rules + transcript logging spec
├── problem_statement.md             # Full challenge specification
├── README.md
├── .env.example                     # Environment variable template
├── .gitignore
├── code/
│   ├── main.py                      # Entry point — orchestrates the pipeline
│   ├── context_loader.py            # CSV ingestion + indexed lookups
│   ├── signal_extractor.py          # Rule-based signal extraction
│   ├── media_handler.py             # Image base64 encoding, voice note handling
│   ├── prompts.py                   # System prompt + few-shot examples
│   ├── llm_router.py                # Multi-backend LLM call + JSON parsing
│   ├── requirements.txt             # Python dependencies
│   └── README.md                    # Quick-start guide
└── dataset/
    ├── messages.csv                 # Input: 110 messages to route
    ├── output.csv                   # Output: predictions written here
    ├── sample_messages.csv          # Solved examples (ground truth for spot-checks)
    ├── users.csv
    ├── groups.csv
    ├── group_members.csv
    ├── business_accounts.csv
    ├── user_business_history.csv
    ├── message_history.csv
    ├── message_events.csv
    ├── images.csv
    ├── voice_notes.csv
    ├── daily_notification_summary.csv
    └── media/
        ├── images/
        └── audio/
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r code/requirements.txt
```

`google-genai` is needed only for the Google Gemini backend. If you are running Ollama locally it is optional.

### 2. Configure secrets

```bash
cp .env.example .env
# Edit .env — set GOOGLE_API_KEY and/or LLM_BACKEND
```

See `.env.example` for all available options.

### 3. (Optional) Start Ollama

If `LLM_BACKEND=ollama` or `auto`, the system will auto-detect Ollama at `localhost:11434`.

```bash
ollama serve
ollama pull llama3.2      # text-only messages
ollama pull llava:7b      # image messages (multimodal)
```

### 4. Run

```bash
python code/main.py
```

Output is written to `dataset/output.csv`.

---

## Output Schema

```
message_id, action, message_type, reason, confidence, evidence_message_ids
```

| Column | Values |
|---|---|
| `action` | `notify` · `digest` · `mute` |
| `message_type` | `personal` · `urgent` · `event` · `payment` · `business_update` · `promotion` · `greeting` · `forward` · `spam` · `scam` · `unknown` |
| `confidence` | float 0.0 – 1.0 |
| `evidence_message_ids` | semicolon-separated historical message IDs, or `none` |

---

## Notes

- **No secrets in source.** All credentials are read from environment variables or `.env` (which is git-ignored).
- **Quota-aware.** Hard-rule mutes skip the LLM entirely. A 1-second sleep is inserted every 15 requests to stay within free-tier Gemini rate limits.
- **Deterministic fallback.** If both backends fail, the system falls back to rule-only decisions so `output.csv` always has 110 rows.
- **Validated.** After writing `output.csv`, the pipeline checks that every `message_id` is present and all enum values are valid.
