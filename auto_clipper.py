import os
import json
import re
import numpy as np
from collections import Counter
from pydub import AudioSegment
from pydub.silence import detect_nonsilent

# ==========================================
# 1. 环境配置
# ==========================================
FFMPEG_BIN = os.environ.get(
    "FFMPEG_BIN",
    r"C:\ffmpeg-2026-04-30-git-cc3ca17127-essentials_build\bin",
)
os.environ["PATH"] += os.pathsep + FFMPEG_BIN
AudioSegment.converter = os.path.join(FFMPEG_BIN, "ffmpeg.exe")
AudioSegment.ffprobe = os.path.join(FFMPEG_BIN, "ffprobe.exe")

# moviepy v1/v2 兼容
try:
    from moviepy import VideoFileClip
    _MOVIEPY_V2 = True
except ImportError:
    from moviepy.editor import VideoFileClip
    _MOVIEPY_V2 = False


def _subclip(video, start, end):
    """moviepy v1/v2 兼容：subclip / subclipped"""
    if _MOVIEPY_V2:
        return video.subclipped(start, end)
    else:
        return video.subclip(start, end)

# ==========================================
# 2. 弹幕解析 (B站直播弹幕JSON格式)
# ==========================================

def parse_danmaku_json(json_path, duration_sec):
    """解析B站直播弹幕JSON，返回每秒密度数组"""
    print("正在解析弹幕JSON...")
    density = np.zeros(int(duration_sec) + 1)

    with open(json_path, "r", encoding="utf-8") as f:
        raw = f.read()

    # B站直播弹幕可能是多行JSON对象，每行一条记录
    lines = raw.strip().split("\n")
    count = 0
    for line in lines:
        try:
            obj = json.loads(line)
            t = obj.get("time") or obj.get("timeline") or obj.get("progress")
            if t is None:
                continue
            sec = int(float(t))
            if 0 <= sec < len(density):
                density[sec] += 1
                count += 1
        except (json.JSONDecodeError, ValueError):
            pass

    if count == 0:
        # 尝试作为单个JSON数组解析
        try:
            arr = json.loads(raw)
            if isinstance(arr, list):
                for item in arr:
                    t = item.get("time") or item.get("timeline") or item.get("progress")
                    if t is None:
                        continue
                    sec = int(float(t))
                    if 0 <= sec < len(density):
                        density[sec] += 1
                        count += 1
        except (json.JSONDecodeError, ValueError):
            pass

    print(f"  解析到 {count} 条弹幕")
    return density


def parse_danmaku_xml(xml_path, duration_sec):
    """解析B站XML弹幕(备用)"""
    import xml.etree.ElementTree as ET
    print("正在解析弹幕XML...")
    density = np.zeros(int(duration_sec) + 1)
    tree = ET.parse(xml_path)
    root = tree.getroot()
    count = 0
    for d in root.findall('.//d'):
        p = d.get('p', '')
        if p:
            sec = int(float(p.split(',')[0]))
            if 0 <= sec < len(density):
                density[sec] += 1
                count += 1
    print(f"  解析到 {count} 条弹幕")
    return density


def load_danmaku(path, duration_sec):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        return parse_danmaku_json(path, duration_sec)
    elif ext == ".xml":
        return parse_danmaku_xml(path, duration_sec)
    else:
        raise ValueError(f"不支持的弹幕格式: {ext}")


# ==========================================
# 2b. 弹幕关键词提取 → 自动起名
# ==========================================

# 无意义词/符号过滤
_STOP_PATTERNS = re.compile(
    r'^[\d\s,.!?，。！？、…~～\-\+=\[\]【】\(\)（）/\\:：;；\'"＂＂\|@#￥%&*]+$'
)
_BRACKET_PATTERN = re.compile(r'\[.*?\]')


def _is_meaningful(text):
    """过滤纯数字、纯符号、空内容"""
    t = _BRACKET_PATTERN.sub('', text).strip()
    if len(t) < 2:
        return False
    if _STOP_PATTERNS.match(t):
        return False
    return True


def parse_danmaku_text_by_time(danmaku_path, duration_sec):
    """重新读取弹幕文件，返回每秒的弹幕文本列表"""
    import xml.etree.ElementTree as ET
    texts_by_sec = {i: [] for i in range(int(duration_sec) + 1)}

    ext = os.path.splitext(danmaku_path)[1].lower()
    if ext == ".xml":
        tree = ET.parse(danmaku_path)
        for d in tree.getroot().findall('.//d'):
            p = d.get('p', '')
            if not p:
                continue
            sec = int(float(p.split(',')[0]))
            text = (d.text or '').strip()
            if 0 <= sec < len(texts_by_sec) and text:
                texts_by_sec[sec].append(text)
    elif ext == ".json":
        with open(danmaku_path, "r", encoding="utf-8") as f:
            raw = f.read()
        lines = raw.strip().split("\n")
        for line in lines:
            try:
                obj = json.loads(line)
                t = obj.get("time") or obj.get("timeline") or obj.get("progress")
                text = (obj.get("text") or obj.get("content") or '').strip()
                if t is None or not text:
                    continue
                sec = int(float(t))
                if 0 <= sec < len(texts_by_sec):
                    texts_by_sec[sec].append(text)
            except (json.JSONDecodeError, ValueError):
                pass

    return texts_by_sec


# ==========================================
# 弹幕情绪分类 → 叙事型标题生成
# ==========================================

_MOOD_PATTERNS = {
    'shock':   ['？', '??', '???', '什么', '卧槽', '我去', '离谱', '啊？？', '啥', '咋',
                '不会吧', '真的假的', '怎么可能', '还有这种', '看不懂', '发生了什么'],
    'hype':    ['666', '牛', '强', '秀', '神', '燃', '帅', '顶', '无敌', '逆天',
                '天秀', '超神', '起飞', '杀疯', '无敌了', '太强了', '绝了', '离谱'],
    'funny':   ['哈哈', '笑死', '草', '乐', '天才', '好笑', '绷不住', '太搞了',
                '笑喷', '笑不活', '整活', '绝活', '节目效果'],
    'fail':    ['翻车', '完了', '破产', '亏', '寄', '崩', '惨', '裂开', '破防',
                '完蛋', '没了', '白给', '下饭', '太惨了', '心疼', '倒霉'],
    'comeback':['翻盘', '逆转', '起飞', '赚', '赢', '天秀', '反杀', '逆袭',
                '赚飞了', '发财了', '好起来了', '让X追X', '翻回来了'],
    'intense': ['刺激', '紧张', '心跳', '不敢看', '极限', '惊险', '差点',
                '险胜', '绝杀', '拼了', '冲', '刚', '敢'],
}


def _classify_mood(texts_by_sec, start_sec, end_sec):
    """分析时段弹幕情绪分布，返回主导情绪"""
    scores = {k: 0 for k in _MOOD_PATTERNS}
    for sec in range(start_sec, min(end_sec + 1, len(texts_by_sec))):
        for text in texts_by_sec.get(sec, []):
            for mood, keywords in _MOOD_PATTERNS.items():
                if any(kw in text for kw in keywords):
                    scores[mood] += 1
    if max(scores.values()) == 0:
        return 'hype'  # 默认
    return max(scores, key=scores.get)


def _pick_rep_danmaku(texts_by_sec, start_sec, end_sec):
    """选一条最能代表该片段的弹幕"""
    candidates = []
    for sec in range(start_sec, min(end_sec + 1, len(texts_by_sec))):
        for text in texts_by_sec.get(sec, []):
            if _is_meaningful(text) and 3 <= len(text) <= 12:
                candidates.append(text)
    if not candidates:
        return None
    counter = Counter(candidates)
    # 选出现次数最多的（≥2次优先）
    for text, count in counter.most_common(10):
        if count >= 2 and 4 <= len(text) <= 8:
            return text
    return counter.most_common(1)[0][0]


# 叙事型标题模板库（参考热门切片的实际命名方式）
_TITLE_TEMPLATES = {
    'shock': [
        "【{up}】观众集体看傻：{danmaku}",
        "【{up}】{danmaku}？这波什么水平",
        "【{up}】弹幕全是「{danmaku}」",
    ],
    'hype': [
        "【{up}】{danmaku}！弹幕直接炸了",
        "【{up}】名场面：{danmaku}",
        "【{up}】{danmaku}，观众看呆了",
    ],
    'funny': [
        "【{up}】{danmaku}！笑不活了",
        "【{up}】节目效果拉满：{danmaku}",
        "【{up}】名场面：{danmaku}",
    ],
    'fail': [
        "【{up}】翻车！{danmaku}",
        "【{up}】{danmaku}，主播血压拉满",
        "【{up}】观众：{danmaku}",
    ],
    'comeback': [
        "【{up}】{danmaku}！惊天逆转",
        "【{up}】{danmaku}，弹幕沸腾",
        "【{up}】翻盘时刻：{danmaku}",
    ],
    'intense': [
        "【{up}】{danmaku}，心跳拉满",
        "【{up}】极限操作：{danmaku}",
        "【{up}】{danmaku}！太刺激了",
    ],
}


def generate_clip_name(uploader, texts_by_sec, start_sec, end_sec):
    """根据弹幕情绪+代表弹幕，生成叙事型标题"""
    m = start_sec // 60
    s = start_sec % 60
    time_str = f"{m:02d}m{s:02d}s"
    up = uploader or "主播"

    if not texts_by_sec:
        return f"【{up}】高能时刻_{time_str}.mp4"

    mood = _classify_mood(texts_by_sec, start_sec, end_sec)
    danmaku = _pick_rep_danmaku(texts_by_sec, start_sec, end_sec)
    if not danmaku:
        danmaku = "高能时刻"

    templates = _TITLE_TEMPLATES.get(mood, _TITLE_TEMPLATES['hype'])
    idx = (start_sec * 7 + end_sec * 13) % len(templates)
    title = templates[idx].format(up=up, danmaku=danmaku)

    # 限制总长（中文约18字以内）
    if len(title) > 28:
        title = title[:26] + '…'

    return f"{title}_{time_str}.mp4"

# ==========================================
# 3. 音频分析
# ==========================================

def analyze_audio(audio_path):
    """分析音频：逐秒能量 + 静音检测"""
    print("正在分析音频...")
    audio = AudioSegment.from_file(audio_path)
    duration = len(audio) / 1000.0

    # 每秒RMS能量
    energy = []
    for i in range(0, len(audio), 1000):
        chunk = audio[i:i+1000]
        energy.append(chunk.rms)
    energy = np.array(energy, dtype=float)

    # 检测非静音区间（说话段）
    nonsilent_ranges = detect_nonsilent(audio, min_silence_len=500, silence_thresh=-40)
    # 转为逐秒mask
    speech_mask = np.zeros(int(duration) + 1)
    for start_ms, end_ms in nonsilent_ranges:
        s = start_ms // 1000
        e = end_ms // 1000 + 1
        if s < len(speech_mask):
            speech_mask[s:min(e, len(speech_mask))] = 1

    return energy, speech_mask, duration

# ==========================================
# 4. 高光评分模型
# ==========================================

def compute_highlight_score(danmaku_density, audio_energy, speech_mask):
    """综合弹幕密度 + 音频能量 + 说话段计算高光分数"""
    L = min(len(danmaku_density), len(audio_energy))
    d = danmaku_density[:L]
    a = audio_energy[:L]
    s = speech_mask[:L]

    # 归一化
    d_norm = d / (np.max(d) + 1e-6)
    a_norm = a / (np.max(a) + 1e-6)

    # 弹幕爆发分：与前后邻域均值比较（排除自身，避免永远≤1的问题）
    window = 10
    d_baseline = np.zeros(L)
    for i in range(L):
        left = d[max(0,i-window):i]
        right = d[i+1:min(L,i+window+1)]
        if len(left) > 0 and len(right) > 0:
            d_baseline[i] = (np.mean(left) + np.mean(right)) / 2
        elif len(left) > 0:
            d_baseline[i] = np.mean(left)
        elif len(right) > 0:
            d_baseline[i] = np.mean(right)
        else:
            d_baseline[i] = d[i]
    d_burst = d / (d_baseline + 1e-6)
    d_burst = np.clip(d_burst, 0, 5)

    # 综合分 = 绝对弹幕密度 × 音频能量 × 爆发加成 × 说话段权重
    score = d_norm * a_norm * (1.0 + d_burst) * (0.3 + 0.7 * s)

    # 平滑一下避免抖动
    kernel = np.ones(3) / 3
    score = np.convolve(score, kernel, mode='same')

    return score

# ==========================================
# 5. 片段提取
# ==========================================

def extract_segments(score, duration, top_percentile=95, min_gap=10, pad_before=3, pad_after=5):
    """从分数中提取高光片段，合并相邻片段"""
    threshold = np.percentile(score, top_percentile)
    if threshold <= 0:
        threshold = np.percentile(score, 98)
    print(f"  高光阈值: {threshold:.4f}, 高于阈值的秒数: {np.sum(score >= threshold)}")

    top_idx = np.where(score >= threshold)[0]
    if len(top_idx) == 0:
        return []

    segments = []
    seg_start = top_idx[0]
    for i in range(1, len(top_idx)):
        if top_idx[i] - top_idx[i-1] > min_gap:
            s = max(0, seg_start - pad_before)
            e = min(duration, top_idx[i-1] + pad_after)
            if e - s >= 3:  # 最短3秒
                segments.append((int(s), int(e)))
            seg_start = top_idx[i]
    s = max(0, seg_start - pad_before)
    e = min(duration, top_idx[-1] + pad_after)
    if e - s >= 3:
        segments.append((int(s), int(e)))

    return segments

# ==========================================
# 6. 视频导出
# ==========================================

def export_clips(video_path, segments, output_dir="highlights_output",
                 danmaku_texts=None, uploader=None):
    """导出切片并生成描述文件"""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    video = VideoFileClip(video_path)
    print(f"\n正在导出 {len(segments)} 个高光片段...")

    info = []
    for i, (s, e) in enumerate(segments):
        # 弹幕情绪分析 → 叙事型标题
        filename = generate_clip_name(uploader, danmaku_texts, s, e) if danmaku_texts else \
                   generate_clip_name(uploader, None, s, e)
        # 清理文件名中的非法字符
        filename = re.sub(r'[\\/:*?"<>|]', '_', filename)

        filepath = os.path.join(output_dir, filename)
        m, sec = divmod(s, 60)
        print(f"  [{i+1}/{len(segments)}] {m:02d}m{sec:02d}s → {filename}")
        clip = _subclip(video, s, e)
        clip.write_videofile(filepath, codec="libx264", audio_codec="aac", logger=None)
        info.append({"index": i, "start": s, "end": e, "duration": e - s, "file": filename})

    video.close()

    # 写片段信息
    with open(os.path.join(output_dir, "segments.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

    return info

# ==========================================
# 7. 主流程
# ==========================================

def highlight_clip(video_path, danmaku_path, output_dir="highlights_output",
                   top_percentile=95, min_gap=10, pad_before=3, pad_after=5,
                   uploader=None):
    """
    直播切片主函数

    参数:
        video_path:      视频文件路径
        danmaku_path:    弹幕文件路径 (.json 或 .xml)
        output_dir:      输出目录
        top_percentile:  高光阈值百分位 (默认95, 越小片段越多)
        min_gap:         两个片段合并的最大间隔秒数
        pad_before:      片段前提前秒数
        pad_after:       片段后延后秒数
        uploader:        UP主名称 (用于自动起名)
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"视频不存在: {video_path}")
    if not os.path.exists(danmaku_path):
        raise FileNotFoundError(f"弹幕不存在: {danmaku_path}")

    print("=" * 50)
    print("  B站直播自动切片工具")
    print("=" * 50)

    # Step 1: 提取音频
    video = VideoFileClip(video_path)
    duration = video.duration
    temp_audio = os.path.join(os.path.dirname(video_path) or ".", "temp_analysis.wav")
    print(f"\n视频时长: {duration:.0f}秒")
    print("正在提取音频...")
    video.audio.write_audiofile(temp_audio, logger=None)
    video.close()

    # Step 2: 分析
    audio_energy, speech_mask, _ = analyze_audio(temp_audio)
    danmaku_density = load_danmaku(danmaku_path, duration)

    # Step 3: 评分
    print("\n正在计算高光分数...")
    score = compute_highlight_score(danmaku_density, audio_energy, speech_mask)

    # Step 4: 提取片段
    print("正在识别高光片段...")
    segments = extract_segments(score, duration, top_percentile, min_gap, pad_before, pad_after)

    if not segments:
        print("\n未检测到高光片段，请尝试降低 top_percentile 参数。")
        if os.path.exists(temp_audio):
            os.remove(temp_audio)
        return []

    # Step 5: 导出 (含自动起名)
    danmaku_texts = parse_danmaku_text_by_time(danmaku_path, duration)
    info = export_clips(video_path, segments, output_dir, danmaku_texts, uploader)

    # 清理
    if os.path.exists(temp_audio):
        os.remove(temp_audio)

    print(f"\n完成! 共导出 {len(info)} 个片段到 {os.path.abspath(output_dir)}")
    return info

# ==========================================
# 8. 命令行入口
# ==========================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="B站直播自动切片工具")
    parser.add_argument("video", help="视频文件路径")
    parser.add_argument("danmaku", help="弹幕文件路径 (.json / .xml)")
    parser.add_argument("-o", "--output", default="highlights_output", help="输出目录")
    parser.add_argument("-p", "--percentile", type=int, default=95,
                        help="高光阈值百分位 (默认95, 越低片段越多)")
    parser.add_argument("-g", "--gap", type=int, default=10,
                        help="合并片段最大间隔秒数 (默认10)")
    parser.add_argument("--pad-before", type=int, default=3, help="片段前摇秒数 (默认3)")
    parser.add_argument("--pad-after", type=int, default=5, help="片段后摇秒数 (默认5)")
    parser.add_argument("--uploader", default=None, help="UP主名称 (用于自动起名)")

    args = parser.parse_args()

    highlight_clip(
        video_path=args.video,
        danmaku_path=args.danmaku,
        output_dir=args.output,
        top_percentile=args.percentile,
        min_gap=args.gap,
        pad_before=args.pad_before,
        pad_after=args.pad_after,
        uploader=args.uploader,
    )
