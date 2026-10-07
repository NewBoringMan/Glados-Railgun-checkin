#ifndef GLADOS_BROWSER_PROCESS_INFO_H
#define GLADOS_BROWSER_PROCESS_INFO_H

#include <stddef.h>

#define GLADOS_BROWSER_PATH_LIMIT 4096
#define GLADOS_BROWSER_ARGS_LIMIT (1024U * 1024U)
#define GLADOS_BROWSER_ARGC_LIMIT 512

/* Pure parsing helpers, shared with synthetic offline fixtures. */
struct glados_browser_arguments {
    int is_main_process;
    int has_profile;
    int has_port;
    unsigned int port;
    char profile[GLADOS_BROWSER_PATH_LIMIT];
};

enum glados_browser_argument_decision {
    GLADOS_BROWSER_OTHER = 0,
    GLADOS_BROWSER_REUSABLE = 1,
    GLADOS_BROWSER_NO_DEBUGGING = 2
};

/* The input is the 64-bit KERN_PROCARGS2 layout, including its native int argc.
 * Stops exactly after argc arguments; never examines the environment tail.
 * Returns 1 for a complete, supported argument representation, 0 otherwise.
 */
int glados_browser_parse_procargs(const unsigned char *bytes, size_t length,
                                struct glados_browser_arguments *result);
enum glados_browser_argument_decision glados_browser_decide_arguments(
    const struct glados_browser_arguments *arguments, int profile_matches);
void glados_browser_clear(void *bytes, size_t length);
/* Negative prefilter only; unknown/truncated names remain candidates. */
int glados_browser_name_may_match(const char *name, size_t capacity, const char *basename);

/* Metadata-only path validation. BrowserProfiles itself may be a root alias;
 * no component below that anchor may be a symlink. canonical_root may be NULL
 * on the first call, and permits the equivalent canonical root on later calls.
 * Directories must exist and belong to the current real/effective UID.
 */
int glados_browser_safe_profile(const char *path, const char *canonical_root,
                               char root_result[GLADOS_BROWSER_PATH_LIMIT],
                               char profile_result[GLADOS_BROWSER_PATH_LIMIT]);

/* Exactly one bounded JSON line; no paths, argv, environment, or OS errors.
 * A zero exit status means the protocol was emitted, not that reuse is safe.
 */
int glados_browser_process_info(const char *expectedExecutable,
                               const char *expectedProfile);

#endif
