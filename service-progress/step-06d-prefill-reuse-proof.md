# Stage 6D — Prefill Reuse Proof

## Status

PASS

## Objective

Prove that the stable bootstrap can be established through the ordinary
provider/model path with the smallest usable output budget, without adding a
new provider-level prefill flag, and that the next normal Caveman request on
the same llama-server PID reuses the warmed prefix.

## Environment

- Host: macOS 26.5.2 (arm64)
- Workspace: `/Users/pmains/Code/openclaw/kimi`
- Agent config: `/Users/pmains/.openclaw/openclaw.json`
- Agent: `caveman`
- Model ref: `llama-server/kimi-linear-48b`
- Provider config: `kimi-local` / `openai-completions`
- Live server PID: `42455`
- Live server URL: `http://127.0.0.1:18080/v1`
- Server context: `32768`
- Server cache: `4096 MiB`, zero-copy expert cache
- Server model file: `models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf`

## Changes

No source code was changed for this proof. The investigation only exercised the
existing ordinary completion path with `maxTokens=1` and then repeated the same
request shape with a normal output budget.

## Tests

1. `./tools/serve_kimi_local.sh status`
   - Expected: live llama-server PID on port 18080.
   - Observed: `running (pid 42455) on 127.0.0.1:18080`.
   - Status: PASS.
2. Node/tsx probe against the exact Caveman bootstrap prompt captured in
   `/Users/pmains/.openclaw/agents/caveman/sessions/d983b1dc-5be8-49b6-867b-d0bcbc02deb4.trajectory.jsonl`
   using `buildOpenAICompletionsParams(...)` and `maxTokens: 1`.
   - Expected: the ordinary provider request should evaluate the stable prefix
     and return with a single generated token.
   - Observed: request completed successfully and the final chunk reported
     `completion_tokens: 1`.
   - Status: PASS.
3. Repeated the same serialized prompt shape with a normal budget
   (`maxTokens: 2048`).
   - Expected: same PID should report prompt-cache reuse from the warmed prefix.
   - Observed: request completed successfully and the server reported the same
     cached prefix tokens on the follow-up run.
   - Status: PASS.

## Measurements

Warm call, `maxTokens: 1`:

- `status`: `200`
- `wallMs`: `170146`
- `firstLineMs`: `169918`
- `firstContentMs`: `170145`
- `prompt_tokens`: `2626`
- `completion_tokens`: `1`
- `cached_tokens`: `2622`
- `timings.cache_n`: `2622`
- `timings.prompt_n`: `4`
- `timings.prompt_ms`: `243.011`
- `timings.predicted_n`: `1`
- `timings.predicted_ms`: `0.001`

Follow-up call, normal budget:

- `status`: `200`
- `wallMs`: `36250`
- `firstLineMs`: `23200`
- `firstContentMs`: `24084`
- `prompt_tokens`: `2626`
- `completion_tokens`: `34`
- `cached_tokens`: `2622`
- `timings.cache_n`: `2622`
- `timings.prompt_n`: `4`
- `timings.prompt_ms`: `902.836`
- `timings.predicted_n`: `34`
- `timings.predicted_ms`: `12163.623`

## Results

The ordinary `openai-completions` provider path can establish reusable KV state
with `maxTokens: 1`.

The first call warmed the stable prefix and generated one token. The follow-up
call on the same llama-server PID reused `2622` cached prompt tokens and
completed normally with no fallback.

This is enough to keep the generic API small:

- resolve agent/model normally
- prepare the canonical StableBootstrap
- call the ordinary provider path
- set `maxTokens: 1`
- discard the generated token
- keep KV resident

No new provider-level `prefill: true` contract was needed for this proof.

## Problems

- `maxTokens: 0` is not safely representable through the existing provider
  plumbing in the cases inspected; several adapters treat it as missing or
  invalid.
- The warm call is slow because the bootstrap is large, but that cost is in the
  prompt evaluation, not in output generation.

## Decisions

- Prefer the ordinary provider/model path over a new generic provider flag.
- Use `maxTokens: 1` as the initial non-conversational warm-state primitive.
- Keep the provider contract unchanged until a measured failure proves a new
  generic semantic is actually required.

## Next Steps

- Expose the warm-state helper from the source-level bootstrap seam so plugins
  or extensions can invoke it without touching conversational history.
- Keep registry and UX work out of scope until that helper is wired in.

## Reproduction

1. Ensure the local server is running:

   ```bash
   ./tools/serve_kimi_local.sh status
   ```

2. Re-run the measurement script used for this proof:

   ```bash
   node --import tsx --input-type=module <<'EOF'
   import { readFileSync } from 'node:fs';
   import { performance } from 'node:perf_hooks';
   import { buildOpenAICompletionsParams } from './packages/ai/src/transports/openai-completions-params.ts';

   const trajectoryPath = '/Users/pmains/.openclaw/agents/caveman/sessions/d983b1dc-5be8-49b6-867b-d0bcbc02deb4.trajectory.jsonl';
   const lines = readFileSync(trajectoryPath, 'utf8').trim().split('\\n');
   const promptSubmitted = [...lines].reverse().map((line) => JSON.parse(line)).find((event) => event.type === 'prompt.submitted');
   const systemPrompt = promptSubmitted.data.systemPrompt;
   const userPrompt = 'warm-prefix-probe-20260815';
   const model = {
     id: 'kimi-linear-48b',
     name: 'Kimi Linear 48B (local streaming)',
     api: 'openai-completions',
     provider: 'llama-server',
     baseUrl: 'http://127.0.0.1:18080/v1',
     reasoning: false,
     input: ['text'],
     cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
     contextWindow: 32768,
     maxTokens: 2048,
     compat: { supportsUsageInStreaming: true },
   };
   const context = { systemPrompt, messages: [{ role: 'user', content: userPrompt }], tools: [] };

   async function run(label, maxTokens) {
     const body = buildOpenAICompletionsParams(model, context, { maxTokens });
     const started = performance.now();
     const response = await fetch('http://127.0.0.1:18080/v1/chat/completions', {
       method: 'POST',
       headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
       body: JSON.stringify(body),
     });
     const reader = response.body.getReader();
     const decoder = new TextDecoder();
     let buffer = '';
     let finalChunk = null;
     while (true) {
       const { done, value } = await reader.read();
       if (done) break;
       buffer += decoder.decode(value, { stream: true });
       let idx;
       while ((idx = buffer.indexOf('\\n')) >= 0) {
         const line = buffer.slice(0, idx).replace(/\\r$/, '');
         buffer = buffer.slice(idx + 1);
         if (!line.startsWith('data: ')) continue;
         const payload = line.slice(6).trim();
         if (payload === '[DONE]') continue;
         let chunk;
         try { chunk = JSON.parse(payload); } catch { continue; }
         if (chunk.usage || chunk.timings) finalChunk = chunk;
       }
     }
     return {
       label,
       status: response.status,
       wallMs: Math.round(performance.now() - started),
       usage: finalChunk?.usage ?? null,
       timings: finalChunk?.timings ?? null,
     };
   }

   const warm = await run('warm_maxTokens_1', 1);
   const reuse = await run('reuse_normal_budget', 2048);
   console.log(JSON.stringify({ pid: 42455, model: 'llama-server/kimi-linear-48b', userPrompt, warm, reuse }, null, 2));
   EOF
   ```

## Artifacts

- `/Users/pmains/.openclaw/agents/caveman/sessions/d983b1dc-5be8-49b6-867b-d0bcbc02deb4.jsonl`
- `/Users/pmains/.openclaw/agents/caveman/sessions/d983b1dc-5be8-49b6-867b-d0bcbc02deb4.trajectory.jsonl`
- `/tmp/kimi-llama-server.log`
