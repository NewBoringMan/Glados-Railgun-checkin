#define _DARWIN_C_SOURCE
#define _XOPEN_SOURCE 700
#include "browser_process_info.h"

#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#ifdef __APPLE__
#include <libproc.h>
#include <sys/proc.h>
#include <sys/proc_info.h>
#include <sys/sysctl.h>
#endif

void glados_browser_clear(void *bytes, size_t length) {
    volatile unsigned char *cursor = (volatile unsigned char *)bytes;
    while (length--) *cursor++ = 0;
}

static int text_ok(const char *text, size_t length) {
    if (!length || length >= GLADOS_BROWSER_PATH_LIMIT) return 0;
    for (size_t i = 0; i < length; ++i) {
        unsigned char c = (unsigned char)text[i];
        if (c < 32 || c == 127) return 0;
    }
    return 1;
}

static int copy_argument(char *target, const unsigned char *source, size_t length) {
    if (!text_ok((const char *)source, length)) return 0;
    memcpy(target, source, length);
    target[length] = '\0';
    return 1;
}

static int equal_argument(const unsigned char *value, size_t length, const char *literal) {
    return length == strlen(literal) && memcmp(value, literal, length) == 0;
}

static int begins_argument(const unsigned char *value, size_t length, const char *prefix) {
    size_t prefix_length = strlen(prefix);
    return length >= prefix_length && memcmp(value, prefix, prefix_length) == 0;
}

int glados_browser_parse_procargs(const unsigned char *bytes, size_t length,
                                struct glados_browser_arguments *result) {
    if (!result) return 0;
    memset(result, 0, sizeof(*result));
    if (!bytes || length < sizeof(int) + 3 || length > GLADOS_BROWSER_ARGS_LIMIT) return 0;
    int argc = 0;
    memcpy(&argc, bytes, sizeof(argc));
    if (argc < 1 || argc > GLADOS_BROWSER_ARGC_LIMIT) return 0;

    size_t position = sizeof(argc);
    const unsigned char *end = memchr(bytes + position, 0, length - position);
    if (!end || end == bytes + position) return 0;
    size_t executable_size = (size_t)(end - (bytes + position)) + 1;
    position += executable_size;
    /* XNU aligns executable_path= plus the saved path to the target pointer
     * size. KERN_PROCARGS2 strips that 16-byte prefix, leaving the same padding
     * for a 64-bit process. Do not skip arbitrary NULs: argv[0] may be empty,
     * which must not shift the argc boundary into the environment. */
    size_t padding = (8 - executable_size % 8) % 8;
    if (padding > length - position) return 0;
    for (size_t i = 0; i < padding; ++i) if (bytes[position + i] != 0) return 0;
    position += padding;
    if (position == length) return 0;

    struct glados_browser_arguments parsed = {0};
    parsed.is_main_process = 1;
    int switches = 1;
    for (int index = 0; index < argc; ++index) {
        if (position >= length) goto invalid;
        const unsigned char *value = bytes + position;
        end = memchr(value, 0, length - position);
        if (!end) goto invalid;
        size_t size = (size_t)(end - value);
        position += size + 1;
        if (index == 0) {
            if (!size) goto invalid;
            continue;
        }
        if (!switches) continue;
        if (equal_argument(value, size, "--")) { switches = 0; continue; }
        if (equal_argument(value, size, "--type") || begins_argument(value, size, "--type=")) {
            parsed.is_main_process = 0;
        } else if (equal_argument(value, size, "--remote-debugging-pipe") ||
                   begins_argument(value, size, "--remote-debugging-pipe=")) {
            goto invalid;
        } else if (equal_argument(value, size, "--user-data-dir") ||
                   equal_argument(value, size, "--remote-debugging-port")) {
            /* Only the equals form used by this App is supported. Do not guess
             * whether the following positional argument is a switch value. */
            goto invalid;
        } else if (begins_argument(value, size, "--user-data-dir=")) {
            const size_t prefix = sizeof("--user-data-dir=") - 1;
            if (parsed.has_profile || !copy_argument(parsed.profile, value + prefix, size - prefix) ||
                parsed.profile[0] != '/') goto invalid;
            parsed.has_profile = 1;
        } else if (begins_argument(value, size, "--remote-debugging-port=")) {
            const size_t prefix = sizeof("--remote-debugging-port=") - 1;
            if (parsed.has_port || size <= prefix || size - prefix > 5) goto invalid;
            unsigned int port = 0;
            for (size_t i = prefix; i < size; ++i) {
                if (value[i] < '0' || value[i] > '9') goto invalid;
                port = port * 10 + (unsigned int)(value[i] - '0');
            }
            if (port < 1024 || port > 65535) goto invalid;
            parsed.port = port;
            parsed.has_port = 1;
        }
    }
    *result = parsed;
    glados_browser_clear(&parsed, sizeof(parsed));
    return 1;
invalid:
    glados_browser_clear(&parsed, sizeof(parsed));
    return 0;
}

int glados_browser_name_may_match(const char *name, size_t capacity, const char *basename) {
    if (!name || !capacity || !basename || !*basename) return 1;
    const char *end = memchr(name, 0, capacity);
    if (!end || end == name) return 1;
    size_t length = (size_t)(end - name), expected = strlen(basename);
    /* BSD name fields truncate to capacity-1 bytes. Empty/unbounded fields
     * are unknown, never evidence that the process can be excluded. */
    return length <= expected && memcmp(name, basename, length) == 0 &&
           (length == expected || length == capacity - 1);
}

enum glados_browser_argument_decision glados_browser_decide_arguments(
    const struct glados_browser_arguments *arguments, int profile_matches) {
    if (!arguments || !arguments->is_main_process || !arguments->has_profile || !profile_matches)
        return GLADOS_BROWSER_OTHER;
    return arguments->has_port ? GLADOS_BROWSER_REUSABLE : GLADOS_BROWSER_NO_DEBUGGING;
}

static int owned_directory(const char *path, int reject_symlink) {
    struct stat info;
    if ((reject_symlink ? lstat(path, &info) : stat(path, &info)) != 0) return 0;
    return S_ISDIR(info.st_mode) && info.st_uid == getuid();
}

int glados_browser_safe_profile(const char *path, const char *canonical_root,
                               char root_result[GLADOS_BROWSER_PATH_LIMIT],
                               char profile_result[GLADOS_BROWSER_PATH_LIMIT]) {
    if (!path || !root_result || !profile_result || getuid() != geteuid()) return 0;
    size_t length = strnlen(path, GLADOS_BROWSER_PATH_LIMIT);
    if (!text_ok(path, length) || path[0] != '/') return 0;
    /* Callers may revalidate an earlier result in place. Preserve inputs before
     * clearing the output buffers. */
    char original[GLADOS_BROWSER_PATH_LIMIT], expected_root[GLADOS_BROWSER_PATH_LIMIT];
    memcpy(original, path, length + 1);
    path = original;
    if (canonical_root) {
        size_t count = strnlen(canonical_root, sizeof(expected_root));
        if (!text_ok(canonical_root, count) || canonical_root[0] != '/') return 0;
        memcpy(expected_root, canonical_root, count + 1);
        canonical_root = expected_root;
    }
    root_result[0] = profile_result[0] = '\0';
    const char *anchor = strstr(path, "/BrowserProfiles/");
    size_t root_length;
    if (anchor) {
        if (strstr(anchor + 1, "/BrowserProfiles/")) return 0;
        root_length = (size_t)(anchor - path) + sizeof("/BrowserProfiles") - 1;
    } else if (canonical_root && canonical_root[0] == '/') {
        root_length = strnlen(canonical_root, GLADOS_BROWSER_PATH_LIMIT);
        if (!root_length || root_length >= length ||
            memcmp(path, canonical_root, root_length) != 0 || path[root_length] != '/') return 0;
    } else return 0;
    if (root_length + 1 >= length) return 0;
    char partial[GLADOS_BROWSER_PATH_LIMIT];
    memcpy(partial, path, root_length);
    partial[root_length] = '\0';
    if (!realpath(partial, root_result) || !owned_directory(root_result, 0) ||
        (canonical_root && strcmp(root_result, canonical_root) != 0)) return 0;

    /* Resolve the root alias once; lstat each original descendant so a symlink
     * between different account directories can never masquerade as equality. */
    size_t start = root_length + 1;
    while (start < length) {
        const char *slash = strchr(path + start, '/');
        size_t end = slash ? (size_t)(slash - path) : length;
        size_t count = end - start;
        if (!count || (count == 1 && path[start] == '.') ||
            (count == 2 && path[start] == '.' && path[start + 1] == '.')) return 0;
        memcpy(partial, path, end);
        partial[end] = '\0';
        if (!owned_directory(partial, 1)) return 0;
        if (!slash) break;
        start = end + 1;
        if (start == length) return 0;
    }
    if (!realpath(path, profile_result)) return 0;
    size_t canonical_length = strlen(root_result);
    return strncmp(profile_result, root_result, canonical_length) == 0 &&
           profile_result[canonical_length] == '/';
}

static int blocked(const char *reason) {
    return printf("{\"schema\":\"glados.browser-process\",\"version\":1,\"state\":\"blocked\",\"reason\":\"%s\"}\n", reason) < 0 ? 1 : 0;
}

#ifdef __APPLE__
struct process_snapshot {
    pid_t pid;
    uid_t uid;
    uint64_t seconds;
    uint64_t microseconds;
    char executable[GLADOS_BROWSER_PATH_LIMIT];
};

/* 1: matching executable metadata; 0: gone or demonstrably another process;
 * -1: a possible matching process has unavailable/unsupported metadata. */
static int snapshot(pid_t pid, const char *expected_executable, struct process_snapshot *result) {
    struct proc_bsdinfo info;
    memset(&info, 0, sizeof(info));
    errno = 0;
    int amount = proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, (int)sizeof(info));
    if (amount != (int)sizeof(info)) return errno == ESRCH ? 0 : -1;
    if (info.pbi_pid != (uint32_t)pid) return -1;
    if (info.pbi_uid != getuid() || info.pbi_ruid != getuid() || info.pbi_status == SZOMB ||
        (info.pbi_flags & PROC_FLAG_INEXIT)) return 0;
    const char *basename = strrchr(expected_executable, '/');
    if (!basename || !basename[1]) return -1;
    ++basename;
    /* This exclusion prevents a dead/unlinked unrelated executable from
     * blocking every browser lookup. Names are never positive identity: all
     * remaining candidates still require canonical proc_pidpath equality. */
    if (!glados_browser_name_may_match(info.pbi_name, sizeof(info.pbi_name), basename) &&
        !glados_browser_name_may_match(info.pbi_comm, sizeof(info.pbi_comm), basename)) return 0;
    char path[PROC_PIDPATHINFO_MAXSIZE];
    memset(path, 0, sizeof(path));
    errno = 0;
    int path_length = proc_pidpath(pid, path, sizeof(path));
    if (path_length <= 0) return errno == ESRCH ? 0 : -1;
    if (!memchr(path, 0, sizeof(path)) || !realpath(path, result->executable)) return -1;
    if (strcmp(result->executable, expected_executable) != 0) return 0;
    if (!(info.pbi_flags & PROC_FLAG_LP64)) return -1;
    result->pid = pid;
    result->uid = info.pbi_uid;
    result->seconds = info.pbi_start_tvsec;
    result->microseconds = info.pbi_start_tvusec;
    if (!result->seconds || result->microseconds >= 1000000) return -1;
    return 1;
}

static int same_snapshot(const struct process_snapshot *a, const struct process_snapshot *b) {
    return a->pid == b->pid && a->uid == b->uid && a->seconds == b->seconds &&
           a->microseconds == b->microseconds && strcmp(a->executable, b->executable) == 0;
}

static int read_arguments(pid_t pid, struct glados_browser_arguments *arguments) {
    int limit = 0;
    size_t limit_size = sizeof(limit);
    int size_query[2] = {CTL_KERN, KERN_ARGMAX};
    if (sysctl(size_query, 2, &limit, &limit_size, NULL, 0) != 0 ||
        limit_size != sizeof(limit) || limit <= 0 || (unsigned)limit > GLADOS_BROWSER_ARGS_LIMIT) return -1;
    unsigned char *bytes = calloc(1, (size_t)limit);
    if (!bytes) return -1;
    size_t size = (size_t)limit;
    int query[3] = {CTL_KERN, KERN_PROCARGS2, pid};
    int status = -1;
    if (sysctl(query, 3, bytes, &size, NULL, 0) == 0 && size <= (size_t)limit)
        status = glados_browser_parse_procargs(bytes, size, arguments);
    /* This also erases any environment bytes returned by the kernel. They are
     * never parsed, copied to output, or persisted. */
    glados_browser_clear(bytes, (size_t)limit);
    free(bytes);
    return status;
}

int glados_browser_process_info(const char *expectedExecutable, const char *expectedProfile) {
    char executable[GLADOS_BROWSER_PATH_LIMIT];
    char root[GLADOS_BROWSER_PATH_LIMIT], profile[GLADOS_BROWSER_PATH_LIMIT];
    struct stat executable_info;
    if (getuid() != geteuid() || !expectedExecutable || expectedExecutable[0] != '/' ||
        !text_ok(expectedExecutable, strnlen(expectedExecutable, sizeof(executable))) ||
        !realpath(expectedExecutable, executable) || stat(executable, &executable_info) != 0 ||
        !S_ISREG(executable_info.st_mode) || access(executable, X_OK) != 0 ||
        !glados_browser_safe_profile(expectedProfile, NULL, root, profile)) return blocked("invalid_expected_paths");

    errno = 0;
    int required = proc_listpids(PROC_UID_ONLY, getuid(), NULL, 0);
    if (required <= 0 || required > 1024 * 1024 || required % sizeof(pid_t) != 0) return blocked("metadata_unavailable");
    size_t capacity = (size_t)required + 128 * sizeof(pid_t);
    pid_t *pids = calloc(1, capacity);
    if (!pids) return blocked("metadata_unavailable");
    errno = 0;
    int received = proc_listpids(PROC_UID_ONLY, getuid(), pids, (int)capacity);
    if (received <= 0 || (size_t)received >= capacity || received % sizeof(pid_t) != 0) {
        free(pids); return blocked("metadata_unavailable");
    }
    const char *failure = NULL;
    size_t matches = 0;
    unsigned int port = 0;
    struct process_snapshot found = {0};
    for (size_t index = 0; index < (size_t)received / sizeof(pid_t); ++index) {
        pid_t pid = pids[index];
        if (pid <= 0) continue;
        struct process_snapshot before = {0}, after = {0};
        int known = snapshot(pid, executable, &before);
        if (!known) continue;
        if (known < 0) { failure = "metadata_unavailable"; break; }
        struct glados_browser_arguments arguments = {0};
        int parsed = read_arguments(pid, &arguments);
        if (snapshot(pid, executable, &after) != 1 || !same_snapshot(&before, &after)) {
            glados_browser_clear(&arguments, sizeof(arguments));
            failure = "process_changed"; break;
        }
        if (parsed != 1) {
            glados_browser_clear(&arguments, sizeof(arguments));
            failure = parsed < 0 ? "metadata_unavailable" : "invalid_arguments"; break;
        }
        if (!arguments.is_main_process || !arguments.has_profile) {
            glados_browser_clear(&arguments, sizeof(arguments)); continue;
        }
        char candidate[GLADOS_BROWSER_PATH_LIMIT];
        if (!realpath(arguments.profile, candidate)) {
            glados_browser_clear(&arguments, sizeof(arguments)); failure = "metadata_unavailable"; break;
        }
        int matching = strcmp(candidate, profile) == 0;
        if (matching) {
            char candidate_root[GLADOS_BROWSER_PATH_LIMIT];
            if (!glados_browser_safe_profile(arguments.profile, root, candidate_root, candidate)) {
                glados_browser_clear(&arguments, sizeof(arguments)); failure = "invalid_expected_paths"; break;
            }
        }
        enum glados_browser_argument_decision decision = glados_browser_decide_arguments(&arguments, matching);
        if (decision != GLADOS_BROWSER_OTHER) {
            ++matches;
            found = before;
            port = arguments.port;
            if (matches > 1) failure = "ambiguous_processes";
            else if (decision == GLADOS_BROWSER_NO_DEBUGGING) failure = "debugging_unavailable";
        }
        glados_browser_clear(&arguments, sizeof(arguments));
        if (failure) break;
    }
    free(pids);
    if (failure) return blocked(failure);
    /* The selected process must still be the same after inspecting other PIDs. */
    if (matches) {
        struct process_snapshot final = {0};
        char final_root[GLADOS_BROWSER_PATH_LIMIT], final_profile[GLADOS_BROWSER_PATH_LIMIT];
        if (snapshot(found.pid, executable, &final) != 1 || !same_snapshot(&found, &final) ||
            !glados_browser_safe_profile(expectedProfile, root, final_root, final_profile) ||
            strcmp(final_profile, profile) != 0) return blocked("process_changed");
        return printf("{\"schema\":\"glados.browser-process\",\"version\":1,\"state\":\"found\",\"pid\":%d,\"port\":%u,\"startedAt\":\"%" PRIu64 ":%" PRIu64 "\"}\n",
                      (int)found.pid, port, found.seconds, found.microseconds) < 0 ? 1 : 0;
    }
    return puts("{\"schema\":\"glados.browser-process\",\"version\":1,\"state\":\"none\"}") < 0 ? 1 : 0;
}
#else
int glados_browser_process_info(const char *expectedExecutable, const char *expectedProfile) {
    (void)expectedExecutable; (void)expectedProfile;
    return blocked("unsupported_platform");
}
#endif
