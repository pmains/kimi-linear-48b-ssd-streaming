# Stage 5A Attempt Note

## Status

INVALID

## Summary

Stage 5A did not run on the same warm live server instance that handled the successful Stage 4 warm request.

## Evidence

- Stage 4 warm reuse was served by `llama-server` PID `89029`, started `2026-08-15 08:58:29 -0700`.
- The live service exited with code `134` and then restarted.
- The current serving process is PID `91674`, started `2026-08-15 09:31:51 -0700`.
- On the restarted process, the server reloaded the model again and the first slot selection reset to `t_last = -1`, which shows the prior slot/state did not survive the restart.

## Conclusion

Because the server restarted between Stage 4 and Stage 5A, this Stage 5A attempt is invalid rather than a failure of the warm-state path.

## Abort Cause

PID `89029` was the shell wrapper waiting on the child `llama-server` process. The child process crashed with `SIGABRT` / `Abort trap: 6` in the matching crash report
`~/Library/Logs/DiagnosticReports/llama-server-2026-08-15-093152.ips` for child PID `89033`.

Immediate cause:

- `ggml_new_object: not enough space in the context's memory pool (needed 1048800, available 1048576)`
- stack top: `ggml_new_tensor_impl` -> `ggml_new_tensor_3d`
- caller: `llama_expert_streamer::cache_ensure_temp`
- path: `llama_expert_streamer::zc_place` -> `llama_expert_streamer::load_layer` -> `llama_context::process_ubatch_streamed`

This is a reproducible runtime abort in the streamed expert-loading path, not a one-off configuration glitch. Treat it as a Stage 5C/blocker for cross-session durability testing until the allocation boundary is fixed.

---

## Deployment Retry Attempt (2026-08-15)

## Status

INVALID

## What Changed

- Rebuilt `llama.cpp/build-metal/bin/llama-server` from the tree containing the passing `cache_ensure_temp()` sizing fix.
- Froze that build into `runtime/live/` and restarted the managed wrapper once.
- Verified the live binary matches the freshly built binary byte-for-byte.

## Live Server

- Restarted live PID: `11752`
- Command line: `runtime/live/bin/llama-server -m models/kimi-linear/moonshotai_Kimi-Linear-48B-A3B-Instruct-Q4_K_M.gguf -ngl 0 --no-mmap --ctx-size 32768 --host 127.0.0.1 --port 18080 --parallel 1`
- Current status after the failed attempt: still running

## Attempted Stage 5A Run

- The first post-restart Caveman bootstrap was sent to `agent:caveman:main`.
- That request did not complete: the OpenClaw session tool timed out and the server later logged `stop: cancel task, id_task = 0`.
- The server reached prompt-processing milestones at `2048`, `4096`, `6144`, and `8192` tokens, then the task was canceled before generation completed.
- Because the cold bootstrap never completed, there was no valid warm state to reuse and no second Caveman session was launched.

## Conclusion

This deployment was correct, but the Stage 5A validation is still invalid because the unavoidable cold bootstrap was interrupted before it completed. A fresh same-PID rerun is still needed, but not during this turn.

---

## Detached Harness Retry (2026-08-15)

## Status

`INVALID`

## Harness

- Harness PID: `13270`
- Start time: `2026-08-15 13:08:29 -0700`
- Server PID at launch: `11752`
- Stdout: `/tmp/stage5a-detached-20260815T130829/stdout.log`
- Stderr: `/tmp/stage5a-detached-20260815T130829/stderr.log`
- Exit status: `/tmp/stage5a-detached-20260815T130829/exit.status`
- Metadata: `/tmp/stage5a-detached-20260815T130829/meta.txt`

## Result

- The harness did launch out-of-band from the initiating shell, but the cold Caveman bootstrap still did not complete.
- The live server log shows the task progressing to `8192` prompt tokens at `519.76 s`, then being canceled:
  - `stop: cancel task, id_task = 0`
- The server subsequently logged `stop processing: n_tokens = 10240, truncated = 0`.
- No stdout/stderr payload or exit-status file was produced before the cancellation, so there is no run/request ID to record from the detached launcher.

## Conclusion

The failure source is still the request cancellation at the OpenClaw/llama-server boundary, not the detached OS wrapper itself. The next valid Stage 5A harness still needs a request launcher that outlives the cold prefill without triggering this cancellation path.
