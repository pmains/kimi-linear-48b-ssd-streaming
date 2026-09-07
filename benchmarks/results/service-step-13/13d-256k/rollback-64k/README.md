# Rollback to the qualified 64K production state (Step 13D)

Qualified 64K state recorded 2026-09-07 (see
benchmarks/results/service-step-13/baseline-64k-20260907.json):
llama-server ctx 65536 (launchd job com.openclaw.kimi-llama-server), OpenClaw kimi-local +
llama-server provider contextWindow 65536, RSS ~8.8 GiB.

Artifacts in this dir:
- plist-installed-64k-20260907.plist  (installed launchd plist, KIMI_CTX=65536)
- plist-repo-64k-20260907.plist       (repo tools/ plist, KIMI_CTX=65536)
- openclaw.json backup: /Users/pmains/.openclaw/openclaw.json.bak-step13d-20260907 (sha256 efe1f7a29a96fce4af35e15ea57d1f4ccc744ce27c234f2466a5fb1a9daa0586)

Rollback procedure:
1. /usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:KIMI_CTX 65536" \
     /Users/pmains/Library/LaunchAgents/com.openclaw.kimi-llama-server.plist
   /usr/libexec/PlistBuddy -c "Set :EnvironmentVariables:KIMI_CTX 65536" \
     /Users/pmains/Code/openclaw/kimi/tools/com.openclaw.kimi-llama-server.plist
2. cp /Users/pmains/.openclaw/openclaw.json.bak-step13d-20260907 /Users/pmains/.openclaw/openclaw.json
3. launchctl bootout gui/501/com.openclaw.kimi-llama-server; sleep 2
4. launchctl bootstrap gui/501 /Users/pmains/Library/LaunchAgents/com.openclaw.kimi-llama-server.plist
5. Verify: curl http://127.0.0.1:18080/props -> n_ctx 65536; gateway
   healthz 200; a short agent turn reports contextTokens 65536 (resolved).
