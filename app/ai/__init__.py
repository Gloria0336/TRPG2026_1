"""AI Orchestrator — intent parsing + narration via OpenRouter (design §8).

Hard rule (§4.0 / §8.0): the AI fills structured intent slots and writes prose. It
never decides success/failure, never sets a free DC, never touches a number. Every AI
output is validated; out-of-schema responses are rejected.
"""
