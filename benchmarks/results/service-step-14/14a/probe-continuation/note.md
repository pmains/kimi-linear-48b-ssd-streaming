# 14A continuation probe (2026-09-07 20:45-20:52 MST)

Validates the core same-session design before the measured driver legs:
two consecutive turns, SAME session key (agent:kimi:step14a-probe-cont-r1),
through the real agent path on the live 262144 contract.

Results:
- cont-t1: rc 0, wall 76.5s, promptTokens 13,561 (assembled), ctx 262144
  resolved, liveness working, winner llama-server. final call usage:
  input 105 / cacheRead 13,456 (99.2% of assembled from cache).
- cont-t2: rc 0, wall 36.6s, promptTokens 14,196 (grew by the t1 exchange ->
  session continuation CONFIRMED, context accumulates on same key),
  ctx 262144 resolved. final call usage: input 101 / cacheRead 14,095
  (99.3%). llama processed only 97 new tokens of 14,210 assembled.

Interpretation: cross-turn prefix reuse through the real agent path works
when the session continues on the same key and the slot KV is retained.
llama-server slot went 48,054 -> 13,589 -> 14,210 (our turns replaced the
13G residue). This is the mechanism 14A must characterize at small/medium/
large context scale; driver legs follow in this evidence dir.
