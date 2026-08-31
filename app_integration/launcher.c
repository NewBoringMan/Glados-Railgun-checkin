#include <limits.h>
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
