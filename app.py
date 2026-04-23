import os
import sqlite3
import json
import glob
import base64
import tempfile
from datetime import date, datetime
from flask import Flask, render_template, request, jsonify
import anthropic
from pypdf import PdfReader
import io

app = Flask(__name__)
DB_PATH = "knowledge.db"
SETTINGS_PATH = "settings.json"
DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_db_conn():
    if DATABASE_URL:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        return conn, "pg"
    else:
        conn = sqlite3.connect(DB_PATH)
        return conn, "sqlite"


def db_execute(sql_sqlite, sql_pg, params=()):
    conn, kind = get_db_conn()
    try:
        c = conn.cursor()
        if kind == "pg":
            c.execute(sql_pg, params)
        else:
            c.execute(sql_sqlite, params)
        conn.commit()
        result = c.lastrowid if kind == "sqlite" else None
        if kind == "pg" and "RETURNING" in (sql_pg or ""):
            row = c.fetchone()
            result = row[0] if row else None
        return result
    finally:
        conn.close()


def db_fetchall(sql_sqlite, sql_pg, params=()):
    conn, kind = get_db_conn()
    try:
        c = conn.cursor()
        if kind == "pg":
            c.execute(sql_pg, params)
        else:
            c.execute(sql_sqlite, params)
        return c.fetchall()
    finally:
        conn.close()

SHEET_COLUMNS = [
    "名前", "獲得者", "契約日", "クーリン", "退職日", "仮申請可能日",
    "年齢", "契約金額", "支払い方法", "入金確認", "入金予定日", "入金日",
    "障害者手帳の有無", "傷病手当申請の有無", "クリニック名", "初診日",
    "受診結果", "次回受診日", "主治医に意見書", "説明会", "認定日",
    "就職希望日", "状況", "メモ"
]

PHASES = {
    1: {
        "label": "フェーズ1：クリニック受診済み",
        "description": "退職前。クリニックの予約・受診が完了している段階。",
        "guidance": """現在のフェーズ：退職前（クリニック受診済み）
このお客様はクリニックの予約・受診が完了しています。
次のステップとして「必要な持ち物の案内」を送る準備を進める段階です。
受診結果の確認や、次回受診日・クリニックの状況について丁寧にフォローしてください。"""
    },
    2: {
        "label": "フェーズ2：持ち物案内 送付済み",
        "description": "必要な持ち物の案内を送付済みの段階。",
        "guidance": """現在のフェーズ：必要な持ち物の案内を送付済み
持ち物の案内はすでにお送りしています。
お客様が案内を確認できているか、不明点がないかフォローしてください。
退職日に向けた準備状況も確認しながら安心感を与える返信をしてください。"""
    },
    3: {
        "label": "フェーズ3：転職希望日案内 送付済み",
        "description": "転職希望日の案内を送付済みの段階。",
        "guidance": """現在のフェーズ：転職希望日の案内を送付済み
転職希望日に関する案内はすでにお送りしています。
お客様の就職希望日の意向を確認し、今後のスケジュールについて丁寧に説明してください。
退職準備を着実に進めていただけるよう励ます返信を心がけてください。"""
    },
    4: {
        "label": "フェーズ4：退職前の準備完了",
        "description": "退職前の準備がすべて完了した段階。",
        "guidance": """現在のフェーズ：退職前の準備完了
退職前の準備がすべて整っています。
退職日当日に向けての最終確認や、お客様の不安を取り除く返信をしてください。
退職日当日に送る「国民健康保険の切り替え」「国民健康保険の減額」「退職後の流れ」の案内について事前に説明するタイミングでもあります。"""
    },
    5: {
        "label": "フェーズ5：退職日案内 送付済み",
        "description": "退職日に「国民健康保険の切り替え」「国民健康保険の減額」「退職後の流れ」の案内を送付済みの段階。",
        "guidance": """現在のフェーズ：退職日の案内を送付済み
退職日に「国民健康保険の切り替え」「国民健康保険の減額」「退職後の流れ」の案内をお送りしました。
お客様が退職後の手続きを正しく理解・実行できているか確認してください。
離職票の到着を待ちながら、不安なく手続きを進められるようサポートしてください。"""
    },
    6: {
        "label": "フェーズ6：離職票到着・意見書取得済み",
        "description": "離職票が到着し、主治医の意見書をハローワークで取得済みの段階。",
        "guidance": """現在のフェーズ：離職票到着・ハローワークで主治医の意見書を取得済み
離職票が到着し、ハローワークで主治医の意見書用紙を取得しています。
次のステップは主治医（クリニック）に意見書の記載をお願いすることです。
クリニックへの持参・依頼方法を丁寧に案内してください。"""
    },
    7: {
        "label": "フェーズ7：主治医の意見書 記載済み",
        "description": "主治医が意見書を記載済みの段階。",
        "guidance": """現在のフェーズ：主治医の意見書を記載済み
主治医が意見書を記載してくれました。
次のステップはこの意見書をハローワークに提出して、給付日数の延長を確認することです。
ハローワークへの提出方法・持ち物・手続きの流れを丁寧に案内してください。"""
    },
    8: {
        "label": "フェーズ8：ハローワーク提出・給付日数延長確認済み",
        "description": "意見書をハローワークに提出し、給付日数の延長が確認できた段階。",
        "guidance": """現在のフェーズ：ハローワークに意見書提出・給付日数延長確認済み
ハローワークへの意見書提出が完了し、給付日数の延長も確認できています。
次のステップは雇用保険説明会への参加と雇用保険受給資格者証の取得です。
説明会の日程確認や準備について案内してください。"""
    },
    9: {
        "label": "フェーズ9：卒業（給付確認完了）",
        "description": "雇用保険説明会に参加し、受給資格者証を取得。給付日数が伸びていることを最終確認済みの段階（卒業）。",
        "guidance": """現在のフェーズ：卒業（サポート完了）
雇用保険説明会への参加・受給資格者証の取得・給付日数の延長最終確認がすべて完了しています。
これはサポートの卒業段階です。
お客様の頑張りを称え、今後の生活や就職活動への応援メッセージを温かく伝えてください。"""
    }
}


DATE_FIELDS = [
    "退職日", "次回受診日", "仮申請可能日", "入金予定日",
    "初診日", "認定日", "就職希望日", "入金日", "契約日"
]

def parse_date(val):
    """様々な日付フォーマットを解析する"""
    if not val or not str(val).strip():
        return None
    val = str(val).strip().replace("年", "/").replace("月", "/").replace("日", "")
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y", "%Y%m%d", "%m/%d"):
        try:
            parsed = datetime.strptime(val, fmt)
            # 月/日のみの場合は今年を補完
            if fmt == "%m/%d":
                parsed = parsed.replace(year=date.today().year)
            return parsed.date()
        except ValueError:
            continue
    return None

def build_date_context(customer_data):
    """日付フィールドを今日と比較してAI向けのテキストを生成する"""
    today = date.today()
    lines = [f"今日の日付：{today.strftime('%Y年%m月%d日')}"]
    has_date = False
    for field in DATE_FIELDS:
        val = customer_data.get(field, "")
        parsed = parse_date(val)
        if not parsed:
            continue
        has_date = True
        diff = (parsed - today).days
        if diff < 0:
            status = f"【{abs(diff)}日前に経過済み】"
        elif diff == 0:
            status = "【今日】"
        elif diff <= 3:
            status = f"【あと{diff}日・直近】"
        elif diff <= 7:
            status = f"【あと{diff}日・今週中】"
        else:
            status = f"【あと{diff}日】"
        lines.append(f"・{field}：{val}　{status}")
    return "\n".join(lines) if has_date else None


def init_db():
    sqlite_sql = """
        CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    pg_sql = """
        CREATE TABLE IF NOT EXISTS knowledge (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    db_execute(sqlite_sql, pg_sql)


def get_all_knowledge():
    sql = "SELECT id, title, content, created_at FROM knowledge ORDER BY created_at DESC"
    rows = db_fetchall(sql, sql)
    return [{"id": r[0], "title": r[1], "content": r[2], "created_at": str(r[3])} for r in rows]


def load_settings():
    if os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH, "r") as f:
            return json.load(f)
    return {}


def save_settings(data):
    settings = load_settings()
    settings.update(data)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


@app.route("/")
def index():
    knowledge_list = get_all_knowledge()
    settings = load_settings()
    creds_files = glob.glob("*.json")
    creds_files = [f for f in creds_files if f != SETTINGS_PATH]
    # 環境変数からAPIキーを渡す（フロントのlocalStorageに保存させる）
    env_api_key = os.environ.get("CLAUDE_API_KEY", "")
    return render_template("index.html",
                           knowledge_list=knowledge_list,
                           settings=settings,
                           creds_files=creds_files,
                           env_api_key=env_api_key)


@app.route("/api/knowledge", methods=["POST"])
def add_knowledge():
    data = request.json
    title = data.get("title", "").strip()
    content = data.get("content", "").strip()
    if not title or not content:
        return jsonify({"error": "タイトルと内容を入力してください"}), 400
    new_id = db_execute(
        "INSERT INTO knowledge (title, content) VALUES (?, ?)",
        "INSERT INTO knowledge (title, content) VALUES (%s, %s) RETURNING id",
        (title, content)
    )
    return jsonify({"success": True, "id": new_id})


@app.route("/api/upload_pdf", methods=["POST"])
def upload_pdf():
    if "file" not in request.files:
        return jsonify({"error": "ファイルが選択されていません"}), 400
    file = request.files["file"]
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "PDFファイルを選択してください"}), 400
    try:
        pdf_bytes = file.read()
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        text = text.strip()
        if not text:
            return jsonify({"error": "PDFからテキストを読み取れませんでした（画像PDFは対応していません）"}), 400
        title = file.filename.rsplit(".", 1)[0]
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO knowledge (title, content) VALUES (?, ?)", (title, text))
        conn.commit()
        new_id = c.lastrowid
        conn.close()
        return jsonify({"success": True, "id": new_id, "title": title, "chars": len(text)})
    except Exception as e:
        return jsonify({"error": f"PDFの読み込みに失敗しました：{str(e)}"}), 500


@app.route("/api/upload_image", methods=["POST"])
def upload_image():
    if "file" not in request.files:
        return jsonify({"error": "ファイルが選択されていません"}), 400
    file = request.files["file"]
    api_key = request.form.get("api_key", "").strip()
    if not api_key:
        api_key = os.environ.get("CLAUDE_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Claude APIキーが必要です"}), 400

    ext = os.path.splitext(file.filename.lower())[1]
    media_type_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"
    }
    if ext not in media_type_map:
        return jsonify({"error": "JPG・PNG・GIF・WebP形式の画像を選択してください"}), 400

    try:
        image_bytes = file.read()
        image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
        media_type = media_type_map[ext]

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": image_b64}
                    },
                    {
                        "type": "text",
                        "text": "この画像の内容を詳しく説明してください。テキストが含まれている場合はすべて正確に書き起こしてください。図・表・グラフがある場合はその内容も説明してください。退職支援サービスのサポートスタッフが参考情報として使用します。"
                    }
                ]
            }]
        )
        description = response.content[0].text
        title = file.filename.rsplit(".", 1)[0]
        new_id = db_execute(
            "INSERT INTO knowledge (title, content) VALUES (?, ?)",
            "INSERT INTO knowledge (title, content) VALUES (%s, %s) RETURNING id",
            (f"🖼️ {title}", description)
        )
        return jsonify({"success": True, "id": new_id, "title": title, "chars": len(description)})
    except anthropic.AuthenticationError:
        return jsonify({"error": "APIキーが正しくありません"}), 401
    except Exception as e:
        return jsonify({"error": f"画像の読み込みに失敗しました：{str(e)}"}), 500


@app.route("/api/knowledge/<int:knowledge_id>", methods=["DELETE"])
def delete_knowledge(knowledge_id):
    db_execute(
        "DELETE FROM knowledge WHERE id = ?",
        "DELETE FROM knowledge WHERE id = %s",
        (knowledge_id,)
    )
    return jsonify({"success": True})


@app.route("/api/knowledge/export", methods=["GET"])
def export_knowledge():
    knowledge_list = get_all_knowledge()
    return jsonify({"knowledge": knowledge_list})


@app.route("/api/knowledge/import", methods=["POST"])
def import_knowledge():
    data = request.json
    items = data.get("knowledge", [])
    count = 0
    for item in items:
        title = item.get("title", "").strip()
        content = item.get("content", "").strip()
        if title and content:
            db_execute(
                "INSERT INTO knowledge (title, content) VALUES (?, ?)",
                "INSERT INTO knowledge (title, content) VALUES (%s, %s)",
                (title, content)
            )
            count += 1
    return jsonify({"success": True, "imported": count})


@app.route("/api/settings", methods=["POST"])
def update_settings():
    data = request.json
    save_settings(data)
    return jsonify({"success": True})


def get_gspread_worksheet(settings):
    import gspread
    from google.oauth2.service_account import Credentials
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly"
    ]
    # クラウド環境：環境変数からJSON認証情報を取得
    env_creds = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if env_creds:
        padded = env_creds + '=' * (-len(env_creds) % 4)
        creds_dict = json.loads(base64.b64decode(padded).decode("utf-8"))
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    else:
        # ローカル環境：ファイルから読み込む
        creds = Credentials.from_service_account_file(settings["creds_file"], scopes=scopes)
    gc = gspread.authorize(creds)
    # 環境変数のIDを優先、なければsettingsから
    spreadsheet_id = os.environ.get("SPREADSHEET_ID") or settings["spreadsheet_id"].strip()
    sh = gc.open_by_key(spreadsheet_id)
    sheet_name = os.environ.get("SHEET_NAME") or settings.get("sheet_name", "").strip()
    if sheet_name:
        worksheet = sh.worksheet(sheet_name)
    else:
        worksheet = sh.get_worksheet(0)
    return sh, worksheet


@app.route("/api/sheets", methods=["GET"])
def get_sheets():
    settings = load_settings()
    env_creds = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    creds_file = settings.get("creds_file")
    spreadsheet_id = os.environ.get("SPREADSHEET_ID") or settings.get("spreadsheet_id", "").strip()
    if not env_creds and (not creds_file or not os.path.exists(creds_file)):
        return jsonify({"error": "認証情報ファイルが設定されていません"}), 400
    if not spreadsheet_id:
        return jsonify({"error": "スプレッドシートIDが設定されていません"}), 400
    try:
        sh, _ = get_gspread_worksheet(settings)
        sheet_titles = [ws.title for ws in sh.worksheets()]
        return jsonify({"sheets": sheet_titles})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/customers", methods=["GET"])
def get_customers():
    settings = load_settings()
    env_creds = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    creds_file = settings.get("creds_file")
    spreadsheet_id = os.environ.get("SPREADSHEET_ID") or settings.get("spreadsheet_id", "").strip()

    if not env_creds and (not creds_file or not os.path.exists(creds_file)):
        return jsonify({"error": "認証情報ファイルが設定されていません"}), 400
    if not spreadsheet_id:
        return jsonify({"error": "スプレッドシートIDが設定されていません"}), 400

    try:
        _, worksheet = get_gspread_worksheet(settings)
        all_values = worksheet.get_all_values()
        if len(all_values) < 2:
            return jsonify({"customers": [], "message": f"「{worksheet.title}」にデータが見つかりませんでした"})

        headers = [h.strip() for h in all_values[0]]
        customers = []
        for i, row in enumerate(all_values[1:]):
            row_dict = {}
            for j, val in enumerate(row):
                if j < len(headers) and headers[j]:
                    row_dict[headers[j]] = val.strip()
            # A列（先頭列）を名前として使う
            name = row[0].strip() if row else ""
            # W列（index=22）をフェーズとして直接取得
            phase_from_w = row[22].strip() if len(row) > 22 else ""
            if name:
                customers.append({
                    "index": i,
                    "name": name,
                    "data": row_dict,
                    "phase": phase_from_w
                })
        return jsonify({"customers": customers})
    except Exception as e:
        return jsonify({"error": f"スプレッドシートの読み込みに失敗しました：{str(e)}"}), 500


@app.route("/api/generate", methods=["POST"])
def generate_reply():
    data = request.json
    customer_message = data.get("message", "").strip()
    api_key = data.get("api_key", "").strip()
    customer_data = data.get("customer_data", None)
    phase_number = data.get("phase", None)
    owner_note = data.get("owner_note", "").strip()

    # 環境変数のAPIキーをフォールバックとして使用
    if not api_key:
        api_key = os.environ.get("CLAUDE_API_KEY", "")
    if not customer_message:
        return jsonify({"error": "お客さんのメッセージを入力してください"}), 400
    if not api_key:
        return jsonify({"error": "Claude APIキーを入力してください"}), 400

    knowledge_list = get_all_knowledge()
    knowledge_text = ""
    if knowledge_list:
        knowledge_text = "\n\n".join(
            f"【{k['title']}】\n{k['content']}" for k in knowledge_list
        )

    system_prompt = """あなたは退職支援サービスのサポートスタッフです。
お客さんからのLINEメッセージに対して、丁寧で温かみのある返信文を作成してください。

以下のルールを守ってください：
- 必ず「ご連絡ありがとうございます」「ご質問ありがとうございます」「ご返信ありがとうございます」のいずれか適切な一文から始める
- 敬語・丁寧語を使う
- 相手の気持ちに寄り添う
- 具体的なサポート内容を案内する
- 長すぎず、読みやすい長さにする（LINEメッセージとして自然な長さ）
- 絵文字は控えめに使う"""

    # フェーズ情報を追加
    try:
        phase_num = int(phase_number) if phase_number else None
    except (ValueError, TypeError):
        phase_num = None

    if phase_num and phase_num in PHASES:
        phase_info = PHASES[phase_num]
        system_prompt += f"\n\n【現在のサポートフェーズ】\n{phase_info['guidance']}"

    if customer_data:
        customer_info_lines = []
        for key, val in customer_data.items():
            if val and str(val).strip():
                customer_info_lines.append(f"・{key}：{val}")
        if customer_info_lines:
            system_prompt += "\n\n【対応中のお客様情報】\n" + "\n".join(customer_info_lines)

        # 日付の経過状況をリアルタイムで追加
        date_context = build_date_context(customer_data)
        if date_context:
            system_prompt += f"\n\n【日付・スケジュールの状況（リアルタイム）】\n{date_context}"
            system_prompt += "\n\n経過済みの日付がある場合はその事実を踏まえ、次のステップを案内してください。直近の日付がある場合はその準備を促してください。"

        system_prompt += "\n\nお客様情報・フェーズ・日付状況を踏まえて、その方の状況に合った返信をしてください。"

    if owner_note:
        system_prompt += f"\n\n【オーナーからの補足情報】\n{owner_note}\n\nこの補足情報を特に重視して、返信の内容やトーンに反映させてください。"

    if knowledge_text:
        system_prompt += f"\n\n【サポートデータ・参考情報】\n{knowledge_text}"

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=[
                {"role": "user", "content": f"お客さんからのメッセージ：\n{customer_message}"}
            ]
        )
        reply = response.content[0].text
        return jsonify({"reply": reply})
    except anthropic.AuthenticationError:
        return jsonify({"error": "APIキーが正しくありません。確認してください。"}), 401
    except Exception as e:
        return jsonify({"error": f"エラーが発生しました：{str(e)}"}), 500


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print("アプリを起動しています...")
    print(f"ブラウザで http://localhost:{port} を開いてください")
    app.run(debug=False, host="0.0.0.0", port=port)
