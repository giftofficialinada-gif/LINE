#!/bin/bash
cd "$(dirname "$0")"

echo "=================================="
echo "  Google認証情報をBase64に変換"
echo "=================================="
echo ""

# JSONファイルを探す
JSON_FILES=(*.json)
JSON_FILES=($(ls *.json 2>/dev/null | grep -v settings.json))

if [ ${#JSON_FILES[@]} -eq 0 ]; then
    echo "❌ JSONファイルが見つかりません"
    echo "このフォルダにGoogle認証情報のJSONを入れてください"
    read -p "Enterキーで閉じる"
    exit 1
fi

if [ ${#JSON_FILES[@]} -eq 1 ]; then
    CREDS_FILE="${JSON_FILES[0]}"
else
    echo "複数のJSONファイルがあります。番号を選んでください："
    for i in "${!JSON_FILES[@]}"; do
        echo "  $((i+1)): ${JSON_FILES[$i]}"
    done
    read -p "番号を入力: " NUM
    CREDS_FILE="${JSON_FILES[$((NUM-1))]}"
fi

echo ""
echo "「${CREDS_FILE}」を変換しています..."
ENCODED=$(base64 -i "$CREDS_FILE" | tr -d '\n')
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "GOOGLE_CREDENTIALS_JSON の値："
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "$ENCODED"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "↑ この文字列をコピーしてRailwayの環境変数に貼り付けてください"
echo ""
read -p "Enterキーで閉じる"
