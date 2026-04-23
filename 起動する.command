#!/bin/bash
cd "$(dirname "$0")"

# 既に起動中なら止める
pkill -f "venv/bin/python app.py" 2>/dev/null
sleep 1

echo "========================================="
echo "  退職支援 AI返信アシスタント"
echo "========================================="
echo ""
echo "アプリを起動しています..."
echo ""
echo "ブラウザで以下のURLを開いてください："
echo "  http://localhost:8080"
echo ""
echo "終了するにはこのウィンドウを閉じてください。"
echo ""

sleep 1
open http://localhost:8080
venv/bin/python app.py
