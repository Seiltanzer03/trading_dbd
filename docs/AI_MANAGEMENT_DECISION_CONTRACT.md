# AI management decision contract

`POST /api/ai/verdict` returns the same structured `management_decision` for LLM success and deterministic fallback. Provider availability cannot remove the execution workflow. CLOSE_10/25/50 and EXIT require manual confirmation. The backend captures market price and R and applies an idempotent event.
