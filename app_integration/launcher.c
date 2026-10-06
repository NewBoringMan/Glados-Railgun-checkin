#include <limits.h>
#include <dlfcn.h>
#include <libgen.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char *argv[]) {
    char executablePath[PATH_MAX];
    uint32_t size = sizeof(executablePath);
    if (_NSGetExecutablePath(executablePath, &size) != 0) {
        fprintf(stderr, "Unable to locate launcher executable.\n");
        return 127;
    }

    char directoryBuffer[PATH_MAX];
    strlcpy(directoryBuffer, executablePath, sizeof(directoryBuffer));
    char *macOSDirectory = dirname(directoryBuffer);

    // Headless commands must never initialize the main UI or its menu plugin.
    if (argc == 2 && strcmp(argv[1], "--refresh-notifications") == 0) {
        char notificationLibrary[PATH_MAX];
        snprintf(notificationLibrary, sizeof(notificationLibrary), "%s/../Frameworks/GLaDOSNotifications.dylib", macOSDirectory);
        void *library = dlopen(notificationLibrary, RTLD_NOW | RTLD_LOCAL);
        int (*entry)(void) = library ? (int (*)(void))dlsym(library, "GLaDOSRunNotificationCLI") : NULL;
        if (!entry) { fprintf(stderr, "Notification helper unavailable.\n"); return 127; }
        return entry();
    }
    if (argc == 3 && strcmp(argv[1], "--checkin-watch") == 0) {
        const char *actions[] = {"status", "set-enabled", "authorize", "test", "poll"};
        int valid = 0;
        for (size_t i = 0; i < sizeof(actions) / sizeof(actions[0]); ++i)
            if (strcmp(argv[2], actions[i]) == 0) valid = 1;
        if (!valid) return 2;
        char watchScript[PATH_MAX];
        snprintf(watchScript, sizeof(watchScript), "%s/../Resources/checkin_watch.py", macOSDirectory);
        const char *interpreters[] = {"/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"};
        for (size_t i = 0; i < sizeof(interpreters) / sizeof(interpreters[0]); ++i) {
            if (access(interpreters[i], X_OK) == 0) {
                char *args[] = {(char *)interpreters[i], "-I", watchScript, argv[2], NULL};
                execv(interpreters[i], args);
                break;
            }
        }
        fprintf(stderr, "Check-in notification helper unavailable.\n");
        return 127;
    }
    if (argc > 1 && (strcmp(argv[1], "--refresh-mail") == 0 ||
                     strcmp(argv[1], "--tick") == 0 ||
                     strcmp(argv[1], "--checkin-watch") == 0 ||
                     strcmp(argv[1], "--refresh-notifications") == 0)) return 2;

    char realExecutable[PATH_MAX];
    char pluginPath[PATH_MAX];
    snprintf(realExecutable, sizeof(realExecutable), "%s/GLaDOSAccountCenter.real", macOSDirectory);
    snprintf(pluginPath, sizeof(pluginPath), "%s/../Frameworks/PolicyMenuPlugin.dylib", macOSDirectory);

    if (access(realExecutable, X_OK) != 0) {
        perror("GLaDOSAccountCenter.real");
        return 127;
    }

    if (access(pluginPath, R_OK) == 0) {
        setenv("DYLD_INSERT_LIBRARIES", pluginPath, 1);
    }

    argv[0] = realExecutable;
    execv(realExecutable, argv);
    perror("execv");
    return 127;
}
