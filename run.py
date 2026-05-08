"""
B站直播回放全自动切片工具
用法: python run.py <BV号或完整链接> [-p 97] [-g 10]
示例: python run.py BV1eN9aBmEDp
      python run.py https://www.bilibili.com/video/BV1eN9aBmEDp/
"""
import os
import sys
import subprocess
import argparse
import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(SCRIPT_DIR, "highlights_input")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "highlights_output")

def run_cmd(cmd, desc):
    print(f"\n>>> {desc}")
    result = subprocess.run(cmd, shell=True, cwd=SCRIPT_DIR)
    if result.returncode != 0:
        print(f"错误: {desc} 失败 (exit {result.returncode})")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="B站直播回放全自动切片")
    parser.add_argument("url", help="B站视频链接")
    parser.add_argument("-p", "--percentile", type=int, default=97, help="高光阈值 (默认97)")
    parser.add_argument("-g", "--gap", type=int, default=10, help="合并间隔秒数 (默认10)")
    parser.add_argument("--pad-before", type=int, default=3)
    parser.add_argument("--pad-after", type=int, default=5)
    parser.add_argument("-o", "--output", default="highlights_output")
    args = parser.parse_args()

    # 兼容 BV 号：自动补全为完整链接
    url = args.url.strip()
    if url.startswith("BV") or url.startswith("bv"):
        url = f"https://www.bilibili.com/video/{url}/"
    elif "bilibili.com" not in url:
        print("错误: 请输入 BV 号或 B站视频链接")
        sys.exit(1)

    # Step 0: 获取UP主名称
    print(">>> 获取视频信息...")
    result = subprocess.run(
        f'yt-dlp --print uploader "{url}"',
        shell=True, cwd=SCRIPT_DIR, capture_output=True, text=True
    )
    uploader = result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None
    print(f"  UP主: {uploader or '未知'}")

    # 确保目录存在
    os.makedirs(INPUT_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Step 1: 下载弹幕
    run_cmd(
        f'yt-dlp --write-subs --sub-format danmaku --skip-download '
        f'-o "{INPUT_DIR}/danmaku_temp" "{url}"',
        "下载弹幕"
    )

    # 找到下载的弹幕文件
    danmaku_files = glob.glob(os.path.join(INPUT_DIR, "danmaku_temp*.xml"))
    if not danmaku_files:
        danmaku_files = glob.glob(os.path.join(INPUT_DIR, "danmaku_temp*.json"))
    if not danmaku_files:
        print("错误: 未找到弹幕文件")
        sys.exit(1)
    danmaku_path = danmaku_files[0]
    print(f"  弹幕文件: {os.path.basename(danmaku_path)}")

    # Step 2: 下载视频
    run_cmd(
        f'yt-dlp -f "30032+30280/best" '
        f'-o "{INPUT_DIR}/video_temp.%(ext)s" "{url}"',
        "下载视频 (480p)"
    )

    # 找到下载的视频文件
    video_files = glob.glob(os.path.join(INPUT_DIR, "video_temp*.mp4"))
    if not video_files:
        video_files = glob.glob(os.path.join(INPUT_DIR, "video_temp*.flv"))
    if not video_files:
        video_files = glob.glob(os.path.join(INPUT_DIR, "video_temp*.mkv"))
    if not video_files:
        print("错误: 未找到视频文件")
        sys.exit(1)
    video_path = video_files[0]
    print(f"  视频文件: {os.path.basename(video_path)}")

    # Step 3: 自动切片
    uploader_arg = f'--uploader "{uploader}"' if uploader else ''
    run_cmd(
        f'python -u "{SCRIPT_DIR}/auto_clipper.py" "{video_path}" "{danmaku_path}" '
        f'-o "{args.output}" -p {args.percentile} -g {args.gap} '
        f'--pad-before {args.pad_before} --pad-after {args.pad_after} '
        f'{uploader_arg}',
        "自动切片"
    )

    # Step 4: 清理
    print("\n>>> 清理临时文件...")
    for f in [video_path, danmaku_path]:
        if os.path.exists(f):
            os.remove(f)
            print(f"  已删除: {os.path.basename(f)}")

    print(f"\n完成! 高光片段在: {os.path.abspath(args.output)}")

if __name__ == "__main__":
    main()
