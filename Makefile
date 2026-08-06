# ----------------------------
# Makefile Options
# ----------------------------

NAME = DDLC
ICON = icon.png
DESCRIPTION = "Doki Doki Literature Club (fan port)"
COMPRESSED = YES
ARCHIVED = YES

CFLAGS = -Wall -Wextra -Oz
CXXFLAGS = -Wall -Wextra -Oz

# ----------------------------

include $(shell cedev-config --makefile)
