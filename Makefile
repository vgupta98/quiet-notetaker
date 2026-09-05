SWIFTC = swiftc -O -swift-version 5 -target arm64-apple-macos15.0

# Where `qn` goes on your PATH. This is where the claude CLI installs itself,
# and it needs no sudo.
BIN ?= $(HOME)/.local/bin

.PHONY: all build models test clean install uninstall diarize

all: build models

build: build/recorder build/watcher

build/recorder: recorder/main.swift recorder/Info.plist
	@mkdir -p build
	$(SWIFTC) -framework ScreenCaptureKit -framework AVFoundation \
	  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker recorder/Info.plist \
	  -o $@ recorder/main.swift

# The plist is not decoration: macOS terminates a process that asks for
# calendar access without a usage description.
build/watcher: recorder/watcher.swift recorder/WatcherInfo.plist
	@mkdir -p build
	$(SWIFTC) -framework ScreenCaptureKit -framework AVFoundation -framework EventKit \
	  -framework CoreAudio -framework CoreGraphics \
	  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker recorder/WatcherInfo.plist \
	  -o $@ recorder/watcher.swift

# `qn setup` owns the model files, so their names and URLs live in one place
# and a Homebrew install can fetch them without a Makefile.
models: build
	@./qn setup

# Optional. Groups the `them` track by voice, so Claude gets a hint about who
# is who. It costs a 49 MB virtual environment, 130 MB of models, and about
# seven minutes of processing per hour of audio, so it is not part of `all`.
diarize: build .venv/bin/python
	@./qn setup --voices
	@echo ""
	@echo "  voice grouping is ready. turn it on in your settings file:"
	@echo ""
	@echo "    diarize = yes"
	@echo ""
	@echo "  after a meeting, say who a voice was and it is remembered:"
	@echo ""
	@echo "    qn confirm <id> A \"Aisha\""
	@echo "    qn voices"
	@echo ""

.venv/bin/python:
	python3 -m venv .venv
	./.venv/bin/pip install -q --upgrade pip
	./.venv/bin/pip install -q sherpa-onnx numpy

test:
	@test/run.sh

# One command: build the binaries, fetch the models, put `qn` on your PATH.
# The symlink points back at this checkout, so `git pull` upgrades you and
# there is never a second copy of the code to debug.
install: all
	@mkdir -p "$(BIN)"
	@ln -sf "$(CURDIR)/qn" "$(BIN)/qn"
	@echo ""
	@echo "  linked $(BIN)/qn -> $(CURDIR)/qn"
	@case ":$$PATH:" in \
	  *":$(BIN):"*) ;; \
	  *) echo ""; \
	     echo "  warning: $(BIN) is not on your PATH."; \
	     echo "  add this to ~/.zshrc:  export PATH=\"$(BIN):\$$PATH\"" ;; \
	esac
	@echo ""
	@"$(BIN)/qn" doctor || true
	@echo "  to let Claude search your meetings, run:"
	@echo ""
	@echo "    claude mcp add quiet-notetaker -- python3 $(CURDIR)/mcp/server.py"
	@echo ""

# Removes the command and nothing else. Your meetings are never touched.
uninstall:
	@rm -f "$(BIN)/qn"
	@echo "  removed $(BIN)/qn"
	@echo "  your notes are untouched. to disconnect Claude, run:"
	@echo ""
	@echo "    claude mcp remove quiet-notetaker"
	@echo ""

clean:
	rm -rf build
