#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
syncfile.py — Đồng bộ 2 server theo cơ chế snapshot (hardlink) dùng rsync + sharding

Kiến trúc:
- Kéo dữ liệu từ SRC (qua SSH/rsync) về DEST.
- Mỗi lần chạy tạo 1 snapshot mới: DEST_BASE/snapshots/<YYYYmmdd_HHMMSS>.
- Nếu có snapshot trước đó, dùng --link-dest để hardlink file không đổi → tiết kiệm dung lượng.
- Chia nhỏ cây thư mục thành nhiều "shard" để rsync song song.
- Dùng staging (thư mục tạm) để đảm bảo tính toàn vẹn; chỉ khi tất cả shard thành công mới rename thành snapshot.
- Có xoay vòng số snapshot giữ lại (retention).

Ghi chú vận hành:
- DEST_BASE và snapshots phải cùng filesystem để hardlink hoạt động.
- Luôn exclude "/snapshots/" và "/latest" ở nguồn để tránh vòng lặp.
- Webserver trỏ vào DEST_BASE/latest (symlink tới snapshot mới nhất).
- Có thể đặt snapshot read-only: `chmod -R a-w DEST_BASE/snapshots/*`.

Cách dùng nhanh:
  python3 syncfile.py \
    --src user@host:/data/src \
    --dest-base /backup/datasetA \
    --identity ~/.ssh/id_ed25519 \
    --port 22 \
    --shards 8 \
    --keep-last 14 \
    --exclude-file /etc/rsync-snapshot.excludes

File /etc/rsync-snapshot.excludes nên có:
  /snapshots/
  /latest

"""

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import os
import shutil
import signal
import subprocess
import sys
import shlex
from pathlib import Path
from typing import List, Tuple

# -------------------------------
# Utils
# -------------------------------

def sh(cmd: List[str], check: bool = True, cwd: Path | None = None) -> subprocess.CompletedProcess:
    print("[CMD]", " ".join(cmd))
    return subprocess.run(cmd, check=check, cwd=str(cwd) if cwd else None)


def which(binary: str) -> str:
    p = shutil.which(binary)
    if not p:
        print(f"❌ Thiếu binary: {binary}")
        sys.exit(2)
    return p


def now_ts() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def shlex_quote(s: str) -> str:
    return shlex.quote(s)

# -------------------------------
# Sharding helpers
# -------------------------------

def list_top_entries_via_ssh(src_spec: str, identity: str | None, port: int | None, extra_ssh: List[str]) -> List[str]:
    """Liệt kê các phần tử level-1 (file/dir) bên SRC.
    Trả về danh sách relative names (không có slash đầu).
    src_spec dạng: user@host:/path/to/root
    """
    if ":" not in src_spec:
        raise ValueError("--src phải dạng user@host:/abs/path hoặc host:/abs/path")
    host, remote_path = src_spec.split(":", 1)

    # Dùng -print0 để tránh nhúng NUL vào argv; strip './' ở client
    remote_cmd = [
        "bash", "-lc",
        f"cd {shlex_quote(remote_path)} && find . -mindepth 1 -maxdepth 1 -print0",
    ]

    # ssh command
    ssh_cmd = ["ssh"]
    if identity:
        ssh_cmd += ["-i", identity]
    if port:
        ssh_cmd += ["-p", str(port)]
    # Reuse session để nhanh hơn
    ssh_cmd += [
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "ControlMaster=auto",
        "-o", f"ControlPath={str(Path.home())}/.ssh/cm-%r@%h:%p",
        "-o", "ControlPersist=60s",
    ]
    ssh_cmd += extra_ssh
    ssh_cmd += [host, "--"] + remote_cmd

    cp = subprocess.run(ssh_cmd, check=True, stdout=subprocess.PIPE)
    raw = cp.stdout.decode("utf-8", errors="ignore").split("\0")
    items: List[str] = []
    for s in raw:
        if not s:
            continue
        if s.startswith("./"):
            s = s[2:]
        s = s.strip("/")
        if s:
            items.append(s)
    items.sort()
    return items


def shard_by_chunk(items: List[str], shards: int) -> List[List[str]]:
    if shards <= 1:
        return [items]
    buckets: List[List[str]] = [[] for _ in range(shards)]
    for i, name in enumerate(items):
        buckets[i % shards].append(name)
    return buckets


def shard_by_hash(items: List[str], shards: int) -> List[List[str]]:
    if shards <= 1:
        return [items]
    buckets: List[List[str]] = [[] for _ in range(shards)]
    for name in items:
        h = int(hashlib.blake2b(name.encode("utf-8"), digest_size=4).hexdigest(), 16)
        buckets[h % shards].append(name)
    return buckets

# -------------------------------
# Rsync helpers
# -------------------------------

def build_rsync_base_cmd(identity: str | None, port: int | None, compress: bool, bwlimit: int | None, extra_ssh: List[str]) -> List[str]:
    ssh_parts = ["ssh"]
    if identity:
        ssh_parts += ["-i", identity]
    if port:
        ssh_parts += ["-p", str(port)]
    ssh_parts += [
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "ControlMaster=auto",
        "-o", f"ControlPath={str(Path.home())}/.ssh/cm-%r@%h:%p",
        "-o", "ControlPersist=300s",
    ] + extra_ssh

    base = [
        which("rsync"),
        "-aHAX",            # archive + hardlinks + xattrs
        "--numeric-ids",
        "--partial",
        "--prune-empty-dirs",
        "--no-inc-recursive",  # ổn định RAM cho cây lớn
        "-e", " ".join(ssh_parts),
        "--info=stats2",
    ]
    if compress:
        base.append("-z")
    if bwlimit:
        base += ["--bwlimit", str(bwlimit)]
    return base


def write_include_file(target: Path, rels: List[str]) -> None:
    lines = []
    for r in rels:
        r = r.strip("/")
        if not r:
            continue
        lines.append(f"+ /{r}/***\n")
        lines.append(f"+ /{r}\n")
    lines.append("- /***\n")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(lines), encoding="utf-8")


def rsync_one_shard(src_spec: str, stage_dir: Path, link_dest: Path | None, include_file: Path,
                    base_cmd: List[str], extra_rsync_opts: List[str], dry_run: bool) -> None:
    dest = stage_dir
    cmd = list(base_cmd)
    if link_dest:
        cmd += ["--link-dest", str(link_dest.resolve())]
    cmd += [
        "--include-from", str(include_file),
        src_spec + "/",            # nhớ dấu '/'
        str(dest),
    ] + extra_rsync_opts
    if dry_run:
        cmd.append("-n")
    sh(cmd)

# -------------------------------
# Snapshot housekeeping
# -------------------------------

def latest_snapshot_path(base: Path) -> Path | None:
    snaps = sorted((base / "snapshots").glob("*"))
    return snaps[-1] if snaps else None


def atomic_rename(src: Path, dst: Path) -> None:
    src.replace(dst)  # atomic trong cùng filesystem


def rotate_snapshots(base: Path, keep_last: int) -> None:
    snaps = sorted((base / "snapshots").glob("*"))
    if keep_last <= 0:
        return
    to_delete = snaps[:-keep_last]
    for p in to_delete:
        print(f"🧹 Xoá snapshot cũ: {p}")
        shutil.rmtree(p, ignore_errors=False)

# -------------------------------
# Main
# -------------------------------

def main():
    ap = argparse.ArgumentParser(description="Rsync snapshot + hardlink + sharding")
    ap.add_argument("--src", required=True, help="user@host:/abs/path nguồn")
    ap.add_argument("--dest-base", required=True, help="Thư mục gốc chứa snapshots ở máy đích")
    ap.add_argument("--identity", default=None, help="SSH identity file (private key)")
    ap.add_argument("--port", type=int, default=None, help="SSH port")
    ap.add_argument("--extra-ssh", default="", help="Chuỗi tuỳ chọn SSH bổ sung, ví dụ: -o StrictHostKeyChecking=no")
    ap.add_argument("--exclude-file", default=None, help="Đường dẫn file exclude patterns cho rsync (option --exclude-from)")
    ap.add_argument("--compress", action="store_true", help="Bật nén khi truyền (-z)")
    ap.add_argument("--bwlimit", type=int, default=None, help="Giới hạn băng thông KB/s (rsync --bwlimit)")
    ap.add_argument("--shards", type=int, default=4, help="Số shard chạy song song")
    ap.add_argument("--shard-mode", choices=["chunk", "hash"], default="chunk", help="Cách chia shard: chunk (round-robin) hoặc hash")
    ap.add_argument("--keep-last", type=int, default=14, help="Giữ lại bao nhiêu snapshot gần nhất")
    ap.add_argument("--dry-run", action="store_true", help="Chạy thử, không ghi dữ liệu")

    args = ap.parse_args()

    which("ssh"); which("rsync")

    extra_ssh = args.extra_ssh.split() if args.extra_ssh else []

    base = Path(args.dest_base).resolve()
    (base / "snapshots").mkdir(parents=True, exist_ok=True)
    (base / "logs").mkdir(parents=True, exist_ok=True)

    # Liệt kê entries level-1 để chia shard
    print("🔎 Đang lấy danh sách level-1 từ nguồn...")
    items = list_top_entries_via_ssh(args.src, args.identity, args.port, extra_ssh)
    if not items:
        print("⚠️ Không có mục nào dưới nguồn, dừng.")
        return

    if args.shards > len(items):
        args.shards = max(1, len(items))

    buckets = shard_by_chunk(items, args.shards) if args.shard_mode == "chunk" else shard_by_hash(items, args.shards)

    ts = now_ts()
    stage = base / f".stage-{ts}"
    stage.mkdir(parents=True, exist_ok=True)

    latest = latest_snapshot_path(base)
    link_dest = latest if latest else None

    # include files theo shard
    include_dir = stage / ".include"
    include_dir.mkdir(exist_ok=True)
    include_files: List[Tuple[int, Path]] = []
    for i, rels in enumerate(buckets):
        inc = include_dir / f"shard{i:02d}.inc"
        write_include_file(inc, rels)
        include_files.append((i, inc))

    # rsync base cmd cho các shard
    base_cmd = build_rsync_base_cmd(args.identity, args.port, args.compress, args.bwlimit, extra_ssh)

    # ⬇️ Tạo danh sách opts cho các shard
    extra_rsync_opts: List[str] = []
    # Luôn exclude vòng lặp
    extra_rsync_opts += ["--exclude", "/snapshots/", "--exclude", "/latest"]
    # Thêm exclude-file của bạn (nếu có)
    if args.exclude_file:
        extra_rsync_opts += ["--exclude-from", args.exclude_file]

    # Bắt tín hiệu để dọn staging nếu bị kill
    def cleanup_and_exit(signum, frame):
        print(f"\n🛑 Nhận tín hiệu {signum}, dọn staging {stage} ...")
        try:
            shutil.rmtree(stage)
        except Exception as e:
            print("Cleanup lỗi:", e)
        sys.exit(1)
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, cleanup_and_exit)

    # Chạy song song các shard (KHÔNG delete trong pass shard)
    print(f"🚚 Bắt đầu rsync {len(include_files)} shard → {stage} (link-dest={link_dest if link_dest else 'None'})")
    errors: List[Tuple[int, Exception]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(include_files)) as ex:
        futs = []
        for i, inc in include_files:
            futs.append(ex.submit(
                rsync_one_shard,
                args.src,
                stage,
                link_dest,
                inc,
                base_cmd,
                extra_rsync_opts,
                args.dry_run,
            ))
        for i, fut in enumerate(concurrent.futures.as_completed(futs)):
            try:
                fut.result()
                print(f"✅ Shard {i+1}/{len(futs)} xong")
            except Exception as e:
                errors.append((i, e))

    if errors:
        print("❌ Một số shard thất bại:")
        for i, e in errors:
            print(f"  - shard#{i}: {e}")
        print("→ Huỷ snapshot, dọn staging.")
        shutil.rmtree(stage, ignore_errors=True)
        sys.exit(1)

    # Pass cuối: reconcile delete toàn cây (an toàn vì đã có dữ liệu stage)
    if not args.dry_run:
        print("🧮 Reconcile delete toàn cây...")
        final_cmd = build_rsync_base_cmd(args.identity, args.port, args.compress, args.bwlimit, extra_ssh)
        final_cmd += ["--delete", "--delete-excluded"]
        if link_dest:
            final_cmd += ["--link-dest", str(link_dest.resolve())]
        # Excludes giống như ở shard-pass
        final_cmd += ["--exclude", "/snapshots/", "--exclude", "/latest"]
        if args.exclude_file:
            final_cmd += ["--exclude-from", args.exclude_file]
        sh(final_cmd + [args.src + "/", str(stage)])

    # Tạo snapshot mới
    snap_dir = base / "snapshots" / ts
    if not args.dry_run:
        print(f"🧩 Đóng gói snapshot: {snap_dir}")
        atomic_rename(stage, snap_dir)
        # cập nhật symlink latest
        latest_link = base / "latest"
        tmp_link = base / f".latest-{ts}"
        try:
            if tmp_link.exists() or tmp_link.is_symlink():
                tmp_link.unlink()
            tmp_link.symlink_to(Path("snapshots") / ts)
            tmp_link.replace(latest_link)
        except Exception as e:
            print("Cập nhật symlink 'latest' lỗi:", e)

        # Xoay vòng
        rotate_snapshots(base, args.keep_last)
    else:
        print("[DRY-RUN] Bỏ qua reconcile/delete, rename và rotate")
        shutil.rmtree(stage, ignore_errors=True)

    print("🎉 Hoàn tất.")

if __name__ == "__main__":
    main()