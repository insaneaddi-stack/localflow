#!/bin/bash
# Compile le helper audiotap (capture du son système). Requiert swiftc (Command Line Tools).
set -euo pipefail
cd "$(dirname "$0")"
swiftc -O -target arm64-apple-macos14.2 -o audiotap main.swift \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker Info.plist
codesign --force --sign - --identifier com.louqui.localflow.audiotap audiotap
echo "audiotap compilé : $(du -h audiotap | cut -f1)"
