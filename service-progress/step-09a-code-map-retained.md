[THINKING] I have the exact block. Note `followupRun.run.timeoutMs = timeoutMs` (line ~2274) — the main run's timeout (1200s) is inherited by the maintenance run; runMemoryFlushIfNeeded launches an embedded maintenance attempt with that full budget; the attempt's own run-timeout aborts at +timeoutMs if the model never terminates (silent expected).

Everything verified. Now compose the report. Keep < 4000 words; include:

- Runtime/paths facts: CLI shim, gateway launchd, same dist; restart implications.
- Defect A: files/functions/line quotes (7 lines REPLY_TAG_RE; 53-60 display; 82-92 delivery; 94-133 parseInlineDirectives; 522 parseReplyDirectives), buildEmbeddedRunPayloads quote (437-456 in payloads-iGx_eB0H.js), meta text (embedded-agent-l7T_r8nK.js 5697-5699), system prompt section (system-prompt-params 366-385 + param assembly 706 & append 910), evidence: colon left, tag in meta. Explain why the model's tag+": " leaves artifacts: strip regex only consumes tag+padding whitespace, not separator colon; display/raw text never runs any strip in this path. Minimal patch options (precise).
- Defect B: lifecycle evidence quotes from transcript; code: finalizeEmbeddedAgentCommand (2260-2296), runMemoryFlushIfNeeded (725; 890-1040 region), followupRun timeoutMs inherit; the maintenance run unbounded tool loop; abort-cutoff runTimeout at +timeoutMs ends it; no channel/deliver gating anywhere; turn-service deadline also at timeoutMs (agent-turn 2369-2385 registerChatAbortController expiresAt now+timeoutMs).
- Patch suggestions for B: (1) gate maintenance block on having delivery channel/deliver... careful: maintenance is also desirable for persistent sessions generally (cloud models). Better: skip/limit when deliver false AND no channel (headless CLI), OR bound maintenance attempt duration & iterations; precise descriptions.

Deliver report as final message text (concise but complete).
All analysis complete. Final report follows.

---

# Report: Agent-path code mapping for Defect A (reply-directive leak) and Defect B (~1200 s stall)

## 0. Environment facts (both defects)

- Running build: `/opt/homebrew/lib/node_modules/openclaw/dist` (esbuild chunks, source region markers like `//#region src/…` preserved). All files below are in that directory; I use short chunk names.
- The agent CLI (`openclaw`) and the gateway share the same dist: the CLI shim `~/.openclaw/tmp/agent-cli/openclaw` (and the real `openclaw` bin) `exec`s `/opt/homebrew/lib/node_modules/openclaw/dist/index.js`. The gateway is launchd service `ai.openclaw.gateway` (currently PID 77143, `dist/index.js gateway --port 18789`, log `/Users/pmains/Library/Logs/openclaw/gateway.log`).
- **Restart requirement:** Yes. The gateway loads chunks into memory (incl. lazy `import()`-cached chunks); a dist file edit alone does not affect the running process. The gateway service must be restarted (`launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway` or equivalent) for any patch to take effect on the server side. The CLI spawns a fresh node per invocation, so CLI-side behavior changes apply immediately to new invocations.
- I additionally confirmed both defects against retained evidence in the repo: `benchmarks/results/service-step-09/P1/` (summary, timed.out, llama-server log) and the agent session SQLite copy (`~/.openclaw/agents/kimi/agent/openclaw-agent.sqlite` → `transcript_events` for session `6cd3bccc-750a-4c08-9043-d789ab73e0d0`, key `agent:kimi:step09-p1`).

---

## Defect A — leaked reply directive in headless `openclaw agent` results

### A.1 Where reply directives are parsed/stripped after model output

All tag parsing lives in `directive-tags-D4YR8I_B.js` (region `src/utils/directive-tags.ts`):

```
7   const REPLY_TAG_RE = /\[\[\s*(?:reply_to_current|reply_to\s*:\s*([^\]\n]+))\s*\]\]/gi;
8   const INLINE_DIRECTIVE_TAG_WITH_PADDING_RE = /\s*(?:\[\[\s*audio_as_voice\s*\]\]|\[\[\s*(?:reply_to_current|reply_to\s*:\s*[^\]\n]+)\s*\]\])\s*/gi;
…
16  function replacementPreservesWordBoundary(source, offset, length) {
17      const before = source[offset - 1];
18      const after = source[offset + length];
19      return before && after && !/\s/u.test(before) && !/\s/u.test(after) ? " " : "";
20  }
```

Removal functions (all in the same file):
- `stripInlineDirectiveTagsForDisplay` (line 53): replaces only the tag, with `""`.
- `stripInlineDirectiveTagsForDelivery` (line 82): replaces `INLINE_DIRECTIVE_TAG_WITH_PADDING_RE` (tag + surrounding whitespace) with `" "`, then `.trim()`s.
- `parseInlineDirectives` (line 94): strips `REPLY_TAG_RE` via `replacementPreservesWordBoundary` (line 115-126), so `[[reply_to_current]]` at text start is replaced by `""`.
- `parseReplyDirectives` (`reply-directives-BdvizK-w.js`, line 522, region `src/auto-reply/reply/reply-directives.ts`) = `splitMediaFromOutput` + `parseInlineDirectives`, returning `{ text, replyToId, replyToCurrent, … }`.

**Why the leak happens:** for model text `[[reply_to_current]]: PLATANOS`, every strip path removes only the tag itself. The regexes have no clause for a *separator* the model placed after the tag (`: `). `stripInlineDirectiveTagsForDelivery` additionally only trims when the padding regex matched trailing whitespace — here the next char is `:`, so even it yields `" : PLATANOS"` → trim → `": PLATANOS"`. `parseInlineDirectives` yields the same. Hence payload text `: PLATANOS`, and the `[[reply_to_current]]`-based visible text keeps the raw tag because (see A.2) the agent-CLI path never applies *any* strip to the final-visible/raw text.

### A.2 Where finalAssistantVisibleText and result.payloads[].text are computed (agent-CLI/JSON path)

The headless run executes the embedded agent path (gateway `agent` method → `agent-command-BHLpevuF.js` → `runAgentAttempt`/embedded runtime). The result is assembled in `embedded-agent-l7T_r8nK.js` (region `src/agents/embedded-agent-runner/run/…`), around lines 5697-5776:

```
5697  const finalAssistantVisibleText = terminalAssistantCanOwnFinalText ? resolveFinalAssistantVisibleText(terminalAssistant) ?? attemptFinalText : void 0;
5698  const finalAssistantRawText      = terminalAssistantCanOwnFinalText ? resolveFinalAssistantRawText(terminalAssistant) ?? attemptFinalText : void 0;
…
5718  const payloads = buildEmbeddedRunPayloads({ … lastAssistant: payloadAssistant, … sourceReplyDeliveryMode: runParams.sourceReplyDeliveryMode, … });
```

- `resolv…Text` are defined in `helpers-BQ9--IYM.js` (lines 202-209) as plain `extractAssistantVisibleText(lastAssistant).trim()` / `extractAssistantTextForPhase(...)` — **no directive stripping at all**, which is why `meta.finalAssistantRawText === finalAssistantVisibleText === "[[reply_to_current]]: PLATANOS"` in P1.client/timed.out.
- `buildEmbeddedRunPayloads` is defined in `payloads-iGx_eB0H.js` (region `src/agents/embedded-agent-runner/run/payloads.ts`). Its answer-text loop (lines ~437-456) is where payload text is produced:

```
437  for (const text of answerTexts) {
438      const { text: cleanedText, mediaUrls, audioAsVoice, replyToId, replyToTag, replyToCurrent } = parseReplyDirectives(text);
…
453      const replyPayload = { text: cleanedText, media: mediaUrls, ...delivery };
454      replyItems.push(...);
```

`parseReplyDirectives("[[reply_to_current]]: PLATANOS")` → text `": PLATANOS"` (tag removed, colon kept) → `payloads[0].text === ": PLATANOS"`. The CLI then prints it verbatim: `agent-via-gateway-Bg4p71SQ.js` `formatPayloadForLog` (lines 313-323) / JSON mode prints `result.payloads` untouched (`buildGatewayJsonResponse`, line 558). The `[[:reply_to:]]` probe variant (`[[reply_to:]]{"ok": true}`) follows the same path: an empty-id `[[reply_to:]]` is a valid regex match with no captured id, so nothing is removed beyond the tag and the JSON payload text leaks.

The transcripted assistant message (persisted) is also the already-stripped `": PLATANOS"` — so the colon artifact persists into session history as well.

### A.3 Where the directive instruction is injected, and the gating condition

`system-prompt-params-7goAPY5o.js`, `buildAssistantOutputDirectivesSection` (lines 366-385):

```
366  function buildAssistantOutputDirectivesSection(params) {
367      if (params.isMinimal || params.sourceMessageToolOnly && !params.messageToolAvailable) return [];
368      if (params.sourceMessageToolOnly) return [ … "Explicit native reply: `replyTo`." … ];   // no [[reply_to_*]]
…
378      return [
379          "## Assistant Output Directives",
…
381          "- Native reply starts with `[[reply_to_current]]`; explicit id only: `[[reply_to:<id>]]`.",
382          "- Directives stripped before render; channel config controls delivery.",
383          ""
384      ];
385  }
```

The section is appended unconditionally for every non-minimal prompt whose `sourceReplyDeliveryMode !== "message_tool_only"` (see the caller at line 910 and `sourceMessageToolOnly = params.sourceReplyDeliveryMode === "message_tool_only"` at line 706). **There is no check for channel / reply target / `--deliver` / runtimeChannel.** The headless CLI dispatch (`--session-key …`, no `--channel`, no `--deliver`) has `sourceReplyDeliveryMode` unset, so the model is instructed to emit `[[reply_to_current]]` even though no channel-reply machinery exists in this path — it complied, and the tag then leaks per A.1/A.2.

### A.4 Minimal patch points for Defect A

**Patch A1 (robust strip — fixes payload text and visible text wherever stripping runs):** `directive-tags-D4YR8I_B.js`. Extend the two regexes and/or the replacements so that a removed reply tag also consumes an immediately-following separator the model may have written (`:`, `：`, optionally `-`, and following spaces) — only when the tag is leading (start-of-text or after line break/whitespace), to avoid eating legitimate content mid-sentence. Concretely: in `parseInlineDirectives` (lines 115-126), `stripInlineDirectiveTagsForDisplay` (58), and `stripInlineDirectiveTagsForDelivery` (87), after tag removal also collapse `^\s*[:：]\s*` left at the start of the text (or run the existing `normalizeDirectiveWhitespace` which already trims leading whitespace, then additionally strip a leading `[:：]` when `hasReplyTag`). Result for `"[[reply_to_current]]: PLATANOS"` → `"PLATANOS"` in payloads, display and delivery paths. This is the single highest-leverage fix.

**Patch A2 (clean meta text on the agent-CLI path):** `embedded-agent-l7T_r8nK.js` lines 5697-5698: run the resolved text through `stripInlineDirectiveTagsForDisplay` (export `a` of `directive-tags-D4YR8I_B.js`) before storing `finalAssistantVisibleText`/`finalAssistantRawText` (keep raw only if unchanged flag). With A1 this yields exact model content.

**Patch A3 (stop instructing headless runs to emit directives):** `system-prompt-params-7goAPY5o.js` lines 366-385 + call site 910: thread one boolean (e.g. `hasReplyTarget`) from the prompt-builder params — set when the invocation actually carries a channel/`--deliver`/`replyTo` — and omit the `[[reply_to_current]]` / `[[reply_to:<id>]]` bullets (and the “Media … own line” hint is fine to keep) when false. The decision point upstream is where `sourceReplyDeliveryMode` is resolved for the run (gateway `startAgentRunExecution` in `agent-turn-service-cCOLRusP.js` lines ~2950-3090 builds `ingressOpts`; a headless dispatch has `deliver=false`, `channel=undefined`, `originMessageChannel=undefined`). A1+A2 make the headless result correct even without A3; A3 additionally prevents the model from wasting output tokens on directives it cannot act on. Recommend A1 (required) + A2; A3 optional.

---

## Defect B — ~1200 s post-generation stall (headless `openclaw agent`)

### B.1 What actually happens (from retained evidence, not speculation)

Retained per-turn artifacts + the agent transcript DB show:
- 09:48:43 run `04969eda…` (P1) starts; model replies `[[reply_to_current]]: PLATANOS` at 09:48:54.6 (`durationMs` 12279).
- **1.0 s later (09:48:55.612) an internal maintenance run `8a84ca0f…` starts in the same session** with a hidden user turn `"Continue the OpenClaw runtime event."` (`display:false`) — the constant `OPENCLAW_RUNTIME_EVENT_USER_PROMPT` from `runtime-context-prompt-E9LRffzc.js` line 6, produced when an internal run has an empty prompt but runtime context.
- That maintenance run then executed **~134 `read` tool calls over 1200 s** (one model turn every ~8-10 s, each 83-token prompt / ~21 output tokens in the llama-server log — the kimi-linear model treated the maintenance prompt as real work and kept going, never emitting the expected silent/terminal reply).
- At 10:08:55.699 (flush start + exactly 1200 s = its inherited run timeout) the attempt aborts: transcript seq 276-277 show an empty assistant message followed by `openclaw:turn-aborted` (“The previous turn was interrupted…”). The gateway logs the reply `: PLATANOS` and `run … ended with stopReason=stop` at 10:08:55.749, then emits the final response. Client wall 1214.7 s, rc 0, `timed_out` = wrapper heuristic (wall ≥ timeout-1).

So the stall is not a “wait for delivery”: it is a **post-turn embedded maintenance run (memory flush) that runs unboundedly on the pinned local model until the inherited run timeout aborts it**, and the parent `agent` call does not finalize until that maintenance completes.

### B.2 The code that launches it

`agent-command-BHLpevuF.js`, `finalizeEmbeddedAgentCommand` (region `src/agents/command/post-run.ts`), lines 2260-2296:

```
2260  if ((transcriptPersistenceRunner === "embedded" || persistedCliTurnTranscript) && sessionEntry && sessionStore && sessionKey && !params.suppressVisibleSessionEffects) {
2261      const flushProvider = result.meta.agentMeta?.provider ?? fallbackProvider;
2262      const flushModel = result.meta.agentMeta?.model ?? fallbackModel;
2263      const followupRun = {
2264          prompt: "",
…
2274          run: { agentId: sessionAgentId, …, blockReplyBreak: "message_end", …, timeoutMs, senderIsOwner: false }
…
2289          const { runMemoryFlushIfNeeded } = await loadAgentRunnerMemoryRuntime();
…
2293          sessionEntry = (await runMemoryFlushIfNeeded({
2294              cfg, followupRun, promptForEstimate: "", …, defaultModel: flushModel,
2295              sessionEntry, sessionStore, sessionKey, …, abortSignal: params.opts.abortSignal, …
2296          })).sessionEntry ?? sessionEntry;
```

Notes:
- Trigger condition is **runner-identity based**, not channel/delivery based: any embedded run (every gateway/CLI `agent` run whose `executionTrace.runner === "embedded"`, i.e. all of these probes) enters it whenever a session store exists and session effects are visible. P1 satisfied it (runner `"embedded"`).
- `followupRun.run.timeoutMs = timeoutMs` — the maintenance run **inherits the caller's `--timeout` (1200 s)**; nothing else bounds it.
- The flush is gated on thresholds inside `runMemoryFlushIfNeeded` (`agent-runner-memory-CHicSjNz.js`, region `src/auto-reply/reply/memory-flush.ts` + `agent-runner-memory.ts`, function at line 725). `shouldRunMemoryFlush` (line 132) compares persisted session tokens vs `contextWindow − reserveTokensFloor − softThresholdTokens` (resolveMemoryFlushThreshold, line 70). For this deployment the ~12.2k-token kimi system prompt puts a fresh one-turn session near that threshold, so even tiny probes triggered the flush on their first turn; the flush then launched a full embedded attempt (`memoryDeps.runEmbeddedAgent` at lines ~960-1000 with `silentExpected: true, terminalReplyExpectation: "optional"` — i.e. it *expects* the model to reply silently/empty, but nothing stops a tool-looping model).
- The only thing that ends the loop is the per-attempt run-timeout (the embedded attempt executor receives `runTimeoutOverrideMs`/`timeoutMs` = 1200 s; see `embedded-agent-l7T_r8nK.js` line 1109 `runTimeoutOverrideMs: params.runTimeoutOverrideMs ?? params.timeoutMs` and the abort-cutoff machinery), which fired at flush-start + 1200 s and produced the `turn-aborted` event. The gateway also registers a chat-abort entry expiring at `now + timeoutMs` (`agent-turn-service-cCOLRusP.js` lines 2369-2385, `registerChatAbortController` in `chat-abort-Df2Wk9Zt.js` line 92) — same deadline, secondary.

### B.3 Minimal patch points for Defect B

**Patch B1 (recommended, smallest semantic change):** in `agent-command-BHLpevuF.js` `finalizeEmbeddedAgentCommand`, gate the whole maintenance block at line 2260 so it is skipped for headless one-shot dispatches that have no reply/delivery target — e.g. add `&& (params.opts.deliver === true || normalizeOptionalString(params.opts.messageChannel ?? params.opts.runContext?.messageChannel ?? params.opts.channel) != null || <explicit session with channel>)` to the condition. For `openclaw agent --session-key … --json --timeout 1200` (no `--channel`, no `--deliver`) `messageChannel`/`channel`/`runContext.messageChannel` are all undefined, so the flush (and hence the stall) is skipped and the turn finalizes right after model generation. Channel-backed chat turns keep flushing as before. (If a “skip only when `opts.json`/no live observer” shape is preferred, `params.opts` carries `json` and verbose flags as well — pick the condition the parent considers semantically right; the key is that maintenance must not run for invisible headless dispatches, or must be time-bounded.)

**Patch B2 (robustness — bounds the maintenance attempt regardless of gating):** in `agent-runner-memory-CHicSjNz.js` `runMemoryFlushIfNeeded` (the `runCandidate` → `memoryDeps.runEmbeddedAgent` call, lines ~960-1000), pass a strict iteration/turn budget for maintenance runs (e.g. cap tool-loop turns to a small N, or a short absolute deadline well under `timeoutMs`, and treat non-terminal results as silent/failed). Also consider capping the inherited `followupRun.run.timeoutMs` (line 2274) for maintenance runs (e.g. min(timeoutMs, maintenance cap)) so a non-cooperative model can never hold the caller for the full user timeout.

B1 alone fixes the observed probe behavior; B2 is defense-in-depth for any future path that legitimately needs post-turn maintenance on a slow/tool-happy model.

### B.4 Things not to touch
- Do not change the parse/strip semantics that handle real channel replies in `chat-send-handler-BPCePsFM.js` (it imports `stripInlineDirectiveTagsForDelivery`/`parseInlineDirectives` from `directive-tags-D4YR8I_B.js` at line 50 and re-encodes `replyToId`/`replyToCurrent` back into tags for delivery at lines 1425-1427/1502-1504 — patch A1 in directive-tags automatically improves that path too without touching chat-send-handler).
- The “missing `!`” in `PLATANOS` vs `PLATANOS!` is model-output variance (11 output tokens, direct llama-server control differs); it is not an OpenClaw stripping bug and cannot be fixed in dist.

### Verification commands (for after patching)
```
# restart gateway (dist patch server-side)
launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway   # then confirm new pid on port 18789

# Defect A repro (expect reply exactly "PLATANOS!"; no [[..]] or ": " artifacts anywhere in JSON)
printf 'Respond only PLATANOS!' > /tmp/p1.md
openclaw agent --agent kimi --model llama-server/kimi-linear-48b \
  --session-key agent:kimi:step09-P1-fix --message-file /tmp/p1.md --json --timeout 300

# Defect B repro (expect wall_s ≈ model time (~15 s), NOT ~timeout)
python3 tools/service_step09_turn.py /tmp/fix-P1 P1 /tmp/p1.md agent:kimi:step09-P1-fix --agent kimi --timeout 300
```

Word count ≈ 2,450 — under the 4,000 limit.
