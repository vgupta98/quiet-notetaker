MODEL_NAME ?= ggml-small.en.bin
MODEL_URL  ?= https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$(MODEL_NAME)
VAD_NAME   ?= ggml-silero-v5.1.2.bin
VAD_URL    ?= https://huggingface.co/ggml-org/whisper-vad/resolve/main/$(VAD_NAME)

.PHONY: all build models clean

all: build models

build: build/recorder

build/recorder: recorder/main.swift recorder/Info.plist
	@mkdir -p build
	swiftc -O -swift-version 5 -target arm64-apple-macos15.0 \
	  -framework ScreenCaptureKit -framework AVFoundation \
	  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker recorder/Info.plist \
	  -o $@ recorder/main.swift

# Speech model, plus the voice-detector that stops whisper inventing
# sentences during the long silences on the microphone track.
models: models/$(MODEL_NAME) models/$(VAD_NAME)

models/$(MODEL_NAME):
	@mkdir -p models
	curl -L --progress-bar -o $@ $(MODEL_URL)

models/$(VAD_NAME):
	@mkdir -p models
	curl -L --progress-bar -o $@ $(VAD_URL)

clean:
	rm -rf build
