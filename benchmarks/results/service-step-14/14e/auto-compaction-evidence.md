# 14E auto-compaction evidence (2026-09-09 06:56 UTC / 22:56 MST)

During build leg B13 (session agent:poliscopic:step13g-kg-maintenance-r1), at accumulated context ~121.8K tokens, OpenClaw's embedded-agent layer auto-compacted the session:

125337:2026-09-07T19:57:30.059-07:00 [agent/embedded] auto-compaction succeeded for deepseek/deepseek-v4-flash; retrying prompt
129990:2026-09-08T12:35:11.818-07:00 [agent/embedded] auto-compaction succeeded for deepseek/deepseek-v4-flash; retrying prompt
132599:2026-09-08T22:56:20.988-07:00 [agent/embedded] auto-compaction succeeded for llama-server/kimi-linear-48b; retrying prompt

Compaction config (openclaw.json agents/defaults/compaction):
{
 "keepRecentTokens": 50000,
 "notifyUser": true,
 "timeoutSeconds": 600,
 "mode": "default",
 "midTurnPrecheck": {
  "enabled": true
 },
 "maxActiveTranscriptBytes": "8mb"
}
