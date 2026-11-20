/*
 * The main executable of PendiFy.app on macOS: starts PendiFy's own Python as its child and waits for it.
 *
 * macOS gives a permission such as Accessibility to the app a process was started by, so the click on accept is
 * asked for and listed as "PendiFy", with its icon, rather than as the Python it runs. The child is
 *     ~/Library/Application Support/pendify/venv/bin/python -m pendify [the app's own arguments]
 * with PENDIFY_APP set to this executable's path, so a restart and the start at logon go through the app as well.
 * With no terminal, the child's output goes to pendify.log beside the config. SIGTERM, SIGINT and SIGHUP are passed on to the child; the app ends with the child's exit status.
 *
 * Built once, universal, by tools/build_macos_launcher.sh into src/pendify/macos/PendiFy; the same bytes in every
 * install, so the app's signature, and the permission given to it, outlive a reinstall.
 */
#include <fcntl.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
static pid_t child = 0;

static void pass_on(int sig) {
    if (child > 0) kill(child, sig);
}

int main(int argc, char **argv) {
    const char *home = getenv("HOME");
    if (home == NULL || home[0] == '\0') return 1;
    char python[4096];
    snprintf(python, sizeof python, "%s/Library/Application Support/pendify/venv/bin/python", home);

    char self[4096];
    uint32_t size = sizeof self;
    if (_NSGetExecutablePath(self, &size) == 0) {
        char resolved[4096];
        if (realpath(self, resolved) != NULL) setenv("PENDIFY_APP", resolved, 1);
    }

    char **args = calloc((size_t)argc + 3, sizeof(char *));
    if (args == NULL) return 1;
    args[0] = python;
    args[1] = "-m";
    args[2] = "pendify";
    for (int i = 1; i < argc; i++) args[i + 2] = argv[i];

    /* Opened from Finder, Spotlight or at logon there is no terminal: the output goes to pendify.log. */
    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    if (!isatty(STDOUT_FILENO)) {
        char log[4096];
        snprintf(log, sizeof log, "%s/Library/Application Support/pendify/pendify.log", home);
        posix_spawn_file_actions_addopen(&actions, STDOUT_FILENO, log, O_WRONLY | O_CREAT | O_APPEND, 0644);
        posix_spawn_file_actions_adddup2(&actions, STDOUT_FILENO, STDERR_FILENO);
    }

    signal(SIGTERM, pass_on);
    signal(SIGINT, pass_on);
    signal(SIGHUP, pass_on);
    if (posix_spawn(&child, python, &actions, NULL, args, environ) != 0) {
        fprintf(stderr, "PendiFy: %s could not be started; run the installer again\n", python);
        return 1;
    }
    int status = 0;
    while (waitpid(child, &status, 0) < 0) {
        /* interrupted by a signal passed on: wait again */
    }
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
