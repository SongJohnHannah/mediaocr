"""
mediaocr 编排层（core.py）
=========================
OCR 批处理：把「渲染/抽帧/去重」与「引擎 OCR」解耦，支持串行与 spawn 进程池。

性能设计依据（本机 6 核实测）：
  - 单连接内引擎串行（wcocr 为全局单连接），并行只能靠多进程
  - 多守护进程并行上限 ~1.6x（引擎级共享串行点），故池默认最多 3 worker
  - OCR 调用本身占 A4 页成本的 ~74%，主优化是「减少调用次数」：
    视频帧去重（重复帧不 OCR）、自适应 dpi（低置信度才升 300 重试）
  - PDF 双缓冲经实测无效（渲染仅 0.04s/页、OCR 已吃满 CPU），故不引入
"""

import itertools
import os
import sys
import tempfile
import threading
from pathlib import Path


# ── 并行阈值：job 数低于此值时不用池（spawn 池启动 ~1s，小批量反而亏）──
POOL_MIN_JOBS = 8
# 自适应 dpi：低于此置信度且当前 dpi<300 时，该页升 300 重试
ADAPTIVE_DPI_CONFIDENCE = 0.6
MAX_DDPI = 300

# 视频帧去重：全帧 + 底部字幕带 双指纹，均低于阈值才算"重复帧"跳过 OCR。
# 实测（6核/压缩噪声 60kbps）：静止帧 full≈0.0003/bottom≈0.0012，
# 字幕变化帧 full≈0.0009/bottom≈0.0044，阈值取二者中点留 ~2x 余量。
FINGERPRINT_W, FINGERPRINT_H = 64, 48       # 全帧指纹分辨率
BOTTOM_RATIO = 0.22                          # 底部字幕带高度占比
VIDEO_DEDUP_FULL_THRESHOLD = 0.0005
VIDEO_DEDUP_BOTTOM_THRESHOLD = 0.0020

# 池 worker 全局（由 initializer 设置）
_WORKER_OCR = None

# 模块级（spawn 子进程可导入，无副作用）
_temp_counter = itertools.count(1)


def _temp_dir() -> Path:
    """Linux 优先 /dev/shm（tmpfs，避免磁盘 IO），否则系统临时目录。"""
    if sys.platform.startswith("linux") and Path("/dev/shm").is_dir():
        return Path("/dev/shm")
    return Path(tempfile.gettempdir())


class TempBuffer:
    """可复用的临时 PNG 缓冲：覆盖写，避免每页反复创建/删除临时文件。

    /dev/shm 上的读写是内存操作，PDF/视频批量场景省掉磁盘往返。
    """

    def __init__(self, tag: str):
        self.path = _temp_dir() / f"mediaocr_{tag}_{os.getpid()}_{next(_temp_counter)}.png"

    def cleanup(self):
        try:
            self.path.unlink()
        except OSError:
            pass


def _format_result(result: dict, source: str, compact: bool = False) -> dict:
    """把 wcocr 原始结果整理成友好 JSON。

    compact=True 时省略每行 box/confidence（大批量 stdio 传输减半），
    始终保留 line_count 与平均 confidence 供自适应 dpi 判断。
    """
    lines = []
    confs = []
    for item in result.get("ocr_response", []):
        lines.append({
            "text": item.get("text", ""),
            "confidence": round(item.get("rate", 0.0), 4),
            "box": {
                "left": round(item.get("left", 0), 1),
                "top": round(item.get("top", 0), 1),
                "right": round(item.get("right", 0), 1),
                "bottom": round(item.get("bottom", 0), 1),
            },
        })
        confs.append(item.get("rate", 0.0))
    out = {
        "errcode": result.get("errcode", 0),
        "source": source,
        "width": result.get("width"),
        "height": result.get("height"),
        "line_count": len(lines),
        "confidence": round((sum(confs) / len(confs)) if confs else 0.0, 4),
        "text": "\n".join(l["text"] for l in lines),
    }
    if not compact:
        out["lines"] = lines
    return out


def _low_confidence(r: dict) -> bool:
    """自适应 dpi 触发条件：没识别出文字，或平均置信度过低。"""
    return r.get("line_count", 0) == 0 or r.get("confidence", 1.0) < ADAPTIVE_DPI_CONFIDENCE


def _ocr_image_inner(ocr, image_path: str, compact: bool = False) -> dict:
    try:
        result = ocr.ocr(image_path)
    except Exception as e:
        return {"errcode": -1, "error": str(e), "source": image_path}
    return _format_result(result, str(Path(image_path).resolve()), compact)


# ── 视频帧去重 ────────────────────────────────────────────────

def _abs_mean(a: bytes, b: bytes) -> float:
    """两串指纹的平均绝对像素差（0~1）。"""
    if a is None or b is None or len(a) != len(b):
        return 1.0
    s = 0
    for x, y in zip(a, b):
        s += abs(x - y)
    return s / (len(a) * 255.0)


def _fingerprint(image_path: str):
    """全帧 + 底部字幕带 双灰度指纹。

    全帧（64×48）捕捉任何画面变化；底部字幕带（底部 22%）放大字幕变化信号，
    避免"字幕变了但全帧平均差太小"被误判为重复帧。失败返回 None。
    """
    try:
        import fitz
        doc = fitz.open(image_path)
        page = doc[0]
        w, h = page.rect.width, page.rect.height
        if w <= 0 or h <= 0:
            return None
        full = page.get_pixmap(
            matrix=fitz.Matrix(FINGERPRINT_W / w, FINGERPRINT_H / h),
            colorspace=fitz.csGRAY,
        )
        bh = max(1, int(h * BOTTOM_RATIO))
        clip = fitz.Rect(0, h - bh, w, h)
        bottom = page.get_pixmap(
            clip=clip, matrix=fitz.Matrix(FINGERPRINT_W / w, FINGERPRINT_H / bh),
            colorspace=fitz.csGRAY,
        )
        data = (bytes(full.samples), bytes(bottom.samples))
        doc.close()
        return data
    except Exception:
        return None


def _frame_diff(a, b) -> tuple[float, float]:
    """返回 (全帧差, 底部差)。"""
    return _abs_mean(a[0], b[0]), _abs_mean(a[1], b[1])


def _dedup_frames(frame_files) -> list[dict]:
    """抽帧后去重：全帧+底部字幕带均低于阈值才视为重复帧，跳过 OCR。"""
    kept = []
    prev = None
    for idx, f in enumerate(frame_files, 1):
        fp = _fingerprint(str(f))
        skip = False
        if prev is not None and fp is not None:
            df, db = _frame_diff(prev, fp)
            skip = (df < VIDEO_DEDUP_FULL_THRESHOLD
                    and db < VIDEO_DEDUP_BOTTOM_THRESHOLD)
        if not skip:
            kept.append({"frame": idx, "path": str(f)})
        if fp is not None:
            prev = fp
    return kept


# ── 单页 PDF：渲染 + OCR + 自适应 dpi ─────────────────────────

def _render_and_ocr(ocr, doc, page_idx: int, dpi: int, auto_dpi: bool, compact: bool) -> dict:
    """渲染单页并 OCR（含自适应 dpi 重试）。doc 为已打开的 fitz 文档。"""
    import fitz
    buf = TempBuffer("pdf")
    page = doc[page_idx]
    try:
        pix = page.get_pixmap(dpi=dpi)
        pix.save(str(buf.path))
        r = _ocr_image_inner(ocr, str(buf.path), compact)
        if auto_dpi and dpi < MAX_DDPI and _low_confidence(r):
            pix2 = page.get_pixmap(dpi=MAX_DDPI)
            pix2.save(str(buf.path))
            r = _ocr_image_inner(ocr, str(buf.path), compact)
        return {
            "page": page_idx + 1,
            "type": "ocr",
            "text": r.get("text", ""),
            "line_count": r.get("line_count", 0),
            **({"lines": r.get("lines", [])} if not compact else {}),
        }
    finally:
        buf.cleanup()


# ── 并行分发（spawn 进程池）──────────────────────────────────

def _pool_init(eng):
    """池 worker 初始化：每个 worker 独立引擎连接 + 独立守护进程。"""
    global _WORKER_OCR
    from server import WeChatOCR
    _WORKER_OCR = WeChatOCR(*eng)
    _WORKER_OCR._ensure_init()  # 预热：spawn 守护进程，首个 job 免冷启动


def process_job_with(ocr, job: dict) -> dict:
    """处理单个 OCR job（串行模式显式传 ocr）。job 为可 pickle 的纯 dict。"""
    kind = job["kind"]
    compact = job.get("compact", False)
    if kind == "image":
        return _ocr_image_inner(ocr, job["path"], compact)
    if kind == "pdf_page":
        import fitz
        doc = fitz.open(job["pdf"])
        try:
            return _render_and_ocr(ocr, doc, job["page"], job.get("dpi", 150),
                                   job.get("auto_dpi", True), compact)
        finally:
            doc.close()
    if kind == "video_frame":
        r = _ocr_image_inner(ocr, job["path"], compact)
        return {
            "frame": job["frame"],
            "timestamp_sec": job.get("ts", 0.0),
            "text": r.get("text", ""),
            "line_count": r.get("line_count", 0),
            **({"lines": r.get("lines", [])} if not compact else {}),
        }
    raise ValueError(f"未知 job 类型: {kind}")


def process_job(job: dict) -> dict:
    """池 worker 入口（读模块全局 _WORKER_OCR）。"""
    return process_job_with(_WORKER_OCR, job)


def run_pipeline(jobs: list[dict], ocr, workers: int = 1) -> list:
    """分发一批 OCR job。

    - workers<=1 或 job 数不足：串行（PDF 页走双缓冲，其他直接 OCR）
    - workers>1 且 job 数足够：spawn 进程池，每 worker 独立引擎，结果按输入序
    """
    if not jobs:
        return []
    if workers > 1 and len(jobs) >= POOL_MIN_JOBS:
        eng = (ocr.wxocr_dll, ocr.wechat_path)
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        with ctx.Pool(workers, initializer=_pool_init, initargs=(eng,)) as pool:
            return pool.map(process_job, jobs, chunksize=1)
    return [process_job_with(ocr, j) for j in jobs]


# ── PDF / 视频 顶层处理（工具层调用）──────────────────────────

def ocr_pdf(ocr, pdf: Path, max_pages: int = 20, dpi: int = 150,
            auto_dpi: bool = True, compact: bool = False, workers: int = 1) -> dict:
    """PDF 识别：文本型直接抽取；扫描型渲染 + OCR（串行双缓冲或池）。"""
    fitz = _ensure_fitz()
    if fitz is None:
        st = _dep_state("pymupdf")
        if st == "installing":
            return {"errcode": -2, "error": "pymupdf 正在后台安装，请稍后重试"}
        return {"errcode": -1, "error": "pymupdf 安装失败，请手动运行: "
                f"{sys.executable} -m pip install pymupdf 或 uv pip install --python {sys.executable} pymupdf"}

    doc = fitz.open(str(pdf))
    total = min(doc.page_count, max_pages)
    text_entries, scanned = [], []
    for i in range(total):
        tl = doc[i].get_text().strip()
        if len(tl) >= 10:
            text_entries.append({"page": i + 1, "type": "text_layer",
                                 "text": tl, "line_count": len(tl.splitlines())})
        else:
            scanned.append(i)
    doc.close()

    jobs = [{"kind": "pdf_page", "pdf": str(pdf), "page": pi,
             "dpi": dpi, "auto_dpi": auto_dpi, "compact": compact} for pi in scanned]
    ocr_pages = run_pipeline(jobs, ocr, workers)

    by_page = {e["page"]: e for e in text_entries + ocr_pages}
    pages_out = [by_page[i] for i in range(1, total + 1) if i in by_page]
    ok = sum(1 for p in pages_out if p.get("text"))
    full_text = "\n\n".join(
        f"--- 第{p['page']}页 ---\n{p['text']}" for p in pages_out if p.get("text")
    )
    return {
        "errcode": 0,
        "source": str(pdf.resolve()),
        "total_pages": total,
        "processed_pages": len(pages_out),
        "pages_with_text": ok,
        "pages": pages_out,
        "text": full_text,
    }


def ocr_video(ocr, video: Path, interval_sec: float = 5.0, max_frames: int = 10,
              dedup: bool = True, compact: bool = False, workers: int = 1) -> dict:
    """视频识别：ffmpeg 抽帧 → 帧去重 → OCR（串行或池）。"""
    import subprocess
    ffmpeg = _ensure_ffmpeg()
    if ffmpeg is None:
        st = _dep_state("ffmpeg")
        if st == "installing":
            return {"errcode": -2, "error": "ffmpeg 正在后台安装，请稍后重试"}
        return {"errcode": -1, "error": "ffmpeg 安装失败，请手动安装（https://ffmpeg.org）或运行: "
                f"{sys.executable} -m pip install imageio-ffmpeg"}

    with tempfile.TemporaryDirectory() as td:
        pattern = os.path.join(td, "frame_%04d.png")
        cmd = [ffmpeg, "-y", "-i", str(video), "-vf", f"fps=1/{interval_sec}",
               "-frames:v", str(max_frames), pattern]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            return {"errcode": -1, "error": f"ffmpeg 抽帧失败: {r.stderr[-300:]}"}

        frame_files = sorted(Path(td).glob("frame_*.png"))
        total = len(frame_files)
        if total == 0:
            return {"errcode": 0, "source": str(video.resolve()), "frames_processed": 0,
                    "frames_with_text": 0, "frames": [], "text": ""}

        kept = _dedup_frames(frame_files) if dedup else [
            {"frame": i, "path": str(f)} for i, f in enumerate(frame_files, 1)
        ]
        # 补时间戳（按原始帧序号）
        ts_map = {i: round((i - 1) * interval_sec, 1) for i in range(1, total + 1)}
        jobs = [{"kind": "video_frame", "frame": k["frame"], "ts": ts_map[k["frame"]],
                 "path": k["path"], "compact": compact} for k in kept]
        results = run_pipeline(jobs, ocr, workers)

        frames_out = results
        skipped = total - len(results)
        ok = sum(1 for f in frames_out if f.get("text"))
        full_text = "\n\n".join(
            f"--- {f['timestamp_sec']}s ---\n{f['text']}" for f in frames_out if f.get("text")
        )
        return {
            "errcode": 0,
            "source": str(video.resolve()),
            "frames_processed": total,
            "frames_with_text": ok,
            "frames_skipped_dedup": skipped,
            "frames": frames_out,
            "text": full_text,
        }


# ── 依赖异步安装（调用内不阻塞；启动时后台预装）──────────────

_DEP_STATE: dict[str, str] = {}  # "pymupdf"/"ffmpeg" -> "ok"|"installing"|"failed"
_PIP_LOCK = threading.Lock()


def _pip_install(pkg: str) -> bool:
    """安装到当前 venv。优先 pip，uv venv 无 pip 时退回 uv。"""
    with _PIP_LOCK:
        candidates = [
            [sys.executable, "-m", "pip", "install", "--quiet",
             "--disable-pip-version-check", "--no-input", pkg],
            ["uv", "pip", "install", "--quiet", "--python", sys.executable, pkg],
        ]
        for cmd in candidates:
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                if r.returncode == 0:
                    return True
            except Exception:
                continue
        return False


def _dep_state(name: str) -> str:
    return _DEP_STATE.get(name, "missing")


def _start_dep_install(name: str, pkg: str):
    with _PIP_LOCK:
        if _DEP_STATE.get(name) == "installing":
            return
        _DEP_STATE[name] = "installing"

    def _run():
        ok = _pip_install(pkg)
        _DEP_STATE[name] = "ok" if ok else "failed"
        print(f"[mediaocr] {name} 安装{'成功' if ok else '失败'}", file=sys.stderr)

    threading.Thread(target=_run, daemon=True).start()


def _ensure_fitz():
    """返回 fitz 模块；不可用时启动后台安装并返回 None（调用方看状态给提示）。"""
    try:
        import fitz
        _DEP_STATE["pymupdf"] = "ok"
        return fitz
    except ImportError:
        pass
    _start_dep_install("pymupdf", "pymupdf")
    return None


def _ensure_ffmpeg():
    """返回 ffmpeg 可执行路径；不可用时启动后台安装并返回 None。"""
    import shutil
    path = shutil.which("ffmpeg")
    if path:
        _DEP_STATE["ffmpeg"] = "ok"
        return path
    try:
        import imageio_ffmpeg
        _DEP_STATE["ffmpeg"] = "ok"
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass
    except Exception:
        return None
    _start_dep_install("ffmpeg", "imageio-ffmpeg")
    return None


# 供 server.py 启动时后台预装
def preinstall_deps():
    """启动时后台预检/预装 pymupdf 与 ffmpeg，避免首个 PDF/视频调用卡安装。"""
    threading.Thread(target=lambda: (_ensure_fitz(), _ensure_ffmpeg()), daemon=True).start()


def warmup_engine(ocr):
    """后台预热：init 引擎 + 尝试 OCR 一张生成的小图，把冷启动移出首个工具调用。"""
    try:
        ocr._ensure_init()
        path = _make_warmup_image()
        if path:
            ocr.ocr(path)
            try:
                Path(path).unlink()
            except OSError:
                pass
        print("[mediaocr] 引擎预热完成", file=sys.stderr)
    except Exception as e:
        print(f"[mediaocr] 预热失败（忽略）: {e}", file=sys.stderr)


def _make_warmup_image():
    """用 fitz 渲染一张含文字的小图作为预热样本；无 fitz 时返回 None（仅 init 预热）。"""
    try:
        import fitz
        doc = fitz.open()
        page = doc.new_page(width=200, height=60)
        page.insert_text((10, 35), "mediaocr warmup 0123456789", fontsize=14)
        path = _temp_dir() / f"mediaocr_warmup_{os.getpid()}.png"
        page.get_pixmap(dpi=150).save(str(path))
        doc.close()
        return str(path)
    except Exception:
        return None
