#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <linux/sched.h>
#include <seccomp.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <unistd.h>

/* Small, fail-closed launcher. This binary is outside the submitted-code chroot.
 * The coordinator lives in a separate Fargate task, not in this security domain.
 * fd is a private close-on-exec setup error pipe, never a verdict channel. */
static int error_fd = -1;
static void fail(void) {
    if (error_fd >= 0) { const char failed = 'E'; (void)write(error_fd, &failed, 1); }
    _exit(125);
}
static long number(const char *s, long min, long max) {
    char *end; errno = 0; long n = strtol(s, &end, 10);
    if (errno || !*s || *end || n < min || n > max) fail();
    return n;
}
static void limit(int resource, rlim_t value) {
    struct rlimit r = {value, value}; if (setrlimit(resource, &r)) fail();
}
static void allow(scmp_filter_ctx ctx, const char *name) {
    int call = seccomp_syscall_resolve_name(name);
    if (call == __NR_SCMP_ERROR || seccomp_rule_add(ctx, SCMP_ACT_ALLOW, call, 0)) fail();
}
int main(int argc, char **argv) {
    if (argc < 7) return 125;
    error_fd = (int)number(argv[5], 3, 1024);
    if (fcntl(error_fd, F_SETFD, FD_CLOEXEC)) fail();
    long seconds = number(argv[2], 1, 30), memory = number(argv[3], 16, 1024);
    int compiler = !strcmp(argv[4], "compile");
    if (!compiler && strcmp(argv[4], "run")) fail();
    if (geteuid() != 0 || argv[1][0] != '/' || chdir(argv[1]) || chroot(".") || chdir("/job")) fail();
    umask(0077);
    limit(RLIMIT_CORE, 0); limit(RLIMIT_CPU, seconds + 1);
    limit(RLIMIT_AS, (rlim_t)memory * 1024 * 1024);
    limit(RLIMIT_FSIZE, compiler ? 16 * 1024 * 1024 : 1024 * 1024);
    limit(RLIMIT_NOFILE, 64); limit(RLIMIT_NPROC, compiler ? 32 : 1);
    if (setgroups(0, NULL) || setresgid(10001, 10001, 10001) || setresuid(10001, 10001, 10001)) fail();
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) fail();
    scmp_filter_ctx ctx = seccomp_init(SCMP_ACT_ERRNO(EPERM));
    if (!ctx) fail();
    const char *common[] = {
        "read", "write", "readv", "writev", "pread64", "pwrite64", "close", "close_range",
        "open", "openat", "access", "faccessat", "faccessat2", "stat", "fstat", "lstat", "newfstatat", "statx",
        "lseek", "mmap", "mprotect", "munmap", "mremap", "madvise", "brk",
        "rt_sigaction", "rt_sigprocmask", "rt_sigreturn", "sigaltstack",
        "clock_gettime", "clock_nanosleep", "nanosleep", "gettimeofday", "time", "times",
        "getpid", "getppid", "gettid", "getuid", "geteuid", "getgid", "getegid", "getgroups",
        "getcwd", "chdir", "fchdir", "readlink", "readlinkat", "getdents", "getdents64",
        "fcntl", "ioctl", "dup", "dup2", "dup3", "pipe", "pipe2", "poll", "ppoll", "select", "pselect6",
        "futex", "set_tid_address", "set_robust_list", "rseq", "arch_prctl", "getrandom",
        "uname", "sysinfo", "sched_getaffinity", "sched_yield", "getrlimit", "getrusage",
        "mkdir", "mkdirat", "rmdir", "unlink", "unlinkat", "rename", "renameat", "renameat2",
        "ftruncate", "truncate", "fsync", "fdatasync", "chmod", "fchmod", "fchmodat",
        "utimensat", "umask", "execve", "exit", "exit_group"
    };
    for (size_t i = 0; i < sizeof(common) / sizeof(common[0]); ++i) allow(ctx, common[i]);
    // Allow querying limits only; a submission cannot increase its imposed limits.
    if (seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(prlimit64), 2,
            SCMP_A0(SCMP_CMP_EQ, 0), SCMP_A2(SCMP_CMP_EQ, 0))) fail();
    if (compiler) {
        const char *extra[] = {"fork", "vfork", "wait4", "waitid", "kill", "tgkill"};
        for (size_t i = 0; i < sizeof(extra) / sizeof(extra[0]); ++i) allow(ctx, extra[i]);
        // Only ordinary fork/posix_spawn. No new namespaces, threads or process groups.
        if (seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(clone), 1, SCMP_A0(SCMP_CMP_EQ, SIGCHLD)) ||
            seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(clone), 1,
                SCMP_A0(SCMP_CMP_EQ, CLONE_VM | CLONE_VFORK | SIGCHLD))) fail();
        if (seccomp_rule_add(ctx, SCMP_ACT_ERRNO(ENOSYS), SCMP_SYS(clone3), 0)) fail();
    }
    // socket/connect, io_uring, ptrace, process_vm_*, mount, unshare, bpf,
    // credentials/capabilities, setsid/setpgid and runtime fork/clone are not allowed.
    if (seccomp_load(ctx)) fail();
    seccomp_release(ctx);
    char *const clean_env[] = {"PATH=/usr/bin:/bin", "LANG=C.UTF-8", "HOME=/job", "TMPDIR=/tmp", NULL};
    execve(argv[6], &argv[6], clean_env);
    fail();
}
