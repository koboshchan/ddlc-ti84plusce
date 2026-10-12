# Assembly edge checks

With CEdev on PATH, run make prepare && make. Send bin/DDLC.8xp and clibs.8xg to a fresh calculator or emulator and launch it with the supported jailbreak launcher.

The single screen should report 344 checks and zero failures. This harness deliberately tests the project's assembly code directly, rather than a host substitute. Source and destination sentinel guards detect unintended writes. The framebuffer and graphx palette are initialized explicitly.
