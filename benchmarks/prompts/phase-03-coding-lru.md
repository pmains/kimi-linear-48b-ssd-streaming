Implement a concurrent LRU cache in Rust with the following requirements:

1. Generic over key and value types, bounded by a maximum number of entries.
2. Thread-safe: safe for concurrent reads and writes from multiple threads, with a public API that cannot deadlock in normal usage.
3. Support get, insert, and remove operations, all O(1) average time.
4. Evict the least recently used entry when the cache exceeds capacity; reads must refresh recency.
5. Provide a way to iterate over the current entries without blocking concurrent operations for the whole iteration.
6. Avoid holding a global lock for the entire duration of get or insert; reads should not contend with each other.

Explain your design choices: which data structures you used for the map and the recency list, how you handle the concurrency (sharded locks, sharded maps, atomics, or a single lock), and the trade-offs. Then write the full implementation with doc comments, and finish with a short test suite covering concurrent access and eviction order.

Make the code production-quality: handle the edge cases, document invariants, and keep the public API minimal.
