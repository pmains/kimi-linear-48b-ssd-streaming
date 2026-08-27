/*
 * Phase 9C I/O probe (ANALYSIS ONLY — never promoted to the runtime).
 *
 * Replays a recorded pread schedule against the model GGUF under several
 * issue strategies to quantify the I/O side of the async-overlap question:
 *
 *   seq        one pread at a time, in recorded order (reproduces the
 *              current runtime behavior; validates the probe against the
 *              runtime's own pread_us)
 *   par<N>     N worker threads pull reads from a shared atomic cursor
 *              (emulates bounded async submission / queue depth N)
 *   coalesce   sort by offset, merge adjacent ranges (gap<=0), read the
 *              merged chunks once (emulates storage-layout/coalescing)
 *   rawseq     read the same total bytes as one sequential stream from
 *              offset 0 in 4 MiB chunks (the SSD/page-cache ceiling)
 *
 * Input: a file with one "offset bytes" pair per line (from the runtime's
 * KIMI_PHASE9C_TRACE .preads output, columns 4 and 5).
 *
 * Usage: phase09c_io_probe GGUF SCHEDULE [strategy...]
 *   strategies default: seq par4 par8 par16 par32 coalesce rawseq
 *   env PHASE09C_WARM_MB=<n> : pre-read the first n MiB of the file (cache
 *   warm), default 0.
 *
 * Output: one line per strategy:
 *   strategy reads bytes wall_ms GBps p50_us p90_us p99_us max_us
 */
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

typedef struct {
    int64_t off;
    int64_t len;
} rd_t;

static rd_t * g_reads = NULL;
static int64_t g_n = 0;
static int64_t g_cur = 0;          /* atomic cursor for par strategies */
static pthread_mutex_t g_mtx = PTHREAD_MUTEX_INITIALIZER;
static int g_fd = -1;
static unsigned char * g_buf = NULL;
static size_t g_buf_len = 0;
static int64_t g_done = 0;
static int64_t * g_lat = NULL;     /* per-read latencies (us), seq only */

static int64_t now_us(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t) ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}

static int cmp_rd(const void * a, const void * b) {
    const rd_t * x = (const rd_t *) a, * y = (const rd_t *) b;
    return (x->off > y->off) - (x->off < y->off);
}

static int cmp_us(const void * a, const void * b) {
    int64_t x = *(const int64_t *) a, y = *(const int64_t *) b;
    return (x > y) - (x < y);
}

static void lat_stats(int64_t * lat, int64_t n, double * p50, double * p90, double * p99, double * pmax) {
    qsort(lat, (size_t) n, sizeof(int64_t), cmp_us);
    *p50 = lat[(size_t) (n * 0.50)];
    *p90 = lat[(size_t) (n * 0.90)];
    *p99 = lat[(size_t) (n * 0.99)];
    *pmax = lat[(size_t) (n - 1)];
}

static void * par_worker(void * arg) {
    long tid = (long) arg;
    (void) tid;
    for (;;) {
        pthread_mutex_lock(&g_mtx);
        int64_t i = g_cur++;
        pthread_mutex_unlock(&g_mtx);
        if (i >= g_n) break;
        const rd_t * r = &g_reads[i];
        ssize_t n = pread(g_fd, g_buf, (size_t) r->len, (off_t) r->off);
        if (n != (ssize_t) r->len) {
            fprintf(stderr, "par: short read at %lld (%zd/%lld)\n",
                    (long long) r->off, n, (long long) r->len);
        }
    }
    return NULL;
}

static void run_seq(int64_t * wall_ms, double * gbps, double * p50, double * p90, double * p99, double * pmax) {
    int64_t t0 = now_us();
    for (int64_t i = 0; i < g_n; ++i) {
        const rd_t * r = &g_reads[i];
        int64_t s = now_us();
        ssize_t n = pread(g_fd, g_buf, (size_t) r->len, (off_t) r->off);
        g_lat[i] = now_us() - s;
        if (n != (ssize_t) r->len) {
            fprintf(stderr, "seq: short read at %lld (%zd/%lld)\n",
                    (long long) r->off, n, (long long) r->len);
        }
    }
    int64_t dt = now_us() - t0;
    *wall_ms = dt / 1000.0;
    int64_t total = 0;
    for (int64_t i = 0; i < g_n; ++i) total += g_reads[i].len;
    *gbps = (double) total / (double) dt; /* GB per us -> *1000 = GB/s */
    *gbps = (double) total / 1e9 / ((double) dt / 1e6);
    lat_stats(g_lat, g_n, p50, p90, p99, pmax);
}

static void run_par(int nthreads, int64_t * wall_ms, double * gbps) {
    pthread_t th[64];
    if (nthreads > 64) nthreads = 64;
    g_cur = 0;
    int64_t t0 = now_us();
    for (long i = 0; i < nthreads; ++i) {
        pthread_create(&th[i], NULL, par_worker, (void *) i);
    }
    for (long i = 0; i < nthreads; ++i) {
        pthread_join(th[i], NULL);
    }
    int64_t dt = now_us() - t0;
    *wall_ms = dt / 1000.0;
    int64_t total = 0;
    for (int64_t i = 0; i < g_n; ++i) total += g_reads[i].len;
    *gbps = (double) total / 1e9 / ((double) dt / 1e6);
}

static void run_coalesce(int64_t * wall_ms, double * gbps) {
    /* sort by offset */
    rd_t * r = malloc(sizeof(rd_t) * (size_t) g_n);
    memcpy(r, g_reads, sizeof(rd_t) * (size_t) g_n);
    qsort(r, (size_t) g_n, sizeof(rd_t), cmp_rd);
    /* merge adjacent with gap <= 0 */
    int64_t n_merged = 0;
    rd_t * m = malloc(sizeof(rd_t) * (size_t) g_n);
    int64_t lo = r[0].off, hi = r[0].off + r[0].len;
    for (int64_t i = 1; i < g_n; ++i) {
        if (r[i].off <= hi) {
            int64_t e = r[i].off + r[i].len;
            if (e > hi) hi = e;
        } else {
            m[n_merged].off = lo; m[n_merged].len = hi - lo; n_merged++;
            lo = r[i].off; hi = r[i].off + r[i].len;
        }
    }
    m[n_merged].off = lo; m[n_merged].len = hi - lo; n_merged++;
    int64_t max_merged = 0;
    for (int64_t i = 0; i < n_merged; ++i) {
        if (m[i].len > max_merged) max_merged = m[i].len;
    }
    unsigned char * buf = malloc((size_t) max_merged);
    if (!buf) { fprintf(stderr, "coalesce: buf alloc failed (%lld)\n", (long long) max_merged); exit(1); }
    int64_t t0 = now_us();
    for (int64_t i = 0; i < n_merged; ++i) {
        ssize_t n = pread(g_fd, buf, (size_t) m[i].len, (off_t) m[i].off);
        if (n != (ssize_t) m[i].len) {
            fprintf(stderr, "coalesce: short read at %lld (%zd/%lld)\n",
                    (long long) m[i].off, n, (long long) m[i].len);
        }
    }
    int64_t dt = now_us() - t0;
    *wall_ms = dt / 1000.0;
    int64_t total = 0;
    for (int64_t i = 0; i < g_n; ++i) total += g_reads[i].len;
    *gbps = (double) total / 1e9 / ((double) dt / 1e6);
    free(buf); free(r); free(m);
}

static void run_rawseq(int64_t * wall_ms, double * gbps) {
    int64_t total = 0;
    for (int64_t i = 0; i < g_n; ++i) total += g_reads[i].len;
    int64_t CHUNK = 4 * 1024 * 1024;
    if (CHUNK > (int64_t) g_buf_len) CHUNK = (int64_t) g_buf_len;
    int64_t t0 = now_us();
    int64_t off = 0, left = total;
    while (left > 0) {
        int64_t c = left < CHUNK ? left : CHUNK;
        ssize_t n = pread(g_fd, g_buf, (size_t) c, (off_t) off);
        if (n != (ssize_t) c) {
            fprintf(stderr, "rawseq: short read at %lld (%zd/%lld)\n",
                    (long long) off, n, (long long) c);
            break;
        }
        off += c; left -= c;
    }
    int64_t dt = now_us() - t0;
    *wall_ms = dt / 1000.0;
    *gbps = (double) total / 1e9 / ((double) dt / 1e6);
}

int main(int argc, char ** argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s GGUF SCHEDULE [strategy...]\n", argv[0]);
        return 2;
    }
    const char * path = argv[1];
    const char * sched = argv[2];
    g_fd = open(path, O_RDONLY);
    if (g_fd < 0) { perror("open"); return 1; }

    FILE * f = fopen(sched, "r");
    if (!f) { perror("sched"); return 1; }
    int64_t cap = 1 << 16;
    g_reads = malloc(sizeof(rd_t) * (size_t) cap);
    char line[256];
    int64_t max_len = 0;
    while (fgets(line, sizeof(line), f)) {
        if (line[0] == '#') continue;
        int64_t off = 0, len = 0;
        if (sscanf(line, "%lld %lld", &off, &len) != 2) continue;
        if (g_n >= cap) {
            cap *= 2;
            g_reads = realloc(g_reads, sizeof(rd_t) * (size_t) cap);
        }
        g_reads[g_n].off = off; g_reads[g_n].len = len; g_n++;
        if (len > max_len) max_len = len;
    }
    fclose(f);
    if (g_n == 0) { fprintf(stderr, "no reads in schedule\n"); return 1; }
    g_lat = malloc(sizeof(int64_t) * (size_t) g_n);
    g_buf_len = (size_t) max_len;
    g_buf = malloc(g_buf_len);
    if (!g_buf) { fprintf(stderr, "staging alloc failed (%zu)\n", g_buf_len); return 1; }

    const char * warm = getenv("PHASE09C_WARM_MB");
    if (warm && atoi(warm) > 0) {
        int64_t mb = (int64_t) atoi(warm);
        int64_t left = mb * 1024 * 1024, off = 0;
        while (left > 0) {
            int64_t c = left < (int64_t) g_buf_len ? left : (int64_t) g_buf_len;
            ssize_t n = pread(g_fd, g_buf, (size_t) c, (off_t) off);
            if (n <= 0) break;
            off += n; left -= n;
        }
        fprintf(stderr, "warmed %lld MiB\n", (long long) mb);
    }

    int64_t total = 0;
    for (int64_t i = 0; i < g_n; ++i) total += g_reads[i].len;
    fprintf(stderr, "schedule: %lld reads, %lld bytes (%.1f MiB), max read %lld bytes\n",
            (long long) g_n, (long long) total, (double) total / 1048576.0, (long long) max_len);

    int run_all = argc < 4;
    for (int a = 3; a < argc || run_all; ++a) {
        const char * strat;
        if (run_all) {
            static const char * all[] = {"seq", "par4", "par8", "par16", "par32", "coalesce", "rawseq"};
            if (a - 3 >= 7) break;
            strat = all[a - 3];
        } else {
            strat = argv[a];
        }
        int64_t wall = 0; double gbps = 0;
        double p50 = 0, p90 = 0, p99 = 0, pmax = 0;
        if (strcmp(strat, "seq") == 0) {
            run_seq(&wall, &gbps, &p50, &p90, &p99, &pmax);
            printf("%s reads=%lld bytes=%lld wall_ms=%lld GBps=%.2f p50_us=%.0f p90_us=%.0f p99_us=%.0f max_us=%.0f\n",
                   strat, (long long) g_n, (long long) total, wall, gbps, p50, p90, p99, pmax);
        } else if (strncmp(strat, "par", 3) == 0) {
            int nt = atoi(strat + 3);
            if (nt < 1) nt = 1;
            run_par(nt, &wall, &gbps);
            printf("%s reads=%lld bytes=%lld wall_ms=%lld GBps=%.2f\n",
                   strat, (long long) g_n, (long long) total, wall, gbps);
        } else if (strcmp(strat, "coalesce") == 0) {
            run_coalesce(&wall, &gbps);
            printf("%s reads=%lld bytes=%lld wall_ms=%lld GBps=%.2f\n",
                   strat, (long long) g_n, (long long) total, wall, gbps);
        } else if (strcmp(strat, "rawseq") == 0) {
            run_rawseq(&wall, &gbps);
            printf("%s reads=%lld bytes=%lld wall_ms=%lld GBps=%.2f\n",
                   strat, (long long) g_n, (long long) total, wall, gbps);
        } else {
            fprintf(stderr, "unknown strategy: %s\n", strat);
        }
        fflush(stdout);
        if (run_all && a >= 3 + 6) break;
    }
    return 0;
}
