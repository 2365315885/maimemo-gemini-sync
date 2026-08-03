import streamlit as st
import json
import requests
import re

# 设置页面基础信息
st.set_page_config(page_title="墨墨生词同步助手", page_icon="📖", layout="centered")

st.title("📖 墨墨背单词 & Gemini 同步助手")
st.markdown("将 Gemini 提取的考研英语真题生词，一键追加至墨墨背单词。")

# ================= 侧边栏配置区 =================
st.sidebar.header("⚙️ 基础配置")
# 使用 password 类型输入，保护你的 Token 在录屏或公共场合不泄露
maimemo_token = st.sidebar.text_input("墨墨 API Token", type="password", help="前往墨墨 App：我的 -> 更多设置 -> 实验功能 -> API 开放 获取")
notepad_title = st.sidebar.text_input("目标云词本名称", value="27考研真题生词本")

st.sidebar.markdown("---")
st.sidebar.markdown("💡 **使用提示**\n1. 在 Gemini 获取 JSON 数据\n2. 粘贴至右侧输入框\n3. 点击开始同步即可")

# ================= 核心操作函数 =================
BASE_URL = "https://open.maimemo.com/open"

def get_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

def append_to_single_notepad(new_spellings, token, title):
    """查找指定云词本，去重追加"""
    headers = get_headers(token)
    
    res = requests.get(f"{BASE_URL}/api/v1/notepads", headers=headers)
    res.raise_for_status()
    notepads = res.json().get("notepads", [])
    
    target_id = None
    for pad in notepads:
        if pad["title"] == title:
            target_id = pad["id"]
            break
            
    existing_words = []
    if target_id:
        res = requests.get(f"{BASE_URL}/api/v1/notepads/{target_id}", headers=headers)
        if res.status_code == 200:
            content = res.json().get("notepad", {}).get("content", "")
            existing_words = [w.strip() for w in content.split('\n') if w.strip()]
            
    added_count = 0
    for w in new_spellings:
        if w not in existing_words:
            existing_words.append(w)
            added_count += 1
            
    new_content = "\n".join(existing_words)
    payload = {
        "notepad": {
            "title": title,
            "brief": "AI 精翻自动追加合并的考研真题生词",
            "content": new_content,
            "tags": ["考研", "Gemini精翻"],
            "status": "PUBLISHED"
        }
    }
    
    if target_id:
        payload["id"] = target_id
        requests.post(f"{BASE_URL}/api/v1/notepads/{target_id}", headers=headers, json=payload).raise_for_status()
        return f"找到已有词本，成功追加 {added_count} 个新词（过滤了 {len(new_spellings) - added_count} 个重复项）"
    else:
        requests.post(f"{BASE_URL}/api/v1/notepads", headers=headers, json=payload).raise_for_status()
        return f"创建了全新词本，并写入了 {len(new_spellings)} 个词"

def query_vocabulary_ids(spellings, token):
    """获取生词库 ID"""
    headers = get_headers(token)
    res = requests.post(f"{BASE_URL}/api/v1/vocabulary/query", headers=headers, json={"spellings": spellings})
    res.raise_for_status()
    voc_list = res.json().get("voc", [])
    return {item["spelling"].lower(): item["id"] for item in voc_list}

def create_interpretation(voc_id, interpretation_text, token):
    """写入 AI 专属释义"""
    headers = get_headers(token)
    payload = {"interpretation": {"voc_id": voc_id, "interpretation": interpretation_text, "tags": ["AI释义"], "status": "PUBLISHED"}}
    requests.post(f"{BASE_URL}/api/v1/interpretations", headers=headers, json=payload)

def create_note(voc_id, note_text, token):
    """写入 AI 助记"""
    headers = get_headers(token)
    payload = {"note": {"voc_id": voc_id, "note_type": "AI助记", "note": note_text}}
    requests.post(f"{BASE_URL}/api/v1/notes", headers=headers, json=payload)

# ================= 主界面与执行逻辑 =================
raw_text = st.text_area("在此粘贴 Gemini 生成的 JSON 数据：", height=250, placeholder="{\n  \"words\": [\n    ...\n  ]\n}")

if st.button("🚀 一键开始同步", use_container_width=True):
    if not maimemo_token:
        st.error("⚠️ 请先在左侧输入你的墨墨 API Token。")
    elif not raw_text.strip():
        st.warning("⚠️ 没有检测到输入内容，请先粘贴 JSON 数据。")
    else:
        # 清除可能带有的 Markdown 标记
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
                        # 1. 同步云词本
                        status_msg = append_to_single_notepad(spellings, maimemo_token, notepad_title)
                        st.info(f"📁 词本操作: {status_msg}")
                        
                        # 2. 查询 ID
                        spelling_to_id = query_vocabulary_ids(spellings, maimemo_token)
                        
                        # 3. 同步释义与助记
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        
                        sync_details = []
                        total_words = len(words_data)
                        
                        for idx, w in enumerate(words_data):
                            spelling = w["spelling"].lower()
                            voc_id = spelling_to_id.get(spelling)
                            status_text.text(f"正在同步: {spelling} ({idx+1}/{total_words})")
                            
                            if voc_id:
                                create_interpretation(voc_id, w["interpretation"], maimemo_token)
                                create_note(voc_id, w["note"], maimemo_token)
                                sync_details.append(f"🟢 **{spelling}**: 专属释义与助记同步成功")
                            else:
                                sync_details.append(f"🔴 **{spelling}**: 词库匹配失败，仅加入云词本")
                                
                            progress_bar.progress((idx + 1) / total_words)
                        
                        status_text.empty()
                        
                        # 展示同步细节
                        with st.expander("点击查看全部单词同步明细"):
                            for detail in sync_details:
                                st.markdown(detail)
                                
                        st.balloons()
                        st.success(f"🎉 任务圆满完成！快去墨墨 App 里刷这些真题词汇吧！")
                        
                    except Exception as e:
                        st.error(f"🌐 网络请求异常: {str(e)}")
                        
        except json.JSONDecodeError:
            st.error("❌ JSON 解析失败！请确保粘贴的代码结构完整，不包含多余的中文描述。")