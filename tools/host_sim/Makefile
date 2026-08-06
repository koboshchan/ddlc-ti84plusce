# Native build of the VM + text layer, for testing without a calculator.
#
#   make -C tools/host_sim && ./tools/host_sim/build/vnsim --trace

CC      ?= cc
CFLAGS  ?= -std=c11 -Wall -Wextra -Werror -O1 -g
BUILD   := build
TARGET  := $(BUILD)/vnsim

SRC := main.c ../../src/vn.c ../../src/text.c ../../src/demo.c

.PHONY: all run clean

all: $(TARGET)

$(TARGET): $(SRC) | $(BUILD)
	$(CC) $(CFLAGS) -o $@ $(SRC)

$(BUILD):
	mkdir -p $(BUILD)

run: $(TARGET)
	./$(TARGET)

clean:
	rm -rf $(BUILD)
