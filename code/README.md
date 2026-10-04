# code/

Python implementation of the Message Notification Router.

## Quick start

```bash
# from the repo root
pip install -r code/requirements.txt
cp .env.example .env          # set GOOGLE_API_KEY and/or LLM_BACKEND
python code/main.py           # writes dataset/output.csv
```

## Files

| File | Responsibility |
|---|---|
| `main.py` | Pipeline entry point — loads context, iterates messages, writes and validates output |
| `context_loader.py` | Reads all 11 dataset CSVs into a `DataContext` with O(1) indexed lookups |
| `signal_extractor.py` | Rule-based pre-filter: returns structured signals (scam, chain-forward, injection, urgency, group mute, business trust) and hard-decision flags |
| `media_handler.py` | Encodes images as base64 for vision models; describes voice notes by filename/size |
| `prompts.py` | System prompt defining routing rules + four few-shot examples |
| `llm_router.py` | Multi-backend LLM client (Ollama or Google Gemini); builds per-message prompt, calls LLM, parses and validates JSON |
| `requirements.txt` | Python dependencies |

## Backend selection

Set `LLM_BACKEND` in your `.env` (or shell):

| Value | Behaviour |
|---|---|
| `auto` (default) | Try Ollama → fall back to Google Gemini |
| `ollama` | Ollama only — uses `llava:7b` for image messages, `llama3.2` for text |
| `google` | Google Gemini 2.0 Flash — requires `GOOGLE_API_KEY` |

## Signal extraction quick reference

`signal_extractor.extract_signals()` returns a flat dict. Key boolean flags:

- `hard_mute` / `hard_notify` — skip LLM, emit directly
- `is_scam_otp` — OTP/account-block pressure keywords detected
- `is_chain_forward` — chain-forward instruction pattern
- `is_prompt_injection` — message tries to override router behaviour
- `business_domain_mismatch` — sender domain ≠ business's official domain
- `business_high_reports` — ≥ 20 user reports in last 30 days
- `user_opted_out` — user previously unsubscribed from this business
- `group_muted_by_user` — user has muted the group
