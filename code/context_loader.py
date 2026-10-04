"""
context_loader.py
Loads all dataset CSV files into memory-indexed dictionaries for fast O(1) lookup.
"""

import os
import csv
from collections import defaultdict


def _read_csv(path: str) -> list[dict]:
    """Read a CSV file and return list of row dicts."""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


class DataContext:
    """Holds all dataset tables indexed for fast lookup."""

    def __init__(self, dataset_dir: str):
        base = dataset_dir

        # Raw tables
        self.messages = _read_csv(os.path.join(base, "messages.csv"))
        self.sample_messages = _read_csv(os.path.join(base, "sample_messages.csv"))
        self.users = _read_csv(os.path.join(base, "users.csv"))
        self.groups = _read_csv(os.path.join(base, "groups.csv"))
        self.group_members = _read_csv(os.path.join(base, "group_members.csv"))
        self.business_accounts = _read_csv(os.path.join(base, "business_accounts.csv"))
        self.user_business_history = _read_csv(os.path.join(base, "user_business_history.csv"))
        self.message_history = _read_csv(os.path.join(base, "message_history.csv"))
        self.message_events = _read_csv(os.path.join(base, "message_events.csv"))
        self.images = _read_csv(os.path.join(base, "images.csv"))
        self.voice_notes = _read_csv(os.path.join(base, "voice_notes.csv"))
        self.daily_notification_summary = _read_csv(os.path.join(base, "daily_notification_summary.csv"))

        # Indexes
        self.user_by_id: dict[str, dict] = {r["user_id"]: r for r in self.users}
        self.group_by_id: dict[str, dict] = {r["group_id"]: r for r in self.groups}
        self.business_by_id: dict[str, dict] = {r["business_id"]: r for r in self.business_accounts}
        self.image_by_id: dict[str, dict] = {r["image_id"]: r for r in self.images}
        self.voice_note_by_id: dict[str, dict] = {r["voice_note_id"]: r for r in self.voice_notes}

        # group_members: (group_id, user_id) -> membership row
        self.group_member_by_key: dict[tuple, dict] = {
            (r["group_id"], r["user_id"]): r for r in self.group_members
        }

        # user_business_history: (user_id, business_id) -> history row
        self.ubh_by_key: dict[tuple, dict] = {
            (r["user_id"], r["business_id"]): r for r in self.user_business_history
        }

        # message_history indexed by user_id -> list of history rows
        self.history_by_user: dict[str, list] = defaultdict(list)
        for r in self.message_history:
            self.history_by_user[r["user_id"]].append(r)

        # message_history indexed by message_id -> row (for evidence lookup)
        self.history_by_msg_id: dict[str, dict] = {r["message_id"]: r for r in self.message_history}

        # message_events indexed by (user_id, message_id) -> event row
        self.events_by_key: dict[tuple, dict] = {
            (r["user_id"], r["message_id"]): r for r in self.message_events
        }

        # message_events indexed by user_id -> list of events
        self.events_by_user: dict[str, list] = defaultdict(list)
        for r in self.message_events:
            self.events_by_user[r["user_id"]].append(r)

        self.dataset_dir = dataset_dir

    def get_user(self, user_id: str) -> dict:
        return self.user_by_id.get(user_id, {})

    def get_group(self, group_id: str) -> dict:
        return self.group_by_id.get(group_id, {})

    def get_business(self, business_id: str) -> dict:
        return self.business_by_id.get(business_id, {})

    def get_group_membership(self, group_id: str, user_id: str) -> dict:
        return self.group_member_by_key.get((group_id, user_id), {})

    def get_user_business_history(self, user_id: str, business_id: str) -> dict:
        return self.ubh_by_key.get((user_id, business_id), {})

    def get_relevant_history(self, user_id: str, group_id: str, business_id: str,
                              sender_user_id: str, limit: int = 5) -> list[dict]:
        """
        Return the most relevant historical messages for context.
        Priority: same group/sender > same business > same user.
        Returns list of (history_row, event_row) tuples.
        """
        candidates = []

        for hist in self.history_by_user.get(user_id, []):
            score = 0
            if group_id and hist.get("group_id") == group_id:
                score += 3
            if business_id and hist.get("business_id") == business_id:
                score += 3
            if sender_user_id and hist.get("sender_user_id") == sender_user_id:
                score += 2
            if score > 0:
                event = self.events_by_key.get((user_id, hist["message_id"]), {})
                candidates.append((score, hist, event))

        # Sort by score descending, then take top limit
        candidates.sort(key=lambda x: x[0], reverse=True)
        return [(h, e) for _, h, e in candidates[:limit]]

    def get_image_path(self, media_id: str) -> str | None:
        img = self.image_by_id.get(media_id)
        if img:
            return os.path.join(self.dataset_dir, img["file_path"])
        return None

    def get_voice_note_path(self, media_id: str) -> str | None:
        vn = self.voice_note_by_id.get(media_id)
        if vn:
            return os.path.join(self.dataset_dir, vn["file_path"])
        return None
