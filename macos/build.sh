#!/bin/zsh
set -euo pipefail
SOURCE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_ROOT="$SOURCE_ROOT/macos/build/我的任务地图.app"
INSTALL_ROOT="/Users/macmini/Applications/我的任务地图.app"
REPLACE_KNOWN="${1:-}"
if [[ -e "$APP_ROOT" ]]; then
  if [[ "$REPLACE_KNOWN" != "--replace-known" ]] || [[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$APP_ROOT/Contents/Info.plist" 2>/dev/null)" != "com.macmini.mytaskmap" ]]; then
    print -u2 "构建目录已存在且未验证为本应用，停止覆盖：$APP_ROOT"
    exit 3
  fi
  mv "$APP_ROOT" "$SOURCE_ROOT/macos/build/我的任务地图.backup-$(date +%Y%m%d-%H%M%S).app"
fi
mkdir -p "$APP_ROOT/Contents/MacOS" "$APP_ROOT/Contents/Resources"
swiftc "$SOURCE_ROOT/macos/Sources/main.swift" -o "$APP_ROOT/Contents/MacOS/我的任务地图" -framework Cocoa -framework WebKit
cp "$SOURCE_ROOT/macos/Info.plist" "$APP_ROOT/Contents/Info.plist"
print -r -- "$SOURCE_ROOT" > "$APP_ROOT/Contents/Resources/source-path.txt"
codesign --force --sign - --timestamp=none "$APP_ROOT"
plutil -lint "$APP_ROOT/Contents/Info.plist"
if [[ -e "$INSTALL_ROOT" ]]; then
  if [[ "$REPLACE_KNOWN" != "--replace-known" ]] || [[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$INSTALL_ROOT/Contents/Info.plist" 2>/dev/null)" != "com.macmini.mytaskmap" ]]; then
    print -u2 "目标已存在且未验证为本应用，停止覆盖：$INSTALL_ROOT"
    exit 3
  fi
  mv "$INSTALL_ROOT" "$SOURCE_ROOT/macos/build/已安装版.backup-$(date +%Y%m%d-%H%M%S).app"
fi
mkdir -p /Users/macmini/Applications
cp -R "$APP_ROOT" "$INSTALL_ROOT"
codesign --verify --deep --strict "$INSTALL_ROOT"
print "已安装：$INSTALL_ROOT"
