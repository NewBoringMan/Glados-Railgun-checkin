#define _DARWIN_C_SOURCE
#define _XOPEN_SOURCE 700
#include "../../app_integration/browser_process_info.h"
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#ifdef __APPLE__
#include <errno.h>
#include <fcntl.h>
#include <mach-o/dyld.h>
#include <poll.h>
#include <sys/wait.h>
#endif

static unsigned char bytes[16384];
static size_t fixture(const char *const *arguments, size_t count, const char *tail) {
    memset(bytes, 0xA5, sizeof(bytes));
    int argc = (int)count;
    memcpy(bytes, &argc, sizeof(argc));
    size_t position = sizeof(argc);
    const char executable[] = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge";
    memcpy(bytes + position, executable, sizeof(executable)); position += sizeof(executable);
    size_t padding = (8 - sizeof(executable) % 8) % 8;
    while (padding--) bytes[position++] = 0;
    for (size_t i = 0; i < count; ++i) {
        size_t size = strlen(arguments[i]) + 1;
        assert(position + size < sizeof(bytes));
        memcpy(bytes + position, arguments[i], size); position += size;
    }
    if (tail) {
        size_t size = strlen(tail) + 1;
        assert(position + size < sizeof(bytes));
        memcpy(bytes + position, tail, size); position += size;
    }
    return position;
}

static struct glados_browser_arguments parse(const char *const *arguments, size_t count, const char *tail) {
    struct glados_browser_arguments result;
    size_t size = fixture(arguments, count, tail);
    assert(glados_browser_parse_procargs(bytes, size, &result));
    return result;
}

static void invalid(const char *const *arguments, size_t count) {
    struct glados_browser_arguments result;
    memset(&result, 0x5A, sizeof(result));
    size_t size = fixture(arguments, count, NULL);
    assert(!glados_browser_parse_procargs(bytes, size, &result));
    struct glados_browser_arguments zero = {0};
    assert(memcmp(&result, &zero, sizeof(result)) == 0);
}

static void path_fixtures(void) {
    char temporary[] = "/tmp/glados-browser-paths.XXXXXX";
    assert(mkdtemp(temporary));
    char root[GLADOS_BROWSER_PATH_LIMIT], edge[GLADOS_BROWSER_PATH_LIMIT];
    char alias[GLADOS_BROWSER_PATH_LIMIT], link[GLADOS_BROWSER_PATH_LIMIT];
    char resolved_root[GLADOS_BROWSER_PATH_LIMIT], resolved_profile[GLADOS_BROWSER_PATH_LIMIT];
    snprintf(root, sizeof(root), "%s/BrowserProfiles", temporary); assert(mkdir(root, 0700) == 0);
    snprintf(edge, sizeof(edge), "%s/BrowserProfiles/edge", temporary); assert(mkdir(edge, 0700) == 0);
    assert(glados_browser_safe_profile(edge, NULL, resolved_root, resolved_profile));
    char canonical_root[GLADOS_BROWSER_PATH_LIMIT]; strcpy(canonical_root, resolved_root);
    assert(glados_browser_safe_profile(resolved_profile, canonical_root, resolved_root, resolved_profile));
    snprintf(alias, sizeof(alias), "%s/Root Alias", temporary); assert(symlink(root, alias) == 0);
    char root_alias[GLADOS_BROWSER_PATH_LIMIT];
    snprintf(root_alias, sizeof(root_alias), "%s/Root Alias/edge", temporary);
    /* Arbitrary aliases require the explicit BrowserProfiles anchor. */
    assert(!glados_browser_safe_profile(root_alias, NULL, resolved_root, resolved_profile));
    snprintf(link, sizeof(link), "%s/BrowserProfiles/other", temporary); assert(symlink(edge, link) == 0);
    assert(!glados_browser_safe_profile(link, NULL, resolved_root, resolved_profile));
    char traversal[GLADOS_BROWSER_PATH_LIMIT];
    snprintf(traversal, sizeof(traversal), "%s/BrowserProfiles/edge/../edge", temporary);
    assert(!glados_browser_safe_profile(traversal, NULL, resolved_root, resolved_profile));
    assert(unlink(link) == 0); assert(unlink(alias) == 0); assert(rmdir(edge) == 0); assert(rmdir(root) == 0);
    /* The root itself may be a symlink to a separately named directory. */
    snprintf(alias, sizeof(alias), "%s/Actual Root", temporary); assert(mkdir(alias, 0700) == 0);
    snprintf(edge, sizeof(edge), "%s/Actual Root/edge", temporary); assert(mkdir(edge, 0700) == 0);
    assert(symlink(alias, root) == 0);
    snprintf(link, sizeof(link), "%s/BrowserProfiles/edge", temporary);
    assert(glados_browser_safe_profile(link, NULL, resolved_root, resolved_profile));
    strcpy(canonical_root, resolved_root);
    assert(glados_browser_safe_profile(edge, canonical_root, resolved_root, resolved_profile));
    assert(unlink(root) == 0); assert(rmdir(edge) == 0); assert(rmdir(alias) == 0); assert(rmdir(temporary) == 0);
}

#ifdef __APPLE__
/* These integration fixtures execute only copies of this test binary. There
 * is no browser, socket, user profile, GUI, or network access. Closing the
 * parent's pipe ends the synthetic process, including on fixture failure. */
static pid_t synthetic_pid = -1;
static int synthetic_input = -1;
static char synthetic_directory[GLADOS_BROWSER_PATH_LIMIT];
static char synthetic_executable[GLADOS_BROWSER_PATH_LIMIT];
static char synthetic_root[GLADOS_BROWSER_PATH_LIMIT];
static char synthetic_profile[GLADOS_BROWSER_PATH_LIMIT];

static int stop_synthetic(void) {
    if (synthetic_input >= 0) { close(synthetic_input); synthetic_input = -1; }
    if (synthetic_pid <= 0) return 1;
    int status = 0;
    pid_t value;
    do { value = waitpid(synthetic_pid, &status, 0); } while (value < 0 && errno == EINTR);
    synthetic_pid = -1;
    return value > 0 && WIFEXITED(status) && WEXITSTATUS(status) == 0;
}

static void synthetic_cleanup(void) {
    (void)stop_synthetic();
    if (*synthetic_executable) unlink(synthetic_executable);
    if (*synthetic_profile) rmdir(synthetic_profile);
    if (*synthetic_root) rmdir(synthetic_root);
    if (*synthetic_directory) rmdir(synthetic_directory);
}

static void integration_require(int condition) {
    if (!condition) {
        fputs("browser_process_info macOS integration fixture failed\n", stderr);
        exit(1);
    }
}

static void copy_test_executable(void) {
    char path[GLADOS_BROWSER_PATH_LIMIT], canonical[GLADOS_BROWSER_PATH_LIMIT];
    uint32_t size = (uint32_t)sizeof(path);
    integration_require(_NSGetExecutablePath(path, &size) == 0 && realpath(path, canonical) != NULL);
    int source = open(canonical, O_RDONLY);
    int destination = open(synthetic_executable, O_WRONLY | O_CREAT | O_EXCL, 0700);
    integration_require(source >= 0 && destination >= 0);
    char block[16384];
    ssize_t count;
    while ((count = read(source, block, sizeof(block))) > 0) {
        ssize_t copied = 0;
        while (copied < count) {
            ssize_t written = write(destination, block + copied, (size_t)(count - copied));
            if (written < 0 && errno == EINTR) continue;
            integration_require(written > 0);
            copied += written;
        }
    }
    integration_require(count == 0);
    integration_require(close(source) == 0 && close(destination) == 0);
}

static void start_synthetic(int with_port) {
    int input[2], ready[2];
    integration_require(pipe(input) == 0 && pipe(ready) == 0);
    char profile_flag[GLADOS_BROWSER_PATH_LIMIT + 32];
    int amount = snprintf(profile_flag, sizeof(profile_flag), "--user-data-dir=%s", synthetic_profile);
    integration_require(amount > 0 && (size_t)amount < sizeof(profile_flag));
    pid_t child = fork();
    integration_require(child >= 0);
    if (child == 0) {
        if (dup2(input[0], STDIN_FILENO) < 0 || dup2(ready[1], STDOUT_FILENO) < 0) _exit(120);
        close(input[0]); close(input[1]); close(ready[0]); close(ready[1]);
        char *arguments[] = {synthetic_executable, "--synthetic-browser", profile_flag,
                             "--remote-debugging-port=49321", NULL};
        if (!with_port) arguments[3] = NULL;
        char *environment[] = {"GLADOS_SYNTHETIC=1", NULL};
        execve(synthetic_executable, arguments, environment);
        _exit(121);
    }
    synthetic_pid = child; synthetic_input = input[1];
    close(input[0]); close(ready[1]);
    struct pollfd descriptor = {ready[0], POLLIN, 0};
    int available;
    do { available = poll(&descriptor, 1, 5000); } while (available < 0 && errno == EINTR);
    char marker = 0;
    ssize_t count = available > 0 ? read(ready[0], &marker, 1) : -1;
    close(ready[0]);
    integration_require(count == 1 && marker == 'R');
}

static void inspect_synthetic(char output[1024]) {
    int pipe_fds[2];
    integration_require(pipe(pipe_fds) == 0 && fflush(stdout) == 0);
    int saved = dup(STDOUT_FILENO);
    integration_require(saved >= 0 && dup2(pipe_fds[1], STDOUT_FILENO) >= 0);
    close(pipe_fds[1]);
    int result = glados_browser_process_info(synthetic_executable, synthetic_profile);
    integration_require(fflush(stdout) == 0 && dup2(saved, STDOUT_FILENO) >= 0);
    close(saved);
    size_t used = 0;
    ssize_t amount;
    while (used < 1023 && (amount = read(pipe_fds[0], output + used, 1023 - used)) > 0)
        used += (size_t)amount;
    close(pipe_fds[0]);
    output[used] = 0;
    integration_require(result == 0 && used < 1023);
}

static void process_fixtures(void) {
    integration_require(atexit(synthetic_cleanup) == 0);
    strcpy(synthetic_directory, "/tmp/glados-browser-native.XXXXXX");
    integration_require(mkdtemp(synthetic_directory) != NULL);
    /* Both the executable and profile contain spaces, exactly as App launches
     * do; argv must be recovered from the kernel, never split as shell text. */
    snprintf(synthetic_executable, sizeof(synthetic_executable), "%s/Synthetic Browser", synthetic_directory);
    snprintf(synthetic_root, sizeof(synthetic_root), "%s/BrowserProfiles", synthetic_directory);
    snprintf(synthetic_profile, sizeof(synthetic_profile), "%s/BrowserProfiles/edge with spaces", synthetic_directory);
    integration_require(mkdir(synthetic_root, 0700) == 0 && mkdir(synthetic_profile, 0700) == 0);
    copy_test_executable();
    char output[1024];
    inspect_synthetic(output);
    integration_require(strcmp(output, "{\"schema\":\"glados.browser-process\",\"version\":1,\"state\":\"none\"}\n") == 0);
    start_synthetic(1);
    inspect_synthetic(output);
    int pid = 0, consumed = 0;
    unsigned int port = 0;
    unsigned long long seconds = 0, microseconds = 0;
    int fields = sscanf(output, "{\"schema\":\"glados.browser-process\",\"version\":1,\"state\":\"found\",\"pid\":%d,\"port\":%u,\"startedAt\":\"%llu:%llu\"}\n%n",
                        &pid, &port, &seconds, &microseconds, &consumed);
    integration_require(fields == 4 && consumed > 0 && output[consumed] == 0 &&
                        pid == synthetic_pid && port == 49321 && seconds > 0 && microseconds < 1000000);
    integration_require(stop_synthetic());
    start_synthetic(0);
    inspect_synthetic(output);
    integration_require(strcmp(output, "{\"schema\":\"glados.browser-process\",\"version\":1,\"state\":\"blocked\",\"reason\":\"debugging_unavailable\"}\n") == 0);
    integration_require(stop_synthetic());
    synthetic_cleanup();
    synthetic_directory[0] = synthetic_executable[0] = synthetic_root[0] = synthetic_profile[0] = 0;
}
#endif

int main(int argc, char **argv) {
#ifdef __APPLE__
    if (argc > 1 && strcmp(argv[1], "--synthetic-browser") == 0) {
        if (write(STDOUT_FILENO, "R", 1) != 1) return 122;
        close(STDOUT_FILENO);
        char value;
        ssize_t amount;
        do { amount = read(STDIN_FILENO, &value, 1); } while (amount > 0 || (amount < 0 && errno == EINTR));
        return amount == 0 ? 0 : 123;
    }
#else
    (void)argc; (void)argv;
#endif
    const char *normal[] = {"/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "--remote-debugging-port=49321", "--user-data-dir=/Users/Example Person/Library/Application Support/GLaDOS Account Center/BrowserProfiles/edge",
        "--no-first-run", "https://glados.cloud/console/checkin"};
    struct glados_browser_arguments result = parse(normal, 5, "--user-data-dir=/environment/must/not/be/read");
    assert(result.port == 49321 && result.has_profile && result.is_main_process);
    assert(strcmp(result.profile, normal[2] + strlen("--user-data-dir=")) == 0);
    assert(glados_browser_decide_arguments(&result, 1) == GLADOS_BROWSER_REUSABLE);
    assert(glados_browser_decide_arguments(&result, 0) == GLADOS_BROWSER_OTHER);

    const char *no_port[] = {"Edge", "--user-data-dir=/a/BrowserProfiles/edge"};
    result = parse(no_port, 2, "--remote-debugging-port=49321");
    assert(!result.has_port && glados_browser_decide_arguments(&result, 1) == GLADOS_BROWSER_NO_DEBUGGING);
    const char *no_profile[] = {"Edge", "--remote-debugging-port=49321"};
    result = parse(no_profile, 2, "--user-data-dir=/a/BrowserProfiles/edge");
    assert(!result.has_profile && glados_browser_decide_arguments(&result, 1) == GLADOS_BROWSER_OTHER);
    const char *child[] = {"Edge", "--type=renderer", "--remote-debugging-port=49321", "--user-data-dir=/a/BrowserProfiles/edge"};
    result = parse(child, 4, NULL);
    assert(glados_browser_decide_arguments(&result, 1) == GLADOS_BROWSER_OTHER);
    const char *after_dash[] = {"Edge", "--", "--user-data-dir=/a/BrowserProfiles/edge", "--remote-debugging-port=49321"};
    result = parse(after_dash, 4, NULL); assert(!result.has_profile && !result.has_port);
    const char *duplicate_profile[] = {"Edge", "--user-data-dir=/a", "--user-data-dir=/a"}; invalid(duplicate_profile, 3);
    const char *duplicate_port[] = {"Edge", "--remote-debugging-port=49321", "--remote-debugging-port=49321"}; invalid(duplicate_port, 3);
    const char *bare[] = {"Edge", "--user-data-dir", "/a/BrowserProfiles/edge"}; invalid(bare, 3);
    const char *relative[] = {"Edge", "--user-data-dir=relative/path"}; invalid(relative, 2);
    const char *control[] = {"Edge", "--user-data-dir=/a\n/BrowserProfiles/edge"}; invalid(control, 2);
    const char *pipe[] = {"Edge", "--remote-debugging-pipe"}; invalid(pipe, 2);
    const char *pipe_port[] = {"Edge", "--remote-debugging-port=49321", "--remote-debugging-pipe=1"}; invalid(pipe_port, 3);
    const char *empty_first[] = {"", "--user-data-dir=/a/BrowserProfiles/edge"};
    size_t empty_size = fixture(empty_first, 2, "--remote-debugging-port=49321");
    assert(!glados_browser_parse_procargs(bytes, empty_size, &result));
    const char *empty_later[] = {"Edge", "", "--user-data-dir=/a/BrowserProfiles/edge"};
    result = parse(empty_later, 3, "--remote-debugging-port=49321"); assert(!result.has_port && result.has_profile);
    const char process_name[32] = "Microsoft Edge";
    assert(glados_browser_name_may_match(process_name, 15, "Microsoft Edge"));
    assert(glados_browser_name_may_match(process_name, 15, "Microsoft Edge Canary"));
    assert(!glados_browser_name_may_match(process_name, 16, "Microsoft Edge Canary"));
    assert(!glados_browser_name_may_match("unrelated", 10, "Microsoft Edge"));
    assert(glados_browser_name_may_match("", 1, "Microsoft Edge"));
    const char unknown_name[4] = {'E', 'd', 'g', 'e'};
    assert(glados_browser_name_may_match(unknown_name, sizeof(unknown_name), "Microsoft Edge"));
    const char *invalid_ports[] = {"0", "1", "1023", "65536", "999999999999999999", "-1", "+49321", "49x21", ""};
    for (size_t i = 0; i < sizeof(invalid_ports) / sizeof(invalid_ports[0]); ++i) {
        char flag[128]; snprintf(flag, sizeof(flag), "--remote-debugging-port=%s", invalid_ports[i]);
        const char *arguments[] = {"Edge", flag}; invalid(arguments, 2);
    }
    const char *limits[] = {"Edge", "--remote-debugging-port=1024"}; result = parse(limits, 2, NULL); assert(result.port == 1024);
    limits[1] = "--remote-debugging-port=65535"; result = parse(limits, 2, NULL); assert(result.port == 65535);
    size_t size = fixture(normal, 5, NULL);
    for (size_t cut = 0; cut < size; ++cut) assert(!glados_browser_parse_procargs(bytes, cut, &result));
    assert(!glados_browser_parse_procargs(bytes, GLADOS_BROWSER_ARGS_LIMIT + 1U, &result));
    int bad_counts[] = {0, -1, INT_MAX, GLADOS_BROWSER_ARGC_LIMIT + 1};
    for (size_t i = 0; i < sizeof(bad_counts) / sizeof(bad_counts[0]); ++i) {
        size = fixture(normal, 5, NULL); memcpy(bytes, &bad_counts[i], sizeof(int));
        assert(!glados_browser_parse_procargs(bytes, size, &result));
    }
    char oversized[GLADOS_BROWSER_PATH_LIMIT + 32];
    memset(oversized, 'x', sizeof(oversized)); memcpy(oversized, "--user-data-dir=/", 17); oversized[sizeof(oversized) - 1] = 0;
    const char *too_long[] = {"Edge", oversized}; invalid(too_long, 2);
    memset(bytes, 0x5A, sizeof(bytes)); glados_browser_clear(bytes, sizeof(bytes));
    for (size_t i = 0; i < sizeof(bytes); ++i) assert(bytes[i] == 0);
    path_fixtures();
#ifdef __APPLE__
    process_fixtures();
#endif
    puts("browser_process_info synthetic fixtures passed");
    return 0;
}
