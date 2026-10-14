The verified runtime is 59c1c6392c603cb584e95f9b399956c63c9d7e73, based on cdde7eaea85296e6e20d0f301ff0d670d02a0549. The frozen release SHA256 is 096102d96965ad00dc1fa8c2739d7c75986fadcf96a776953b9caf864d565081.

The freshly built release totals 2,968,624 bytes against a 2,970,000-byte budget, leaving 1,376 bytes spare. All 157 internal variable names are unique, with exactly one DLUTQ. The official generator reproduces all eight motion/palette AppVars exactly; the packaged launcher and both code parts match the actual ELF. Unchanged art is reused from the shipped bundle.

The seeded Act2 diagnostic shows three characters without Sayori. Deleting FIRSTRUN resets playthrough to zero and restores all four characters; this survives quitting and restarting. Normal New Game displays the street background. Ordinary dialogue inputs and a newly generated version3 menu save/load restore the same narration. All eleven loaded bindings, including the launcher, both code parts and DLUTQ, match the frozen release in ten saved target states. The screenshots were independently visually reviewed.

Separate actual-target palette/noise fixtures cover scenes 26, 32 and 35. Drawing, saved-byte order/count and undo match the scalar reference exactly, with zero mismatches or errors and no out-of-reference changes. Palette/fade checks preserve all 48 UI colors and restore the title/game palette. Loaded noise code matches the fixture ELF except documented self-modifying coverage immediates. IRQ clobber remains an inferred mechanism, not an instruction trace.

All 70 host tests pass with python3 -m unittest discover -s tests -v. The final runtime was force-built before freezing; this documentation-only commit does not change it.

Act2 is explicitly seeded. Normal play covers opening dialogue and current-binary save/load, not a full playthrough or authentic full Act2 progression. Separate death-scene fixtures do not establish a fullgame death-animation playthrough. Persistent slot order remains compatible; version3 saves reject legacy version2 bytecode addresses, without claiming per-build save identity.

The adjacent persist-fix-proof.json records the eleven payload hashes, ten state hashes, source identities, exact noise results and retained evidence hashes. Large RAM dumps, game assets and binaries are not committed.
