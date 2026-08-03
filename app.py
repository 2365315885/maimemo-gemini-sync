import streamlit as st
import json
import requests
import re
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 设置页面基础信息
st.set_page_config(page_title="墨墨生词同步助手", page_icon="📖", layout="centered")

st.title("📖 墨墨背单词 & Gemini 同步助手")
st.markdown("将 Gemini 提取的考研英语真题生词，一键精准追加至专属云词本。")

# ================= 全局 Session 优化 =================
@st.cache_resource
def get_http_session():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))
    return session

BASE_URL = "https://open.maimemo.com/open"
http_session = get_http_session()

def get_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

def extract_safe(res_json, key):
    """安全解析补丁：兼容墨墨实际返回格式中隐形的 'data' 包装层"""
    if "data" in res_json and isinstance(res_json["data"], dict):
        return res_json["data"].get(key, {})
    return res_json.get(key, {})

# ================= 侧边栏配置区 =================
st.sidebar.header("⚙️ 基础配置")
maimemo_token = st.sidebar.text_input("墨墨 API Token", type="password", help="前往墨墨 App 获取")

st.sidebar.markdown("---")
st.sidebar.markdown("👇 **告别查不到的烦恼，直接绑定底层 ID**")
notepad_id = st.sidebar.text_input("目标云词本 ID (必填)", help="输入你的专属 API 词本 ID")

# ================= 核心操作函数 =================
def fetch_and_append_notepad(new_spellings, token, pad_id, session):
    headers = get_headers(token)
    
    res = session.get(f"{BASE_URL}/api/v1/notepads/{pad_id}", headers=headers)
    if not res.ok:
        raise Exception(f"读取词本失败 (HTTP {res.status_code})：请确认 ID 是否正确。服务器返回：{res.text}")
        
    # 应用解析补丁
    pad_data = extract_safe(res.json(), "notepad")
    content = pad_data.get("content", "")
    existing_words = [w.strip() for w in content.split('\n') if w.strip()]
            
    added_count = 0
    clean_new_spellings = [w.strip() for w in new_spellings]
    for w in clean_new_spellings:
        if w not in existing_words:
            existing_words.append(w)
            added_count += 1
            
    new_content = "\n".join(existing_words)
    
    payload = {
        "notepad": {
            "title": pad_data.get("title", "27考研真题生词本"),
            "content": new_content,
            "brief": pad_data.get("brief", "AI 精翻自动追加合并的考研真题生词"),
            "tags": pad_data.get("tags", ["考研"]),
            "status": "PUBLISHED"
        },
        "id": pad_id
    }
    
    res_post = session.post(f"{BASE_URL}/api/v1/notepads/{pad_id}", headers=headers, json=payload)
    if not res_post.ok: 
         raise Exception(f"更新词本失败 (HTTP {res_post.status_code}): {res_post.text}")
         
    return f"成功向专属词本追加了 {added_count} 个新词（已自动过滤 {len(new_spellings) - added_count} 个重复项）"

def query_vocabulary_ids(spellings, token, session):
    headers = get_headers(token)
    spelling_to_id = {}
    clean_spellings = [w.strip() for w in spellings]
    errors = []

    for w in clean_spellings:
        w_lower = w.lower()
        try:
            res = session.get(f"{BASE_URL}/api/v1/vocabulary", headers=headers, params={"spelling": w_lower})
            if res.ok:
                # 应用解析补丁
                voc_data = extract_safe(res.json(), "voc")
                if voc_data and "id" in voc_data:
                    spelling_to_id[w_lower] = voc_data["id"]
                else:
                    errors.append(f"【{w}】: 词库中无此词或解析为空 -> {res.text}")
            else:
                errors.append(f"【{w}】: 查询报错 (HTTP {res.status_code}) -> {res.text}")
            time.sleep(1.0) 
        except Exception as e:
            errors.append(f"【{w}】: 程序崩溃 -> {str(e)}")
                
    return spelling_to_id, errors

def create_interpretation(voc_id, interpretation_text, token, session):
    headers = get_headers(token)
    payload = {"interpretation": {"voc_id": voc_id, "interpretation": interpretation_text, "tags": ["考研"], "status": "PUBLISHED"}}
    res = session.post(f"{BASE_URL}/api/v1/interpretations", headers=headers, json=payload)
    return res.ok, res.text

def create_note(voc_id, note_text, token, session):
    headers = get_headers(token)
    payload = {"note": {"voc_id": voc_id, "note_type": "助记", "note": note_text}}
    res = session.post(f"{BASE_URL}/api/v1/notes", headers=headers, json=payload)
    return res.ok, res.text

# ================= 主界面与执行逻辑 =================
if not notepad_id:
    st.info("👋 **首次使用配置指南**\n\n为了保证单词能够 100% 精准追加，程序需要绑定一个专属的云词本 ID。")
    if st.button("✨ 一键自动生成专属词本并获取 ID", type="primary"):
        if not maimemo_token:
            st.error("⚠️ 请先在左侧输入你的墨墨 API Token。")
        else:
            with st.spinner('正在通过 API 注册专属词本...'):
                headers = get_headers(maimemo_token)
                payload = {
                    "notepad": {
                        "title": "27考研真题生词本",
                        "content": "test_word_placeholder", 
                        "brief": "AI 精翻真题生词专属仓库",
                        "tags": ["考研"],
                        "status": "PUBLISHED"
                    }
                }
                res = http_session.post(f"{BASE_URL}/api/v1/notepads", headers=headers, json=payload)
                if res.ok:
                    # 修复解析，精准读取 ID
                    new_id = extract_safe(res.json(), "notepad").get("id", "")
                    if new_id:
                        st.success(f"🎉 专属词本创建成功！\n\n请**复制**下方这串 ID，并**粘贴**到左侧边栏的【目标云词本 ID】输入框中。以后每次打开网页，只需输入这个 ID 即可完美追加！")
                        st.code(new_id)
                    else:
                        st.error(f"解析 ID 失败，返回原始数据为: {res.text}")
                else:
                    st.error(f"创建失败: {res.text}")
else:
    raw_text = st.text_area("在此粘贴 Gemini 生成的 JSON 数据：", height=250, placeholder="{\n  \"words\": [\n    ...\n  ]\n}")

    if st.button("🚀 一键开始精准同步", use_container_width=True):
        if not maimemo_token:
            st.error("⚠️ 请先在左侧输入你的墨墨 API Token。")
        elif not raw_text.strip():
            st.warning("⚠️ 没有检测到输入内容，请先粘贴 JSON 数据。")
        else:
            cleaned_text = re.sub(r'^```json\s*', '', raw_text.strip(), flags=re.MULTILINE)
            cleaned_text = re.sub(r'^```\s*$', '', cleaned_text, flags=re.MULTILINE)
            
            try:
                data = json.loads(cleaned_text)
                words_data = data.get("words", [])
                
                if not words_data:
                    st.warning("❌ 未在 JSON 中找到 'words' 数据，请确认格式是否正确。")
                else:
                    spellings = [w["spelling"] for w in words_data]
                    st.success(f"✅ 成功提取 {len(spellings)} 个生词：{', '.join(spellings)}")
                    
                    with st.spinner('正在与墨墨背单词通信中，为避免防爬虫封锁，写入速度已智能放缓 (约1-2秒/词)...'):
                        try:
                            # 1. 精准追加词本 (O(1)复杂度)
                            status_msg = fetch_and_append_notepad(spellings, maimemo_token, notepad_id, http_session)
                            st.info(f"📁 词本操作: {status_msg}")
                            
                            # 2. 诊断式查询 ID
                            spelling_to_id, query_errors = query_vocabulary_ids(spellings, maimemo_token, http_session)
                            
                            if query_errors:
                                st.warning("⚠️ 以下单词在底层词库匹配中遇到异常，详细原因如下：")
                                for err in query_errors:
                                    st.code(err)
                            
                            # 3. 同步释义与助记
                            progress_bar = st.progress(0)
                            status_text = st.empty()
                            
                            sync_details = []
                            total_words = len(words_data)
                            
                            for idx, w in enumerate(words_data):
                                spelling = w["spelling"].strip().lower()
                                voc_id = spelling_to_id.get(spelling)
                                status_text.text(f"正在写入释义和助记: {spelling} ({idx+1}/{total_words})")
                                
                                if voc_id:
                                    ok_interp, err_interp = create_interpretation(voc_id, w["interpretation"], maimemo_token, http_session)
                                    time.sleep(1.0) 
                                    
                                    ok_note, err_note = create_note(voc_id, w["note"], maimemo_token, http_session)
                                    time.sleep(1.0) 
                                    
                                    if ok_interp and ok_note:
                                        sync_details.append(f"🟢 **{spelling}**: 释义与助记写入成功")
                                    else:
                                        err_msg = ""
                                        if not ok_interp: err_msg += f"释义遭拒({err_interp}) "
                                        if not ok_note: err_msg += f"助记遭拒({err_note})"
                                        sync_details.append(f"🔴 **{spelling}**: {err_msg}")
                                else:
                                    sync_details.append(f"⚪ **{spelling}**: 词库中未定位到该词，已跳过释义和助记的写入")
                                    
                                progress_bar.progress((idx + 1) / total_words)
                            
                            status_text.empty()
                            
                            with st.expander("点击查看全部单词同步明细"):
                                for detail in sync_details:
                                    st.markdown(detail)
                                    
                            st.balloons()
                            st.success(f"🎉 同步流程执行完毕！")
                            
                        except Exception as e:
                            st.error(f"🌐 系统错误: {str(e)}")
                            
            except json.JSONDecodeError:
                st.error("❌ JSON 解析失败！请确保粘贴的代码结构完整。")
