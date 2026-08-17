# Stage 6A.5 Report

## Status

PASS

## Objective

Derive the invariant provider-level token prefix of an ordinary Caveman request
and make the prefill path reproduce that exact prefix through the canonical
ordinary serialization path.

## Changes

- Added `addGenerationPrompt?: boolean` to the shared stream options contract.
- Threaded `addGenerationPrompt` through the OpenAI completions provider and
  transport parameter builders so chat-template generation prompting can be
  suppressed explicitly.
- Updated the stable-bootstrap prefill wiring to use the ordinary tool surface
  and disable the generation prompt for the prefill request.
- Added a live prefix regression test that compares the tokenized ordinary
  Caveman request against the tokenized prefill prefix.

## Results

- Two ordinary Caveman requests with the same agent, model, tool configuration,
  OpenClaw configuration, and llama-server/chat-template path each tokenized to
  `27,809` tokens.
- The ordinary-to-ordinary longest common prefix was `27,784` tokens.
- The first divergent token index was `27,784`.
- The divergence occurred inside the final user content, where the decoded
  context around the boundary differed as `Sat` vs `Sun`.
- That longer mathematical LCP is not the semantically safe reusable prefix,
  because it cuts through dynamic user content.
- The canonical stable prefill prefix tokenized to `27,554` tokens.
- The ordinary request token stream begins with that exact prefix, so
  `prefill_tokens == ordinary_tokens[:len(prefill_tokens)]` now holds.
- The exact-prefix regression test passed against the live llama-server at
  `http://127.0.0.1:18080`.
- Coverage of the ordinary prompt by the reusable prefix is approximately
  `99.08%` (`27,554 / 27,809`).
- The prefix boundary lands immediately before the next ordinary token
  `<|im_user|>`, so dynamic conversation content begins after the invariant
  provider-level bootstrap surface.

## Problems

- The cache acceptance sequence was intentionally not run yet.
- `incomplete_turn` / `replayInvalid` remain a separate OpenClaw integration
  issue, but they did not block the prefix proof.

## Decisions

- Kept the prefix proof at the provider-level token stream boundary rather than
  comparing JSON, message objects, or rendered chat-template text.
- Preserved Stage 6A.4 as `PARTIAL` / diagnostic-complete instead of trying to
  reinterpret it as a cache success.
- Stopped at the exact-prefix boundary and did not inspect llama-server cache
  matching or llama.cpp KDA/KV reuse yet.

## Next Phase

- With token-prefix identity proven, the next boundary is llama-server cache
  matching and then llama.cpp KDA/KV state reuse, but only after the current
  exact-prefix contract is treated as the invariant input.
- The existing regression test should be kept in place so future prompt-template
  or tool-surface changes fail fast if they break the ordinary prefix contract.

## Reproduction

- `cd /Users/pmains/Code/openclaw/kimi/openclaw-src && node scripts/run-vitest.mjs run --config test/vitest/vitest.unit.config.ts packages/ai/src/transports/openai-completions-params.test.ts src/agents/simple-completion-runtime.test.ts`
- `cd /Users/pmains/Code/openclaw/kimi/openclaw-src && OPENCLAW_LIVE_TEST=1 OPENCLAW_LIVE_LLAMASERVER_URL=http://127.0.0.1:18080 node scripts/run-vitest.mjs run --config test/vitest/vitest.unit.config.ts packages/ai/src/transports/openai-completions-params.prefix.test.ts`
- Prefix characterization artifacts:
  - `/Users/pmains/Code/openclaw/kimi/dev-openclaw/state/stage6a4-prefix-characterization.json`
  - `/Users/pmains/Code/openclaw/kimi/dev-openclaw/state/stage6a4-prefix-characterization-second.json`
  - `/Users/pmains/Code/openclaw/kimi/dev-openclaw/state/stage6a4-prefill-tool-surface.json`
  - `/Users/pmains/Code/openclaw/kimi/dev-openclaw/state/stage6a4-prefill-no-generation.json`
