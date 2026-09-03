MODEL_NAME ?= ggml-small.en.bin
MODEL_URL  ?= https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$(MODEL_NAME)
VAD_NAME   ?= ggml-silero-v5.1.2.bin
VAD_URL    ?= https://huggingface.co/ggml-org/whisper-vad/resolve/main/$(VAD_NAME)

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

# Speech model, plus the voice detector that stops whisper inventing
# sentences during the long silences on the microphone track.
models: models/$(MODEL_NAME) models/$(VAD_NAME)

models/$(MODEL_NAME):
	@mkdir -p models
	curl -L --progress-bar -o $@ $(MODEL_URL)

models/$(VAD_NAME):
	@mkdir -p models
	curl -L --progress-bar -o $@ $(VAD_URL)

# Optional. Groups the `them` track by voice, so Claude gets a hint about who
# is who. It costs a 49 MB virtual environment, 130 MB of models, and about
# seven minutes of processing per hour of audio, so it is not part of `all`.
SEG_URL  ?= https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2
EMB_URL  ?= https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_CAM++.onnx

# Recognising a voice in next week's meeting is a different job from grouping
# it in this one, and needs a different model. Measured on nine real meetings,
# the grouping model above scored two different colleagues MORE alike than one
# colleague on two days; this one had the widest margin of six tested. The
# numbers are in the MATCH_THRESHOLD comment in lib/voices.py.
VOICE_URL ?= https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/nemo_en_titanet_large.onnx

diarize: .venv/bin/python models/segmentation.onnx models/embedding.onnx models/voiceprint.onnx
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

models/segmentation.onnx:
	@mkdir -p models
	curl -L --progress-bar -o models/seg.tar.bz2 $(SEG_URL)
	tar xjf models/seg.tar.bz2 -C models
	mv models/sherpa-onnx-pyannote-segmentation-3-0/model.onnx $@
	rm -rf models/seg.tar.bz2 models/sherpa-onnx-pyannote-segmentation-3-0

models/embedding.onnx:
	@mkdir -p models
	curl -L --progress-bar -o $@ $(EMB_URL)

models/voiceprint.onnx:
	@mkdir -p models
	curl -L --progress-bar -o $@ $(VOICE_URL)

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
