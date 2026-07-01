# -*- coding: utf-8 -*-
"""
书脉 AI 后端服务
- ctext.org API 集成（古籍搜索 + 全文获取）
- DeepSeek V4-Flash API 集成（AI 实体提取 + 深度注释生成）
- 本地 JSON 缓存
"""

import os, json, time, re, hashlib, traceback
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

# ============ 配置 ============
CTEXT_API_BASE = "https://api.ctext.org"
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")  # 从环境变量读取
DEEPSEEK_MODEL = "deepseek-chat"  # DeepSeek V4-Flash 对应的模型名

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# 启动时从 seed 目录恢复缓存（Volume 挂载可能导致 cache 目录为空）
SEED_DIR = os.path.join(os.path.dirname(__file__), "cache_seed")
if os.path.isdir(SEED_DIR):
    seed_files = [f for f in os.listdir(SEED_DIR) if f.endswith('.json')]
    cache_files = [f for f in os.listdir(CACHE_DIR) if f.endswith('.json')] if os.path.isdir(CACHE_DIR) else []
    if len(cache_files) < len(seed_files):
        import shutil
        for f in seed_files:
            src = os.path.join(SEED_DIR, f)
            dst = os.path.join(CACHE_DIR, f)
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
        print(f"[Cache Seed] 已从 seed 目录恢复 {len(seed_files) - len(cache_files)} 个缓存文件")

TEXTS_DIR = os.path.join(os.path.dirname(__file__), "texts")

# 本地古籍文本库索引（书名 -> 文件名）
LOCAL_TEXT_LIBRARY = {}
def _load_local_library():
    """加载本地文本库索引"""
    global LOCAL_TEXT_LIBRARY
    if os.path.exists(TEXTS_DIR):
        for f in os.listdir(TEXTS_DIR):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(TEXTS_DIR, f), "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    title = data.get("title", f.replace(".json", ""))
                    LOCAL_TEXT_LIBRARY[title] = {
                        "file": f,
                        "urn": data.get("urn", ""),
                        "paragraph_count": len(data.get("paragraphs", [])),
                        "source": data.get("source", "local")
                    }
                except:
                    pass
    print(f"[Local Library] 已加载 {len(LOCAL_TEXT_LIBRARY)} 本本地古籍: {list(LOCAL_TEXT_LIBRARY.keys())}")

_load_local_library()

# ============ ctext API 封装 ============

def ctext_search(query):
    """搜索古籍 - 合并本地文本库和 ctext API 结果"""
    results = []

    # 1. 先搜索本地文本库
    for title, info in LOCAL_TEXT_LIBRARY.items():
        if query in title:
            results.append({
                "urn": info["urn"] or f"local:{title}",
                "title": title,
                "type": "古籍",
                "source": "local",
                "can_activate": True,
                "paragraph_count": info["paragraph_count"],
                "desc": f"共 {info['paragraph_count']} 段原文，可立即活化"
            })

    # 2. 再搜索 ctext API（补充本地没有的）
    try:
        resp = requests.get(f"{CTEXT_API_BASE}/searchtexts", params={
            "if": "zh", "remap": "gb", "title": query
        }, timeout=15)
        data = resp.json()
        book_list = data.get("books", data) if isinstance(data, dict) else data
        local_titles = set(LOCAL_TEXT_LIBRARY.keys())
        if isinstance(book_list, list):
            for item in book_list[:20]:
                urn = item.get("urn", "")
                title = item.get("title", "")
                if not urn or title in local_titles:
                    continue
                # ctext 免费可用的古籍（经测试无需认证即可获取全文）
                CTEXT_FREE_URNS = {
                    "ctp:analects", "ctp:mengzi", "ctp:mozi",
                    "ctp:three-character-classic"
                }
                is_free = urn in CTEXT_FREE_URNS
                results.append({
                    "urn": urn,
                    "title": title,
                    "type": "古籍",
                    "source": "ctext",
                    "can_activate": is_free,
                    "desc": "可立即活化" if is_free else "该古籍原文需授权获取，暂不支持活化"
                })
    except Exception as e:
        print(f"[ctext_search] API error: {e}")

    return results[:30]

def ctext_gettext(urn):
    """获取古籍全文（递归获取子章节）"""
    try:
        resp = requests.get(f"{CTEXT_API_BASE}/gettext", params={
            "if": "zh", "remap": "gb", "urn": urn
        }, timeout=15)
        data = resp.json()
        return data
    except Exception as e:
        print(f"[ctext_gettext] error: {e}")
        return None

def ctext_gettextinfo(urn):
    """获取古籍结构信息"""
    try:
        resp = requests.get(f"{CTEXT_API_BASE}/gettextinfo", params={
            "if": "zh", "remap": "gb", "urn": urn
        }, timeout=15)
        data = resp.json()
        return data
    except Exception as e:
        print(f"[ctext_gettextinfo] error: {e}")
        return None

def fetch_full_text(urn):
    """递归获取完整文本，返回段落列表"""
    all_paragraphs = []

    def _fetch(u, depth=0):
        if depth > 3:
            return
        data = ctext_gettext(u)
        if not data:
            return
        if isinstance(data, dict):
            # subsections 可能是字符串列表或字典列表
            if "subsections" in data:
                subs = data["subsections"]
                for sub in subs:
                    if isinstance(sub, str):
                        _fetch(sub, depth + 1)
                    elif isinstance(sub, dict):
                        sub_urn = sub.get("urn", "")
                        if sub_urn:
                            _fetch(sub_urn, depth + 1)
            # 直接包含文本内容
            if "fulltext" in data and data["fulltext"]:
                fulltext = data["fulltext"]
                if isinstance(fulltext, list):
                    all_paragraphs.extend([p.strip() for p in fulltext if p and p.strip()])
                elif isinstance(fulltext, str):
                    paragraphs = fulltext.split("\n\n")
                    all_paragraphs.extend([p.strip() for p in paragraphs if p.strip()])
            if "content" in data and data["content"]:
                content = data["content"]
                if isinstance(content, str):
                    paragraphs = content.split("\n\n")
                    all_paragraphs.extend([p.strip() for p in paragraphs if p.strip()])
                elif isinstance(content, list):
                    all_paragraphs.extend([p.strip() for p in content if p and p.strip()])
            # paragraphs 字段
            if "paragraphs" in data:
                paras = data["paragraphs"]
                if isinstance(paras, list):
                    all_paragraphs.extend([p.strip() for p in paras if p and p.strip()])

    _fetch(urn)
    return all_paragraphs

# ============ DeepSeek API 封装 ============

def deepseek_chat(messages, temperature=0.3, max_tokens=4096):
    """调用 DeepSeek V4-Flash"""
    if not DEEPSEEK_API_KEY:
        return {"error": "DEEPSEEK_API_KEY not set"}

    try:
        resp = requests.post(
            f"{DEEPSEEK_API_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"}
            },
            timeout=120
        )
        data = resp.json()
        if "choices" in data and len(data["choices"]) > 0:
            content = data["choices"][0]["message"]["content"]
            return {"content": content}
        return {"error": f"API error: {data}"}
    except Exception as e:
        return {"error": str(e)}

# ============ AI 管线 ============

def ai_extract_entities(text, book_title=""):
    """第一步：实体提取 + 分类体系"""
    # 截取前 8000 字避免 token 过多
    sample = text[:8000]

    prompt = f"""你是一位古籍研究专家。请分析以下古籍文本，提取其中的关键实体并构建知识图谱分类体系。

古籍名称：{book_title}
文本内容（节选）：
{sample}

请提取以下类型的实体：
1. 人物（神话人物、历史人物、文学人物）
2. 地点/地理（山川、地名、方位）
3. 概念/事件（典故、事件、主题）
4. 物品/技艺（器物、工艺、技术）

请返回 JSON 格式，结构如下：
{{
  "categories": [
    {{"id": "cat1", "label": "分类名称", "description": "分类说明"}}
  ],
  "entities": [
    {{
      "id": "e1",
      "label": "实体名称",
      "categoryId": "cat1",
      "category": "分类名称",
      "brief": "一句话描述该实体"
    }}
  ]
}}

要求：
- 提取 15-25 个最重要的实体
- 分类 3-5 个
- 每个实体必须有 brief 描述
- 实体名称必须是原文中出现的名称"""

    result = deepseek_chat([
        {"role": "system", "content": "你是古籍研究专家，擅长文本分析和知识图谱构建。只返回有效的 JSON。"},
        {"role": "user", "content": prompt}
    ], temperature=0.2, max_tokens=4096)

    if "error" in result:
        return None

    try:
        content = result["content"]
        # 尝试提取 JSON
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        return json.loads(content)
    except Exception as e:
        print(f"[ai_extract_entities] parse error: {e}")
        return None

def ai_generate_detail(entity_label, entity_brief, category, book_title, context_text):
    """第二步：为单个实体生成 7 字段深度注释"""
    prompt = f"""你是一位古籍研究专家。请为以下古籍中的实体生成结构化的深度注释。

古籍名称：{book_title}
实体名称：{entity_label}
实体简介：{entity_brief}
所属分类：{category}
原文上下文：
{context_text[:2000]}

请生成以下 7 个字段的注释，返回 JSON 格式：
{{
  "tags": ["标签1", "标签2", "标签3", "标签4"],
  "sketch": "一句话概括（15-30字）",
  "original": "原文引用（从上下文中摘录最相关的原文段落，30-100字）",
  "translation": "白话翻译（将原文翻译为现代汉语，50-150字）",
  "notes": "学术注释（解释该实体的含义、背景、文化内涵，80-150字）",
  "evolution": "演变历程（该实体/概念在历史上的演变，80-150字）",
  "impact": "影响意义（对后世文化、文学、思想的影响，60-120字）",
  "more": "延伸阅读建议（20-40字）",
  "timeline": [
    ["时期1", "事件描述1"],
    ["时期2", "事件描述2"],
    ["时期3", "事件描述3"],
    ["时期4", "事件描述4"]
  ],
  "related": ["相关实体1", "相关实体2", "相关实体3"]
}}

要求：
- original 字段必须从提供的原文上下文中摘录，不能编造
- translation 必须忠实于原文
- notes 要有学术深度，体现专业知识
- timeline 至少 4 个节点
- related 建议填写同书中的其他实体"""

    result = deepseek_chat([
        {"role": "system", "content": "你是古籍研究专家，擅长撰写学术注释。只返回有效的 JSON。"},
        {"role": "user", "content": prompt}
    ], temperature=0.4, max_tokens=2048)

    if "error" in result:
        return None

    try:
        content = result["content"]
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        return json.loads(content)
    except Exception as e:
        print(f"[ai_generate_detail] parse error for {entity_label}: {e}")
        return None

def ai_build_relations(entities, text):
    """第三步：构建实体间的关系"""
    entity_labels = [e["label"] for e in entities]

    prompt = f"""你是古籍知识图谱专家。请根据以下实体列表和原文，构建实体之间的关系。

实体列表：{json.dumps(entity_labels, ensure_ascii=False)}
原文节选：{text[:4000]}

请分析哪些实体之间存在关系（同一段落共现、语义关联、历史关联等），返回 JSON 格式：
{{
  "relations": [
    ["实体A", "实体B", "关系类型"],
  ]
}}

关系类型包括：从属、关联、对立、演变、同时代等。
请生成 15-30 条关系。"""

    result = deepseek_chat([
        {"role": "system", "content": "你是知识图谱专家。只返回有效的 JSON。"},
        {"role": "user", "content": prompt}
    ], temperature=0.2, max_tokens=2048)

    if "error" in result:
        return []

    try:
        content = result["content"]
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        data = json.loads(content)
        return data.get("relations", [])
    except Exception as e:
        print(f"[ai_build_relations] parse error: {e}")
        return []

def generate_book_data(book_title, urn, full_text_paragraphs):
    """完整 AI 管线：从原文生成完整的书籍数据结构"""
    full_text = "\n\n".join(full_text_paragraphs)

    print(f"[AI Pipeline] 开始处理《{book_title}》，共 {len(full_text_paragraphs)} 段，{len(full_text)} 字")

    # 第一步：实体提取
    print("[AI Pipeline] 步骤1：实体提取...")
    extraction = ai_extract_entities(full_text, book_title)
    if not extraction:
        return {"error": "实体提取失败"}

    entities = extraction.get("entities", [])
    categories = extraction.get("categories", [])
    print(f"[AI Pipeline] 提取到 {len(entities)} 个实体，{len(categories)} 个分类")

    # 第二步：为每个实体生成深度注释
    print("[AI Pipeline] 步骤2：生成深度注释...")
    details = {}
    for i, entity in enumerate(entities):
        label = entity["label"]
        brief = entity.get("brief", "")
        category = entity.get("category", "")
        print(f"  [{i+1}/{len(entities)}] {label}...")

        # 找到该实体在原文中的上下文
        context = ""
        for para in full_text_paragraphs:
            if label in para:
                context = para
                break
        if not context:
            context = full_text[:2000]

        detail = ai_generate_detail(label, brief, category, book_title, context)
        if detail:
            details[label] = detail
        else:
            # 降级：使用 brief 作为 sketch
            details[label] = {
                "tags": [category, "自动生成"],
                "sketch": brief,
                "original": context[:100] if context else "",
                "translation": "",
                "notes": brief,
                "evolution": "",
                "impact": "",
                "more": "",
                "timeline": [],
                "related": []
            }

    # 第三步：构建关系
    print("[AI Pipeline] 步骤3：构建关系...")
    relations = ai_build_relations(entities, full_text)

    # 第四步：组装完整数据结构
    print("[AI Pipeline] 步骤4：组装数据...")

    # 生成节点坐标（圆形布局）
    import math
    cat_nodes = []
    for i, cat in enumerate(categories):
        angle = (i / max(len(categories), 1)) * 2 * math.pi - math.pi / 2
        cat_nodes.append({
            "id": cat.get("id", f"cat{i}"),
            "label": cat["label"],
            "x": 50 + 25 * math.cos(angle),
            "y": 50 + 25 * math.sin(angle)
        })

    entity_nodes = []
    cat_labels = {c["label"]: c.get("id", f"cat{i}") for i, c in enumerate(categories)}
    for i, entity in enumerate(entities):
        angle = (i / max(len(entities), 1)) * 2 * math.pi
        entity_nodes.append({
            "id": f"e{i}",
            "label": entity["label"],
            "x": 50 + 40 * math.cos(angle),
            "y": 50 + 40 * math.sin(angle),
            "categoryId": entity.get("categoryId", ""),
            "category": entity.get("category", "")
        })

    # 生成边
    edges = [["book", c["id"]] for c in cat_nodes]
    for node in entity_nodes:
        if node.get("categoryId"):
            edges.append([node["categoryId"], node["id"]])

    # 添加 AI 生成的关系边
    label_to_id = {e["label"]: f"e{i}" for i, e in enumerate(entities)}
    for rel in relations:
        if len(rel) >= 2:
            from_id = label_to_id.get(rel[0])
            to_id = label_to_id.get(rel[1])
            if from_id and to_id and from_id != to_id:
                edge = [from_id, to_id]
                if edge not in edges:
                    edges.append(edge)

    # 组装完整书籍数据
    book_data = {
        "title": book_title,
        "urn": urn,
        "meta": {
            "author": "（待补充）",
            "dynasty": "（待补充）",
            "category": "（待补充）",
            "summary": extraction.get("summary", f"《{book_title}》的 AI 结构化分析"),
            "stats": {"chapters": len(full_text_paragraphs), "words": len(full_text)}
        },
        "fullText": full_text_paragraphs,
        "nodes": [{"id": "book", "label": f"《{book_title}》", "x": 50, "y": 50}] + cat_nodes + entity_nodes,
        "edges": edges,
        "details": details,
        "categories": categories,
        "generatedAt": int(time.time()),
        "generatedBy": "DeepSeek V4-Flash"
    }

    print(f"[AI Pipeline] 完成！{len(book_data['nodes'])} 节点，{len(edges)} 边，{len(details)} 详情")
    return book_data

# ============ 缓存系统 ============

def get_cache_key(urn):
    """生成缓存文件名"""
    safe = re.sub(r'[^a-zA-Z0-9_-]', '_', urn)
    return os.path.join(CACHE_DIR, f"{safe}.json")

def load_cache(urn):
    """从缓存加载"""
    path = get_cache_key(urn)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return None

def save_cache(urn, data):
    """保存到缓存"""
    path = get_cache_key(urn)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[Cache] 已缓存: {path}")
    except Exception as e:
        print(f"[Cache] 保存失败: {e}")

# ============ API 路由 ============

@app.route("/api/search")
def api_search():
    """搜索古籍"""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "缺少搜索关键词"}), 400

    results = ctext_search(q)
    return jsonify({"query": q, "results": results})

@app.route("/api/book/<path:urn>")
def api_get_book(urn):
    """获取古籍全文 - 优先从本地文本库获取"""
    # 1. 检查本地文本库
    for title, info in LOCAL_TEXT_LIBRARY.items():
        if info["urn"] == urn or urn == f"local:{title}" or urn == title:
            try:
                with open(os.path.join(TEXTS_DIR, info["file"]), "r", encoding="utf-8") as f:
                    data = json.load(f)
                print(f"[Book] 从本地文本库获取《{title}》: {len(data['paragraphs'])} 段")
                return jsonify({
                    "urn": urn,
                    "info": {"title": title, "source": "local"},
                    "paragraphs": data["paragraphs"],
                    "count": len(data["paragraphs"]),
                    "source": "local"
                })
            except Exception as e:
                print(f"[Book] 本地获取失败: {e}")

    # 2. 回退到 ctext API
    print(f"[Book] 从 ctext API 获取: {urn}")
    info = ctext_gettextinfo(urn)
    paragraphs = fetch_full_text(urn)

    if not paragraphs:
        return jsonify({
            "urn": urn,
            "error": "获取原文失败 - 该古籍可能需要认证或暂未收录。推荐使用本地文本库中的古籍。",
            "available_local": list(LOCAL_TEXT_LIBRARY.keys())
        }), 404

    return jsonify({
        "urn": urn,
        "info": info,
        "paragraphs": paragraphs,
        "count": len(paragraphs),
        "source": "ctext"
    })

@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    """AI 深度处理：生成知识图谱 + 深度注释"""
    data = request.json
    urn = data.get("urn", "")
    title = data.get("title", "")
    paragraphs = data.get("paragraphs", [])

    if not paragraphs:
        return jsonify({"error": "缺少原文数据"}), 400

    # 检查缓存
    cached = load_cache(urn or title)
    if cached:
        print(f"[API] 命中缓存: {title}")
        return jsonify(cached)

    # 执行 AI 管线
    book_data = generate_book_data(title, urn, paragraphs)

    if "error" in book_data:
        return jsonify(book_data), 500

    # 保存缓存
    save_cache(urn or title, book_data)

    return jsonify(book_data)

@app.route("/api/analyze-text", methods=["POST"])
def api_analyze_text():
    """轻量级文本分析 - 单次 AI 调用，返回结构化解读"""
    data = request.json
    text = data.get("text", "").strip()

    if not text or len(text) < 10:
        return jsonify({"error": "文本太短"}), 400

    # 截取前 4000 字
    sample = text[:4000]

    prompt = f"""你是一位古籍研究专家。请对以下文言文段落进行结构化分析。

文本内容：
{sample}

请返回 JSON 格式（只返回 JSON，不要其他内容）：
{{
  "title": "为这段文本取一个简洁的标题（不超过10字）",
  "summary": "一句话概括这段文本的核心内容（不超过50字）",
  "genre": "文体判断（如：语录体、散文、诗歌、史传、笔记等）",
  "keywords": ["关键词1", "关键词2", "关键词3", "关键词4", "关键词5"],
  "entities": [
    {{
      "label": "实体名称",
      "type": "人物/地点/概念/物品/事件",
      "brief": "一句话解释这个实体在文中的含义"
    }}
  ],
  "translation": "将这段文本翻译为现代白话文（保持原文段落结构）",
  "analysis": "从文学、哲学或历史角度分析这段文本的价值和内涵（100-200字）"
}}

要求：
- 实体提取 5-10 个最重要的
- 关键词 3-5 个
- 翻译要准确流畅，不要逐字翻译，要传达原意
- 分析要有深度，不要泛泛而谈"""

    result = deepseek_chat([
        {"role": "system", "content": "你是古籍研究专家，擅长文本分析和注释。只返回有效的 JSON。"},
        {"role": "user", "content": prompt}
    ], temperature=0.3, max_tokens=4096)

    if "error" in result:
        return jsonify({"error": result["error"]}), 500

    try:
        content = result["content"]
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        analysis = json.loads(content)
        return jsonify(analysis)
    except Exception as e:
        print(f"[api_analyze_text] parse error: {e}")
        return jsonify({"error": f"解析失败: {e}"}), 500

@app.route("/api/analyze-stream", methods=["POST"])
def api_analyze_stream():
    """AI 深度处理 - 流式版本，逐步返回结果"""
    data = request.json
    urn = data.get("urn", "")
    title = data.get("title", "")
    paragraphs = data.get("paragraphs", [])

    if not paragraphs:
        return jsonify({"error": "缺少原文数据"}), 400

    # 检查缓存
    cached = load_cache(urn or title)
    if cached:
        print(f"[API] 命中缓存(流式): {title}")

        def cached_stream():
            # 先发送基础结构
            base = {
                "type": "structure",
                "title": cached["title"],
                "urn": cached.get("urn", urn),
                "meta": cached.get("meta", {}),
                "fullText": cached.get("fullText", []),
                "nodes": cached.get("nodes", []),
                "edges": cached.get("edges", []),
                "categories": cached.get("categories", []),
                "detailCount": len(cached.get("details", {}))
            }
            yield f"data: {json.dumps(base, ensure_ascii=False)}\n\n"
            # 逐个发送详情
            for label, detail in cached.get("details", {}).items():
                yield f"data: {json.dumps({'type': 'detail', 'label': label, 'detail': detail}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        return Response(stream_with_context(cached_stream()), mimetype="text/event-stream")

    # 未缓存：实时 AI 处理
    full_text = "\n\n".join(paragraphs)
    print(f"[AI Pipeline] 流式处理《{title}》，共 {len(paragraphs)} 段，{len(full_text)} 字")

    def generate_stream():
        try:
            # 步骤1：实体提取
            yield f"data: {json.dumps({'type': 'progress', 'step': 'extract', 'text': 'AI 正在提取实体...', 'sub': f'分析 {len(paragraphs)} 段原文'}, ensure_ascii=False)}\n\n"
            extraction = ai_extract_entities(full_text, title)
            if not extraction:
                yield f"data: {json.dumps({'type': 'error', 'message': '实体提取失败'})}\n\n"
                return

            entities = extraction.get("entities", [])
            categories = extraction.get("categories", [])
            print(f"[AI Pipeline] 提取到 {len(entities)} 个实体，{len(categories)} 个分类")

            # 步骤2：构建基础结构
            import math
            cat_nodes = []
            for i, cat in enumerate(categories):
                angle = (i / max(len(categories), 1)) * 2 * math.pi - math.pi / 2
                cat_nodes.append({
                    "id": cat.get("id", f"cat{i}"),
                    "label": cat["label"],
                    "x": 50 + 25 * math.cos(angle),
                    "y": 50 + 25 * math.sin(angle)
                })

            entity_nodes = []
            for i, entity in enumerate(entities):
                angle = (i / max(len(entities), 1)) * 2 * math.pi
                entity_nodes.append({
                    "id": f"e{i}",
                    "label": entity["label"],
                    "x": 50 + 40 * math.cos(angle),
                    "y": 50 + 40 * math.sin(angle),
                    "categoryId": entity.get("categoryId", ""),
                    "category": entity.get("category", "")
                })

            edges = [["book", c["id"]] for c in cat_nodes]
            for node in entity_nodes:
                if node.get("categoryId"):
                    edges.append([node["categoryId"], node["id"]])

            # 发送基础结构
            base_data = {
                "type": "structure",
                "title": title,
                "urn": urn,
                "meta": {
                    "author": "（待补充）",
                    "dynasty": "（待补充）",
                    "category": "（待补充）",
                    "summary": extraction.get("summary", f"《{title}》的 AI 结构化分析"),
                    "stats": {"chapters": len(paragraphs), "words": len(full_text)}
                },
                "fullText": paragraphs,
                "nodes": [{"id": "book", "label": f"《{title}》", "x": 50, "y": 50}] + cat_nodes + entity_nodes,
                "edges": edges,
                "categories": categories,
                "detailCount": len(entities)
            }
            yield f"data: {json.dumps(base_data, ensure_ascii=False)}\n\n"

            # 步骤3：逐个生成详情
            details = {}
            for i, entity in enumerate(entities):
                label = entity["label"]
                brief = entity.get("brief", "")
                category = entity.get("category", "")

                yield f"data: {json.dumps({'type': 'progress', 'step': 'detail', 'text': f'正在生成深度注释...', 'sub': f'[{i+1}/{len(entities)}] {label}'}, ensure_ascii=False)}\n\n"

                # 找到上下文
                context = ""
                for para in paragraphs:
                    if label in para:
                        context = para
                        break
                if not context:
                    context = full_text[:2000]

                detail = ai_generate_detail(label, brief, category, title, context)
                if not detail:
                    detail = {
                        "tags": [category, "自动生成"],
                        "sketch": brief,
                        "original": context[:100] if context else "",
                        "translation": "",
                        "notes": brief,
                        "evolution": "",
                        "impact": "",
                        "more": "",
                        "timeline": [],
                        "related": []
                    }

                details[label] = detail
                yield f"data: {json.dumps({'type': 'detail', 'label': label, 'detail': detail}, ensure_ascii=False)}\n\n"

            # 步骤4：构建关系
            yield f"data: {json.dumps({'type': 'progress', 'step': 'relations', 'text': '正在构建知识关联...', 'sub': '分析实体间关系'}, ensure_ascii=False)}\n\n"
            relations = ai_build_relations(entities, full_text)

            label_to_id = {e["label"]: f"e{i}" for i, e in enumerate(entities)}
            extra_edges = []
            for rel in relations:
                if len(rel) >= 2:
                    from_id = label_to_id.get(rel[0])
                    to_id = label_to_id.get(rel[1])
                    if from_id and to_id and from_id != to_id:
                        edge = [from_id, to_id]
                        if edge not in edges and edge not in extra_edges:
                            extra_edges.append(edge)

            if extra_edges:
                yield f"data: {json.dumps({'type': 'edges', 'edges': extra_edges}, ensure_ascii=False)}\n\n"

            # 保存缓存
            book_data = {
                "title": title,
                "urn": urn,
                "meta": base_data["meta"],
                "fullText": paragraphs,
                "nodes": base_data["nodes"],
                "edges": edges + extra_edges,
                "details": details,
                "categories": categories,
                "generatedAt": int(time.time()),
                "generatedBy": "DeepSeek V4-Flash"
            }
            save_cache(urn or title, book_data)

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            print(f"[AI Pipeline] 流式错误: {e}")
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return Response(stream_with_context(generate_stream()), mimetype="text/event-stream")

@app.route("/api/cache/<path:urn>")
def api_get_cache(urn):
    """获取缓存的已处理书籍"""
    cached = load_cache(urn)
    if cached:
        return jsonify(cached)
    return jsonify({"error": "未找到缓存"}), 404

@app.route("/api/status")
def api_status():
    """系统状态"""
    cache_files = [f for f in os.listdir(CACHE_DIR) if f.endswith(".json")]
    return jsonify({
        "status": "running",
        "deepseek_configured": bool(DEEPSEEK_API_KEY),
        "cached_books": len(cache_files),
        "cache_list": cache_files,
        "local_library": LOCAL_TEXT_LIBRARY
    })

# ============ 静态文件服务 ============

@app.after_request
def add_no_cache_headers(resp):
    # 防止浏览器缓存 index.html / 静态资源，确保改动立即生效
    if resp.status_code == 200 and (
        request.path == "/" or
        request.path.endswith(".html") or
        request.path.endswith(".css") or
        request.path.endswith(".js") or
        request.path.endswith(".svg")
    ):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp

@app.route("/")
def index():
    resp = send_from_directory(os.path.dirname(__file__), "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp

@app.route("/<path:path>")
def static_files(path):
    resp = send_from_directory(os.path.dirname(__file__), path)
    # HTML/SVG 不缓存；其他静态资源缓存 1 小时
    if path.endswith((".html", ".svg")):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    else:
        resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp

if __name__ == "__main__":
    # 本地开发：python server.py
    # 生产环境由 gunicorn 启动（见 Procfile / railway.json），不会走到这里
    port = int(os.environ.get("PORT", 5001))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    print("=" * 50)
    print("  书脉 AI 后端服务启动")
    print(f"  DeepSeek API: {'已配置' if DEEPSEEK_API_KEY else '未配置（请设置环境变量 DEEPSEEK_API_KEY）'}")
    print(f"  缓存目录: {CACHE_DIR}")
    print(f"  访问地址: http://localhost:{port}")
    print(f"  Debug 模式: {'开启' if debug else '关闭'}")
    print("=" * 50)
    app.run(host="0.0.0.0", port=port, debug=debug)
