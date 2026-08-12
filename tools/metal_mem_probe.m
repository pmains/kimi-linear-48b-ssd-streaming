#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <mach/mach.h>
#import <mach/vm_statistics.h>
#import <sys/mman.h>
#import <sys/stat.h>
#import <fcntl.h>
#import <unistd.h>
#import <stdlib.h>
#import <stdio.h>
#import <string.h>

static void print_stats(const char *label) {
    mach_msg_type_number_t count = HOST_VM_INFO64_COUNT;
    vm_statistics64_data_t vmstats;
    kern_return_t kr = host_statistics64(mach_host_self(), HOST_VM_INFO64, (host_info64_t)&vmstats, &count);
    if (kr != KERN_SUCCESS) {
        fprintf(stderr, "host_statistics64 failed: %d\n", kr);
        exit(1);
    }

    struct mach_task_basic_info tinfo;
    mach_msg_type_number_t tcount = MACH_TASK_BASIC_INFO_COUNT;
    kr = task_info(mach_task_self(), MACH_TASK_BASIC_INFO, (task_info_t)&tinfo, &tcount);
    if (kr != KERN_SUCCESS) {
        fprintf(stderr, "task_info failed: %d\n", kr);
        exit(1);
    }

    printf("%s\n", label);
    printf("  proc_rss=%llu proc_virt=%llu\n", (unsigned long long)tinfo.resident_size, (unsigned long long)tinfo.virtual_size);
    printf("  sys_free=%u active=%u inactive=%u wired=%u speculative=%u\n",
           vmstats.free_count, vmstats.active_count, vmstats.inactive_count, vmstats.wire_count, vmstats.speculative_count);
    printf("  sys_internal=%u external=%u purgeable=%u compressor=%u\n",
           vmstats.internal_page_count, vmstats.external_page_count, vmstats.purgeable_count, vmstats.compressor_page_count);
}

static void touch_bytes(volatile unsigned char *p, size_t len) {
    const size_t step = 4096;
    unsigned long long acc = 0;
    for (size_t i = 0; i < len; i += step) {
        acc += p[i];
    }
    if (acc == 0xdeadbeefULL) {
        fprintf(stderr, "unlikely\n");
    }
}

int main(void) {
    @autoreleasepool {
        const char *path = "/tmp/oc_mmap_probe.bin";
        const size_t len = 128 * 1024 * 1024;

        int fd = open(path, O_CREAT | O_RDWR, 0600);
        if (fd < 0) {
            perror("open");
            return 1;
        }
        if (ftruncate(fd, (off_t)len) != 0) {
            perror("ftruncate");
            return 1;
        }

        print_stats("baseline");

        void *mapped = mmap(NULL, len, PROT_READ, MAP_SHARED, fd, 0);
        if (mapped == MAP_FAILED) {
            perror("mmap");
            return 1;
        }
        print_stats("after mmap, before touch");

        touch_bytes((volatile unsigned char *)mapped, len);
        print_stats("after touching file-backed mmap");

        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (device == nil) {
            fprintf(stderr, "no Metal device\n");
            return 1;
        }
        id<MTLBuffer> buf = [device newBufferWithLength:len options:MTLResourceStorageModeShared];
        if (buf == nil) {
            fprintf(stderr, "newBufferWithLength failed\n");
            return 1;
        }
        memset([buf contents], 0xA5, len);
        print_stats("after allocating/touching Metal shared buffer");

        munmap(mapped, len);
        close(fd);
        unlink(path);
    }
    return 0;
}
