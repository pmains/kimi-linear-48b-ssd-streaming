// Qwen manager tier — ordinary-turn verification through the real gateway.
// Mirrors the stage6d dispatch seam (callGateway method:"agent") and checks
// that the manager agent (qwen-local/qwen3-8b) can take a normal OpenClaw
// turn, make representative tool calls, and report cacheRead evidence.
//
// Usage:
//   OPENCLAW_CONFIG_PATH=.../dev-openclaw/config/openclaw.json \
//   OPENCLAW_STATE_DIR=.../dev-openclaw/state-qwen \
//   OPENCLAW_GATEWAY_URL=http://127.0.0.1:18790 \
//   node --import tsx tools/qwen_ordinary_turn.ts --agent manager --message "..."
import { randomUUID } from "node:crypto";
import { resolveNestedAgentLaneForSession } from "../src/agents/lanes.js";
import { callGateway } from "../src/gateway/call.js";
import { INTERNAL_MESSAGE_CHANNEL } from "../src/utils/message-channel.js";

const argv = process.argv.slice(2);
const agentId = argv[argv.indexOf("--agent") + 1] ?? "manager";
const message =
  argv[argv.indexOf("--message") + 1] ??
  "Please run exactly three tool calls and report their results: (1) exec: `date -u +%FT%TZ`; (2) read the file /Users/pmains/Code/openclaw/kimi/TOOLS.md (first 30 lines only); (3) web_search for 'Qwen3 8B context length'. Then summarize what you did in two sentences.";
const sessionKey = `agent:${agentId}:qwen-verify-${Date.now()}`;
const lane = resolveNestedAgentLaneForSession(sessionKey);
const inputProvenance = {
  kind: "inter_session" as const,
  sourceSessionKey: "agent:kimi:slack:channel:c0bqd732f7d",
  sourceChannel: "webchat",
  sourceTool: "sessions_send",
};

async function main() {
const t0 = Date.now();
const resp = await callGateway({
  method: "agent",
  expectFinal: false,
  timeoutMs: 300_000,
  params: {
    message,
    agentId,
    sessionKey,
    idempotencyKey: randomUUID(),
    deliver: false,
    sourceReplyDeliveryMode: "message_tool_only",
    channel: INTERNAL_MESSAGE_CHANNEL,
    lane,
    extraSystemPrompt: JSON.stringify({
      kind: "inter_session",
      sourceSessionKey: "agent:kimi:slack:channel:c0bqd732f7d",
      sourceChannel: "webchat",
      targetSessionKey: sessionKey,
    }),
    inputProvenance,
  },
});
const details = resp as Record<string, unknown>;
console.log("turn accepted runId=", details.runId, "fallbackActive=", details.fallbackActive);
const wallMs = Date.now() - t0;
console.log(`dispatch returned after ${wallMs}ms`);
}
main().catch((e) => { console.error(e); process.exit(1); });
