# 确保这两行在所有其他 import 之前
import eventlet
eventlet.monkey_patch()

import os # <--- 确保导入 os 模块
import requests
import time
import hashlib
from functools import reduce
import json
import re
from datetime import date, datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_socketio import SocketIO
import logging

# 禁用Flask和SocketIO的冗余日志
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# ===================================================================
# MODIFIED: Use absolute paths to locate template and static folders
# ===================================================================
# 获取 app.py 文件所在的目录的绝对路径 (e.g., .../BiliDownloader-Electron/backend)
backend_dir = os.path.dirname(os.path.abspath(__file__))
# 构建到 frontend 文件夹的绝对路径 (e.g., .../BiliDownloader-Electron/frontend)
frontend_dir = os.path.join(backend_dir, '..', 'frontend')

# 使用绝对路径初始化 Flask
app = Flask(__name__, template_folder=frontend_dir, static_folder=frontend_dir)
# ===================================================================


app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, async_mode='eventlet')

# --- Bilibili API Endpoints ---
# ... (rest of the file is unchanged)
# ... (从这里开始到文件末尾的所有其他代码都和上一版完全一样)
VIDEO_INFO_API = "https://api.bilibili.com/x/web-interface/view"
PLAYURL_API = "https://api.bilibili.com/x/player/wbi/playurl"
DANMAKU_API = "https://api.bilibili.com/x/v1/dm/list.so"
NAV_API = "https://api.bilibili.com/x/web-interface/nav"
FAV_LIST_API = "https://api.bilibili.com/x/v3/fav/resource/list"
SEARCH_API = "https://api.bilibili.com/x/web-interface/search/type"
FAV_DEAL_API = "https://api.bilibili.com/x/v3/fav/resource/deal"

# --- 全局配置 ---
SESSDATA = ""
wbi_keys_cache = {'key': None, 'expires_at': 0}

# --- WBI Signing Implementation ---
mixinKeyEncTab = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52
]
def getMixinKey(orig: str): return reduce(lambda s, i: s + orig[i], mixinKeyEncTab, '')[:32]

def get_wbi_keys(force_refresh=False):
    global wbi_keys_cache
    now = time.time()
    if not force_refresh and wbi_keys_cache['key'] and now < wbi_keys_cache['expires_at']:
        return wbi_keys_cache['key'], "从缓存获取WBI密钥成功。"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        resp = requests.get(NAV_API, headers=headers)
        resp.raise_for_status()
        json_content = resp.json()
        img_url = json_content['data']['wbi_img']['img_url']
        sub_url = json_content['data']['wbi_img']['sub_url']
        img_key = img_url.rsplit('/', 1)[1].split('.')[0]
        sub_key = sub_url.rsplit('/', 1)[1].split('.')[0]
        wbi_key = getMixinKey(img_key + sub_key)
        wbi_keys_cache['key'] = wbi_key
        wbi_keys_cache['expires_at'] = now + 1800
        return wbi_key, "成功获取并刷新WBI密钥。"
    except Exception as e:
        return None, f"错误：获取WBI密钥失败: {e}"

def sign_params(params: dict, wbi_key: str):
    params['wts'] = str(int(time.time()))
    query = '&'.join([f'{key}={value}' for key, value in sorted(params.items())])
    params['w_rid'] = hashlib.md5((query + wbi_key).encode()).hexdigest()
    return params

def sanitize_filename(filename, fallback_name="default_name"):
    """
    一个更智能、更宽容的文件名清理函数。
    1. 只移除在文件系统中明确非法的字符。
    2. 移除 Emoji，但保留其他合法的 Unicode 字符 (例如中文、日文等)。
    3. 仅在清理后文件名变为空时，才使用备用名称。
    """
    # 1. 移除文件系统明确禁止的字符
    sanitized = re.sub(r'[\\/*?:"<>|]', "", filename)
    
    # 2. 移除 Emoji 字符
    # 这个正则表达式覆盖了大部分常见的 Emoji 字符范围
    emoji_pattern = re.compile(
        "["
        u"\U0001F600-\U0001F64F"  # emoticons
        u"\U0001F300-\U0001F5FF"  # symbols & pictographs
        u"\U0001F680-\U0001F6FF"  # transport & map symbols
        u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
        u"\U00002702-\U000027B0"
        u"\U000024C2-\U0001F251"
        "]+",
        flags=re.UNICODE,
    )
    sanitized = emoji_pattern.sub(r'', sanitized)
    
    # 3. 移除首尾可能存在的空白字符
    sanitized = sanitized.strip()
    
    # 4. 如果清理后字符串变为空 (例如原标题就是 ":*?🎉"), 则使用备用名
    if not sanitized:
        return fallback_name
    
    return sanitized

def clean_video_data(raw_data):
    keys_to_keep = ['bvid', 'aid', 'videos', 'pic', 'title', 'pubdate', 'ctime', 'desc', 'duration', 'owner', 'stat', 'pages', 'subtitle', 'isHachimi']
    return {key: raw_data[key] for key in keys_to_keep if key in raw_data}

def log_message(message, is_error=False):
    print(message)
    socketio.emit('log_message', {'data': message, 'is_error': is_error})

def get_today_videos_from_search(session, keyword, log_func):
    video_bvids = []
    page_num = 1
    today_date = date.today()
    stop_fetching = False
    log_func(f"开始搜索关键词 '{keyword}' 的今日视频，目标日期: {today_date}")
    while not stop_fetching:
        params = {"search_type": "video", "keyword": keyword, "order": "pubdate", "page": page_num}
        try:
            response = session.get(SEARCH_API, params=params)
            response.raise_for_status(); data = response.json()
            if data["code"] != 0: break
            results = data.get("data", {}).get("result", [])
            if not results: break
            log_func(f"正在扫描第 {page_num} 页的搜索结果...")
            for video in results:
                if video.get("type") != "video": continue
                video_date = datetime.fromtimestamp(video.get("pubdate")).date()
                if video_date == today_date: video_bvids.append(video["bvid"])
                elif video_date < today_date:
                    stop_fetching = True; log_func("已扫描到昨日视频，停止获取。"); break
            page_num += 1; time.sleep(1)
        except Exception as e:
            log_func(f"请求错误 (搜索视频): {e}", True); break
    return video_bvids

def get_videos_from_favlist(session, fid, log_func):
    video_bvids = []
    page_num = 1
    while True:
        params = {"media_id": fid, "pn": page_num, "ps": 20}
        try:
            response = session.get(FAV_LIST_API, params=params)
            response.raise_for_status(); data = response.json()
            if data["code"] != 0: log_func(f"API错误: {data['message']}", True); break
            medias = data.get("data", {}).get("medias", [])
            if not medias: break
            for video in medias: video_bvids.append(video["bvid"])
            log_func(f"已获取收藏夹第 {page_num} 页...")
            if len(medias) < 20: break
            page_num += 1; time.sleep(1)
        except Exception as e:
            log_func(f"请求错误 (收藏夹): {e}", True); break
    return video_bvids

def process_single_video(video_id, wbi_key, session, config):
    log_func = config['log_func']
    try:
        video_id_str = str(video_id).strip()
        input_aid, input_bvid = (int(video_id_str), None) if video_id_str.isdigit() else (None, video_id_str)
        video_data = get_video_info(session, aid=input_aid, bvid=input_bvid)
        if not video_data:
            log_func(f"获取视频 {video_id} 信息失败，已跳过。", True)
            return
        title = video_data.get("title", "未知标题")
        bvid = video_data.get("bvid", "未知BVID")
        sanitized_title = sanitize_filename(title)
        video_folder_path = os.path.join(config['download_path'], sanitized_title)
        log_func(f"正在处理视频: {title} (BVID: {bvid})")
        os.makedirs(video_folder_path, exist_ok=True)
        video_data["isHachimi"] = False
        pool = eventlet.GreenPool()
        if "pages" in video_data:
            for i, part in enumerate(video_data["pages"]):
                pool.spawn(process_video_part, part, i + 1, bvid, wbi_key, session, video_folder_path, config)
        pool.waitall()
        final_video_data = clean_video_data(video_data)
        json_path = os.path.join(video_folder_path, "video_info.json")
        if not os.path.exists(json_path):
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(final_video_data, f, ensure_ascii=False, indent=4)
        if config['download_cover'] and "pic" in final_video_data:
            download_file(session, final_video_data["pic"], os.path.join(video_folder_path, "cover.jpg"), "封面图", log_func)
        log_func(f"视频 '{title}' 处理完成。")
    except Exception as e:
        log_func(f"处理视频 {video_id} 时发生未知严重错误: {e}", True)

def process_video_part(part, part_num, bvid, wbi_key, session, video_folder_path, config):
    log_func = config['log_func']
    part_cid = part["cid"]
    log_func(f"  正在处理分P {part_num} (CID: {part_cid})...")
    video_url, audio_url = get_media_urls(session, bvid, part_cid, wbi_key)
    part["video_url"] = video_url or ""
    part["audio_url"] = audio_url or ""
    part_pool = eventlet.GreenPool()
    part_title_sanitized = sanitize_filename(part.get("part", f"P{part_num}"))
    if video_url and config['download_video']:
        part_pool.spawn(download_file, session, video_url, os.path.join(video_folder_path, f"{part_title_sanitized}_video.m4s"), f"视频(P{part_num})", log_func)
    if audio_url and config['download_audio']:
        part_pool.spawn(download_file, session, audio_url, os.path.join(video_folder_path, f"{part_title_sanitized}_audio.m4s"), f"音频(P{part_num})", log_func)
    if config['download_danmaku']:
        part_pool.spawn(download_danmaku, session, part_cid, os.path.join(video_folder_path, f"{part_title_sanitized}_danmaku.xml"), log_func)
    part_pool.waitall()

def get_video_info(session, aid=None, bvid=None):
    params = {"aid": aid} if aid else {"bvid": bvid}
    try:
        r = session.get(VIDEO_INFO_API, params=params); r.raise_for_status()
        data = r.json()
        return data.get("data") if data.get("code") == 0 else None
    except: return None

def get_media_urls(session, bvid, cid, wbi_key):
    params = {"bvid": bvid, "cid": cid, "fnval": 4048, "fourk": 1}
    try:
        signed_params = sign_params(params, wbi_key)
        r = session.get(PLAYURL_API, params=signed_params); r.raise_for_status()
        data = r.json()
        if data["code"] == 0 and "dash" in data["data"]:
            return data["data"]["dash"]["video"][0]["baseUrl"], data["data"]["dash"]["audio"][0]["baseUrl"]
    except: pass
    return None, None

def download_file(session, url, filepath, display_name, log_func):
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        log_func(f"    文件已存在，跳过下载 {display_name}。")
        return
    try:
        log_func(f"    正在下载 {display_name}...")
        r = session.get(url, stream=True); r.raise_for_status()
        with open(filepath, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
    except Exception as e:
        log_func(f"    下载失败 {display_name}: {e}", True)

def download_danmaku(session, cid, filepath, log_func):
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        log_func(f"    弹幕文件已存在，跳过下载。")
        return
    try:
        log_func("    正在下载弹幕...")
        r = session.get(f"{DANMAKU_API}?oid={cid}"); r.raise_for_status()
        with open(filepath, "wb") as f: f.write(r.content)
    except Exception as e:
        log_func(f"    弹幕下载失败: {e}", True)

def downloader_task(config):
    global SESSDATA
    SESSDATA = config.get('sessdata', '')
    log_func = config['log_func']
    log_func("开始下载任务...")
    wbi_key, msg = get_wbi_keys()
    log_func(msg)
    if not wbi_key:
        log_func("获取WBI密钥失败，任务中止。", True)
        socketio.emit('download_complete', {'path': config['download_path'], 'error': True})
        return
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com/",
        "Cookie": f"SESSDATA={SESSDATA}"
    })
    video_ids = []
    mode = config['mode']
    if mode == 'single': video_ids.append(config['target_id'])
    elif mode == 'favlist':
        log_func(f"正在从收藏夹 FID: {config['target_id']} 获取视频列表...")
        video_ids = get_videos_from_favlist(session, config['target_id'], log_func)
    elif mode == 'today':
        if not config['keyword']: log_func("错误：'today'模式下必须提供关键词。", True)
        else: video_ids = get_today_videos_from_search(session, config['keyword'], log_func)
    if not video_ids:
        log_func("未能获取到任何视频ID，任务结束。")
    else:
        try:
            pool_size = int(config.get('max_concurrent_tasks', 5))
            if pool_size < 1: pool_size = 1
        except (ValueError, TypeError): pool_size = 5
        total = len(video_ids)
        log_func(f"共找到 {total} 个视频。将使用 {pool_size} 个并发任务进行处理。")
        pool = eventlet.GreenPool(pool_size)
        for i, video_id in enumerate(video_ids):
            pool.spawn(process_single_video, video_id, wbi_key, session, config)
            log_func(f"任务 {i+1}/{total} (ID: {video_id}) 已提交到队列。")
            eventlet.sleep(0.1)
        pool.waitall()
    log_func("\n--- 所有任务已完成！ ---")
    socketio.emit('download_complete', {'path': config['download_path'], 'error': False})

# --- Flask & SocketIO Routes ---
@app.route('/')
def index():
    return render_template('control_panel.html')

@app.route('/reviewer')
def reviewer():
    return render_template('video_reviewer.html')

@socketio.on('start_download')
def handle_start_download(data):
    config = data
    config['log_func'] = log_message
    download_path = config.get('download_path') or 'downloads'
    config['download_path'] = download_path
    os.makedirs(download_path, exist_ok=True)
    socketio.start_background_task(downloader_task, config)

# --- API for Reviewer ---
@app.route('/api/list-folders')
def api_list_folders():
    path = request.args.get('path', 'downloads')
    if not os.path.isdir(path):
        return jsonify({"error": "Directory not found"}), 404
    try:
        folders = [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]
        return jsonify(folders)
    except Exception as e:
        return jsonify({"error": f"Error reading directory: {e}"}), 500

@app.route('/api/get-file')
def api_get_file():
    path = request.args.get('path', 'downloads')
    folder = request.args.get('folder')
    file = request.args.get('file')
    if not folder or not file:
        return jsonify({"error": "Missing folder or file parameter"}), 400
    full_path = os.path.join(path, folder)
    return send_from_directory(full_path, file)

@app.route('/api/update-json', methods=['POST'])
def api_update_json():
    data = request.json
    path = data.get('path', 'downloads')
    folder = data.get('folder')
    new_status = data.get('status')
    if folder is None or new_status is None:
        return jsonify({"error": "Missing parameters"}), 400
    json_path = os.path.join(path, folder, 'video_info.json')
    if not os.path.exists(json_path):
        return jsonify({"error": "JSON file not found"}), 404
    try:
        with open(json_path, 'r+', encoding='utf-8') as f:
            video_info = json.load(f)
            video_info['isHachimi'] = new_status
            f.seek(0); f.truncate()
            json.dump(video_info, f, ensure_ascii=False, indent=4)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/add-to-favorites', methods=['POST'])
def add_to_favorites():
    data = request.json
    aid = data.get('aid')
    fid = data.get('fid')
    sessdata = data.get('sessdata')
    csrf_token = data.get('csrf_token')
    if not all([aid, fid, sessdata, csrf_token]):
        return jsonify({"code": -1, "message": "Missing required parameters."}), 400
    payload = {
        'rid': aid,
        'add_media_ids': fid,
        'type': 2,
        'csrf': csrf_token
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Cookie': f'SESSDATA={sessdata}; bili_jct={csrf_token}',
        'Origin': 'https://www.bilibili.com',
        'Referer': f'https://www.bilibili.com/video/av{aid}'
    }
    try:
        response = requests.post(FAV_DEAL_API, data=payload, headers=headers)
        response.raise_for_status()
        return jsonify(response.json())
    except requests.exceptions.RequestException as e:
        return jsonify({"code": -500, "message": str(e)}), 500

if __name__ == '__main__':
    print("启动Web服务器，请在浏览器中打开 http://127.0.0.1:5000")
    socketio.run(app, host='127.0.0.1', port=5000)