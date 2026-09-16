# Deskline
An inbound phone menu system built with Python FastAPI and Telnyx Voice API. Routes customers through DTMF keypad prompts, plays business information via text-to-speech, and collects menu decisions. Handles state across multiple webhook events with SQLite persistence, webhook signature verification, and deterministic call testing before going live.
