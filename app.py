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
st.markdown("将 Gemini 提取的考研英语真题生词，一键追加至墨墨背单词。")

# ================= 侧边栏配置区 =================
st.sidebar.header("⚙️ 基础配置")
maimemo_token = st.sidebar.text_input("墨墨 API Token", type="password", help="前往墨墨 App：我的 -> 更多设置 -> 实验功能 -> API 开放 获取")
notepad_title = st.sidebar.text_input("目标云词本名称", value="27考研真题生词本")

st.sidebar.markdown("---")
st.sidebar.markdown("💡 **使用提示**\n1. 请确保墨墨中已存在该同名词本\n2. 粘贴 JSON 数据后点击同步")

# ================= 全局 Session 优化 =================
@st.cache_resource
def get_http_session():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))
    return session

BASE_URL = "https://open.maimemo.com/open"

def get_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

# ================= 核心操作函数 =================
def append_to_single_notepad(new_spellings, token, title, session):
    """【纯追加模式】只寻找现有词本，绝不新建"""
    headers = get_headers(token)
    
    notepads = []
    limit = 10 
    offset = 0
    
    # 1. 遍历寻找词本
    while True:
        res = session.get(f"{BASE_URL}/api/v1/notepads", headers=headers, params={"limit": limit, "offset": offset})
        if not res.ok:  
            raise Exception(f"拉取词本失败 (HTTP {res.status_code}): {res.text}")
            
        current_batch = res.json().get("notepads", [])
        notepads.extend(current_batch)
        
        if len(current_batch) < limit:
            break
            
        offset += limit
        time.sleep(0.3)
    
    # 2. 严格核对名称
    target_notepad = None
    for pad in notepads:
        if pad["title"] == title:
            target_notepad = pad
            break
            
    if not target_notepad:
        raise Exception(f"🚨 严重拦截：你的账号中未找到名为【{title}】的云词本！为了防止重复创建，程序已终止。请先在墨墨 App 中手动建好该词本。")
        
    target_id = target_notepad["id"]
            
    # 3. 拉取该词本的现有单词与属性配置
    res = session.get(f"{BASE_URL}/api/v1/notepads/{target_id}", headers=headers)
    if not res.ok:
        raise Exception(f"读取该词本内容失败 (HTTP {res.status_code}): {res.text}")
        
    pad_data = res.json().get("notepad", {})
    content = pad_data.get("content", "")
    existing_words = [w.strip() for w in content.split('\n') if w.strip()]
            
    # 4. 追加与去重
    added_count = 0
    clean_new_spellings = [w.strip() for w in new_spellings]
    for w in clean_new_spellings:
        if w not in existing_words:
            existing_words.append(w)
            added_count += 1
            
    new_content = "\n".join(existing_words)
    
    # 5. 按照 API 要求，回填所有必填原属性，仅更新 content
    payload = {
        "notepad": {
            "title": pad_data.get("title", title),
            "content": new_content,
            "brief": pad_data.get("brief", "真题生词"),
            "tags": pad_data.get("tags", ["考研"]),
            "status": "PUBLISHED"
        }
    }
    
    res_post = session.post(f"{BASE_URL}/api/v1/notepads/{target_id}", headers=headers, json=payload)
    if not res_post.ok: 
         raise Exception(f"更新已有词本失败 (HTTP {res_post.status_code}): {res_post.text}")
         
    return f"锁定目标词本，成功追加 {added_count} 个新词（拦截了 {len(new_spellings) - added_count} 个重复项）"

def query_vocabulary_ids(spellings, token, session):
    """【深度诊断版】放弃不稳定的批量接口，逐个查询并暴露出具体死因"""
    headers = get_headers(token)
    spelling_to_id = {}
    clean_spellings = [w.strip() for w in spellings]
    errors = []

    for w in clean_spellings:
        w_lower = w.lower()
        try:
            res = session.get(f"{BASE_URL}/api/v1/vocabulary", headers=headers, params={"spelling": w_lower})
            if res.ok:
                voc_data = res.json().get("voc", {})
                if voc_data and "id" in voc_data:
                    spelling_to_id[w_lower] = voc_data["id"]
                else:
                    errors.append(f"【{w}】: 接口通了，但墨墨返回的数据是空的 -> {res.text}")
            else:
                errors.append(f"【{w}】: 查询遭拒 (HTTP {res.status_code}) -> {res.text}")
            time.sleep(0.3)
        except Exception as e:
            errors.append(f"【{w}】: 程序崩溃 -> {str(e)}")
                
    return spelling_to_id, errors

def create_interpretation(voc_id, interpretation_text, token, session):
    headers = get_headers(token)
    # 统一使用官方文档示例的合法 tag，避免被后端校验拦截
    payload = {"interpretation": {"voc_id": voc_id, "interpretation": interpretation_text, "tags": ["考研"], "status": "PUBLISHED"}}
    res = session.post(f"{BASE_URL}/api/v1/interpretations", headers=headers, json=payload)
    return res.ok, res.text

def create_note(voc_id, note_text, token, session):
    headers = get_headers(token)
    # 使用基础文本，避免触发特殊字符拦截
    payload = {"note": {"voc_id": voc_id, "note_type": "助记", "note": note_text}}
    res = session.post(f"{BASE_URL}/api/v1/notes", headers=headers, json=payload)
    return res.ok, res.text

# ================= 主界面与执行逻辑 =================
http_session = get_http_session()

raw_text = st.text_area("在此粘贴 Gemini 生成的 JSON 数据：", height=250, placeholder="{\n  \"words\": [\n    ...\n  ]\n}")

if st.button("🚀 一键开始同步", use_container_width=True):
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
                
                with st.spinner('正在与墨墨背单词通信中...'):
                    try:
                        # 1. 严格追加词本
                        status_msg = append_to_single_notepad(spellings, maimemo_token, notepad_title, http_session)
                        st.info(f"📁 词本操作: {status_msg}")
                        
                        # 2. 诊断式查询 ID
                        spelling_to_id, query_errors = query_vocabulary_ids(spellings, maimemo_token, http_session)
                        
                        if query_errors:
                            st.warning("⚠️ 部分单词查询触礁！这是墨墨服务器返回的真实错误信息：")
                            for err in query_errors:
                                st.code(err)
                        
                        # 3. 同步释义与助记，追踪每一笔报错
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        
                        sync_details = []
                        total_words = len(words_data)
                        
                        for idx, w in enumerate(words_data):
                            spelling = w["spelling"].strip().lower()
                            voc_id = spelling_to_id.get(spelling)
                            status_text.text(f"正在写入释义和助记: {spelling} ({idx+1}/{total_words})")
                            
                            if voc_id:
                                # 写入释义
                                ok_interp, err_interp = create_interpretation(voc_id, w["interpretation"], maimemo_token, http_session)
                                time.sleep(0.3)
                                
                                # 写入助记
                                ok_note, err_note = create_note(voc_id, w["note"], maimemo_token, http_session)
                                time.sleep(0.3)
                                
                                if ok_interp and ok_note:
                                    sync_details.append(f"🟢 **{spelling}**: 释义与助记写入成功")
                                else:
                                    err_msg = ""
                                    if not ok_interp: err_msg += f"释义遭拒({err_interp}) "
                                    if not ok_note: err_msg += f"助记遭拒({err_note})"
                                    sync_details.append(f"🔴 **{spelling}**: {err_msg}")
                            else:
                                sync_details.append(f"⚪ **{spelling}**: 前置 ID 查询失败，跳过写入流程")
                                
                            progress_bar.progress((idx + 1) / total_words)
                        
                        status_text.empty()
                        
                        with st.expander("点击查看全部单词同步明细"):
                            for detail in sync_details:
                                st.markdown(detail)
                                
                        st.balloons()
                        st.success(f"🎉 同步流程执行完毕！")
                        
                    except Exception as e:
                        st.error(f"🌐 严重错误被拦截: {str(e)}")
                        
        except json.JSONDecodeError:
            st.error("❌ JSON 解析失败！请确保粘贴的代码结构完整。")
