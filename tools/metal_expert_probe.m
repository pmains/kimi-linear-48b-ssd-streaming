// Phase 3 Metal residency probe (3B.3).
//
// Demonstrates that an individual expert's quantized bytes can be placed in
// independently managed Metal-accessible shared memory at per-expert
// allocation granularity, WITHOUT allocating or making resident the complete
// parent 3D expert tensor.
//
// Usage:
//   metal_expert_probe <model.gguf> <parent_offset> <parent_length> \
//       <off0> <len0> [<off1> <len1> ...]
//
// For each (offset, length) pair it preads the byte range from the GGUF,
// allocates a shared-mode MTLBuffer of exactly that length, copies the bytes
// in, and verifies a byte-exact round trip. It then allocates one buffer of
// the parent tensor extent to quantify the contrast. Residency is measured via
// MTLDevice.currentAllocatedSize (device-managed allocations) and task RSS.
//
// Offsets/lengths are computed by the verified Python addressability path
// (tools/expert_slice_verify.py) and passed in — this probe tests the
// storage -> Metal boundary, not GGUF offset math.

#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <mach/mach.h>
#import <stdio.h>
#import <stdlib.h>
#import <string.h>
#import <fcntl.h>
#import <unistd.h>

static uint64_t rss_bytes(void) {
    struct mach_task_basic_info tinfo;
    mach_msg_type_number_t tcount = MACH_TASK_BASIC_INFO_COUNT;
    if (task_info(mach_task_self(), MACH_TASK_BASIC_INFO, (task_info_t)&tinfo, &tcount) != KERN_SUCCESS) {
        return 0;
    }
    return (uint64_t)tinfo.resident_size;
}

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        if (argc < 5 || (argc - 4) % 2 != 0) {
            fprintf(stderr, "usage: %s <gguf> <parent_off> <parent_len> <off len> [<off len> ...]\n", argv[0]);
            return 2;
        }
        const char * path = argv[1];
        const uint64_t parent_off = strtoull(argv[2], NULL, 10);
        const uint64_t parent_len = strtoull(argv[3], NULL, 10);
        const int n_slices = (argc - 4) / 2;

        int fd = open(path, O_RDONLY);
        if (fd < 0) { perror("open"); return 1; }

        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (device == nil) { fprintf(stderr, "no Metal device\n"); return 1; }
        printf("device: %s\n", device.name.UTF8String);
        printf("recommendedMaxWorkingSetSize: %.1f GiB\n",
               (double)device.recommendedMaxWorkingSetSize / (1024.0*1024.0*1024.0));
        printf("currentAllocatedSize baseline: %.2f MiB\n",
               (double)device.currentAllocatedSize / (1024.0*1024.0));
        const uint64_t rss0 = rss_bytes();
        printf("rss baseline: %.1f MiB\n", (double)rss0 / (1024.0*1024.0));

        uint64_t alloc_before = device.currentAllocatedSize;
        uint64_t expected_total = 0;
        printf("\n%-8s %-14s %-10s %-12s %-12s %-10s\n",
               "slice", "offset", "length", "alloc_delta", "rss_delta", "roundtrip");
        for (int i = 0; i < n_slices; i++) {
            const uint64_t off = strtoull(argv[4 + 2*i], NULL, 10);
            const uint64_t len = strtoull(argv[5 + 2*i], NULL, 10);
            expected_total += len;

            unsigned char * bytes = malloc(len);
            ssize_t n = pread(fd, bytes, len, (off_t)off);
            if (n != (ssize_t)len) { fprintf(stderr, "short pread at %llu: %zd/%llu\n", off, n, len); return 1; }

            id<MTLBuffer> buf = [device newBufferWithLength:(NSUInteger)len
                                                    options:MTLResourceStorageModeShared];
            memcpy(buf.contents, bytes, len);
            // round trip: read back from the Metal buffer and compare
            int ok = memcmp(buf.contents, bytes, len) == 0;

            uint64_t alloc_delta = device.currentAllocatedSize - alloc_before;
            uint64_t rss_delta = rss_bytes() - rss0;
            alloc_before = device.currentAllocatedSize;
            printf("%-8s %-14llu %-10llu %-12.2f %-12.2f %-10s\n",
                   "expert", off, len,
                   (double)alloc_delta / (1024.0*1024.0),
                   (double)rss_delta / (1024.0*1024.0),
                   ok ? "OK" : "FAIL");
            if (!ok) { fprintf(stderr, "roundtrip FAILED for slice at %llu\n", off); return 1; }
            free(bytes);
        }

        // parent-tensor contrast: one buffer covering the complete 3D expert
        // tensor extent (what conventional Metal execution does)
        unsigned char * parent_bytes = malloc(parent_len);
        ssize_t n = pread(fd, parent_bytes, parent_len, (off_t)parent_off);
        if (n != (ssize_t)parent_len) { fprintf(stderr, "short pread parent: %zd\n", n); return 1; }
        id<MTLBuffer> parent_buf = [device newBufferWithLength:(NSUInteger)parent_len
                                                       options:MTLResourceStorageModeShared];
        memcpy(parent_buf.contents, parent_bytes, parent_len);
        uint64_t parent_alloc_delta = device.currentAllocatedSize - alloc_before;
        printf("\n%-8s %-14llu %-10llu %-12.2f %-12s %-10s\n",
               "PARENT", parent_off, parent_len,
               (double)parent_alloc_delta / (1024.0*1024.0), "", "");
        printf("\nper-expert total bytes requested: %.2f MiB\n", (double)expected_total / (1024.0*1024.0));
        printf("observed: %d per-expert buffers, each at expert scale; "
               "parent tensor extent allocated separately only for contrast\n", n_slices);

        close(fd);
    }
    return 0;
}
