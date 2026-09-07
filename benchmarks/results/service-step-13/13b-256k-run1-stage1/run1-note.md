# 13B run 1 (2026-09-07 11:54:06-11:54:31 MST)

Stage-1 allocation/startup at ctx 262144: **actually healthy** (resolved n_ctx 262144, KV 2016 MiB / 262144 cells / 7 layers, recurrent RS 42.81 MiB / 27 layers, expert cache armed 8192 MiB zerocopy, Metal init OK Apple M5, short probe ok TTFT 0.013 s, steady RSS 10.27 GiB, host free 24-25%).

Stage 2 was SKIPPED by a **false-positive OOM detector**: naive grep matched the word 'abort' in the benign llama.cpp warning
    `common_fit_params: failed to fit params to free device memory: n_gpu_layers already set by user to 999, abort`
(fit-params auto-tuning aborted because -ngl 999 was already set; NOT an allocation failure).

Production was restored and verified after the skip: llama 200 / n_ctx 65536, gw 200, 11B sha 8baf474684, FK 0, no leftover test servers (restore-verify.json).

Superseded by run 2 (13b-256k/) with the corrected detector.
