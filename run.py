#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
import hashlib
import shutil
import threading
import urllib.parse
import sqlite3
import random
import shlex
import subprocess
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from pathlib import Path

# ===== Optional progress bar =====
try:
    from tqdm import tqdm
    USE_TQDM = True
except ImportError:
    USE_TQDM = False

# ===== SSH =====
try:
    import paramiko
except ImportError:
    print("Missing library 'paramiko'. Install: pip install paramiko")
    sys.exit(1)

# ===== Requests (fallback cho sendMessage) =====
try:
    import requests
    HAVE_REQUESTS = True
except ImportError:
    HAVE_REQUESTS = False

# ===== Email =====
import smtplib, ssl
from email.mime.text import MIMEText
from email.utils import formatdate
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email import encoders

# ===== PrettyTable (use only if HTML formatting is needed) =====
try:
    from prettytable import PrettyTable
    HAVE_PRETTYTABLE = True
except ImportError:
    HAVE_PRETTYTABLE = False

# ===== BLAKE3 =====
try:
    import blake3
    HAVE_BLAKE3 = True
except ImportError:
    HAVE_BLAKE3 = False

# ===================== CONFIGURATION =====================
# Server1 (destination)
SERVER1_ROOT = "/home/data"

# Server2 (source)
SERVER2_HOST = "10.237.7.75"
SERVER2_PORT = 22
SERVER2_USER = "root"

# Auth: select 1 (KEY OR PASSWD)
SERVER2_PASSWORD = None
SERVER2_KEY_FILE = "/root/.ssh/id_rsa"
SERVER2_KEY_PASSPHRASE = 'SERVER2_KEY_PASSPHRASE'

# >>>> multiple source directories on server2 <<<<
SERVER2_ROOTS = [
    "/home/data",
    "/var/log",
]
SERVER2_ROOT_ALIASES = None  # ex: ["home_data", "var_data2"]

# File location on server1
USE_MERGE_SUBROOT = False      # True -> SERVER1_ROOT/merge_from_server2/<alias?>/...
ON_CONFLICT = "version"       # "skip" | "overwrite" | "suffix" | "version"

# Remote hash performance / budget per cycle (to avoid full scan)
MAX_WORKERS = 8
HASH_CHUNK = 1024 * 1024  # 1MB
REMOTE_HASH_BUDGET_FILES = 500      # max hash N remote files per round
REMOTE_HASH_BUDGET_BYTES = 5 * 1024 * 1024 * 1024  # 5GB per round (0 = ignore bytes limit)
# Performance / priority when selecting remote files to hash
REMOTE_HASH_SECONDARY = "size"  # "size" | "mtime"

# Logging / Metrics
TEXTFILE_COLLECTOR_DIR = "/var/lib/node_exporter/textfile_collector"
PROM_METRIC_BASENAME = "merge_compare"

# Daemon
RUN_FOREVER = True
SLEEP_SECONDS = 30

# ====== ALERT ======
ENABLE_TELEGRAM_ALERT = True
ENABLE_EMAIL_ALERT = False

# ====== Telegram config ======
TELEGRAM_BOT_TOKEN = "7509858733:AAHw4x3LhS64X7cGbi7LrcbW6jXDgqgdjhQ"
TELEGRAM_CHAT_ID = "-4572608076"

# ====== Email config ======
EMAIL_SMTP_HOST = "smtp.zenhub.vn"
EMAIL_SMTP_PORT = 587
EMAIL_SMTP_USER = "noreply@zenhub.vn"
EMAIL_SMTP_PASS = "password"
EMAIL_FROM = "noreply@zenhub.vn"
EMAIL_TO = ["hoanghd@zenhub.vn", "khoanh@zenhub.vn", "haiht@zenhub.vn"]
EMAIL_SUBJECT_PREFIX = "[Merge Alert] "

# ====== Hash/Cache DB ======
CACHE_DB = "/var/tmp/merge_hash_cache.sqlite"
# Will automatically select "blake3" if the remote has b3sum, otherwise "sha256"
PREFERRED_HASH = "blake3" if HAVE_BLAKE3 else "sha256"

# ====== Scrub (optional) ======
SCRUB_ENABLED = True
# run scrub when on time & minute below (every day)
SCRUB_AT_HOUR = 2
SCRUB_AT_MIN = 0
# percentage of files randomly selected to re-hash each scrub (0.0-1.0). 1.0 = full.
SCRUB_PERCENT_LOCAL = 0.05
SCRUB_PERCENT_REMOTE = 0.02
# or set an absolute limit on the number of files scrubbed each time (preferred if >0)
SCRUB_LIMIT_LOCAL = 5000
SCRUB_LIMIT_REMOTE = 3000

# ====================================================

_thread_local = threading.local()

# ===================== DB =====================
def db_init():
    os.makedirs(os.path.dirname(CACHE_DB), exist_ok=True)
    with sqlite3.connect(CACHE_DB) as db:
        db.execute("""PRAGMA journal_mode=WAL;""")
        db.execute("""CREATE TABLE IF NOT EXISTS filemeta_local(
            root TEXT, rel TEXT, size INTEGER, mtime INTEGER,
            algo TEXT, hash TEXT, last_seen INTEGER, last_hashed INTEGER,
            PRIMARY KEY(root, rel, algo)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS filemeta_remote(
            host TEXT, root TEXT, rel TEXT, size INTEGER, mtime INTEGER,
            algo TEXT, hash TEXT, last_seen INTEGER, last_hashed INTEGER,
            PRIMARY KEY(host, root, rel, algo)
        )""")

    with sqlite3.connect(CACHE_DB) as db:
        db.execute("CREATE INDEX IF NOT EXISTS idx_local_algo_hash ON filemeta_local(algo, hash)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_local_last_seen ON filemeta_local(last_seen)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_remote_algo_hash ON filemeta_remote(algo, hash)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_remote_last_seen ON filemeta_remote(last_seen)")

def _now():
    return int(time.time())

# ===================== Hash helpers =====================
def _hash_new(algo):
    return blake3.blake3() if (algo == "blake3" and HAVE_BLAKE3) else hashlib.sha256()

def compute_hash_local(path, algo, chunk_size=HASH_CHUNK):
    h = _hash_new(algo)
    with open(path, 'rb') as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            h.update(data)
    return h.hexdigest()

# ===================== Local scan =====================
def scan_local_metadata(root):
    root = os.path.abspath(root)
    items = []  # (rel, size, mtime)

    for dirpath, dirnames, filenames in os.walk(root):
        # IGNORE all *_logs* directories (eg: merge_from_server2/_logs, _logs)
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        if rel_dir == "_logs" or rel_dir.endswith("/_logs") or "/_logs/" in rel_dir:
            # Don't go any further into the _logs branch
            dirnames[:] = []
            continue

        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except Exception:
                continue
            rel = os.path.relpath(full, root).replace("\\", "/")
            # If the file is in _logs then ignore it
            if "/_logs/" in rel or rel.startswith("_logs") or rel.startswith("merge_from_server2/_logs"):
                continue
            items.append((rel, st.st_size, int(st.st_mtime)))
    return items

def refresh_local_cache(root, algo):
    """Update local metadata to DB, only hash dirty files"""
    db_init()
    now = _now()
    hashed = 0
    with sqlite3.connect(CACHE_DB) as db:
        db.row_factory = sqlite3.Row
        cur = db.cursor()
        items = scan_local_metadata(root)

        for rel, size, mtime in items:
            cur.execute("""SELECT size, mtime, hash FROM filemeta_local
                           WHERE root=? AND rel=? AND algo=?""",
                        (root, rel, algo))
            row = cur.fetchone()
            is_dirty = (row is None) or (row["size"] != size) or (row["mtime"] != mtime) or (row["hash"] is None)

            if is_dirty:
                # Hash file dirty
                full = os.path.join(root, rel)
                try:
                    h = compute_hash_local(full, algo)
                except Exception as e:
                    # If hash fails, still update last_seen to avoid continuous retrying
                    h = None
                cur.execute("""INSERT OR REPLACE INTO filemeta_local
                               (root, rel, size, mtime, algo, hash, last_seen, last_hashed)
                               VALUES(?,?,?,?,?,?,?,?)""",
                            (root, rel, size, mtime, algo, h, now, now if h else None))
                hashed += 1
            else:
                # only update last_seen
                cur.execute("""UPDATE filemeta_local
                               SET last_seen=? WHERE root=? AND rel=? AND algo=?""",
                            (now, root, rel, algo))
        db.commit()
    return hashed

# ===================== Remote scan =====================
def _ssh_exec(ssh_client, cmd):
    stdin, stdout, stderr = ssh_client.exec_command(cmd, get_pty=False)
    out = stdout.read().decode("utf-8", "ignore")
    err = stderr.read().decode("utf-8", "ignore")
    return out, err

def detect_remote_hash_algo(ssh_client):
    try:
        out, _ = _ssh_exec(ssh_client, "command -v b3sum >/dev/null 2>&1 && echo OK || echo NO")
        if "OK" in out:
            return "blake3"
    except Exception:
        pass
    return "sha256"

def list_remote_metadata(ssh_client, root):
    """Returns a list of (rel, mtime_epoch, size), ignoring the *_logs* directory on the remote."""
    root_esc = root.replace("'", "'\\''")
    # Drop files in any directory named _logs
    cmd = (
        f"cd '{root_esc}' && "
        f"find . -type f "
        f"! -path './_logs/*' ! -path '*/_logs/*' "
        f"-printf '%T@\\t%s\\t%p\\n'"
    )
    out, err = _ssh_exec(ssh_client, cmd)
    if err.strip():
        print(f"[remote-meta:{root}] {err.strip()}")
    items = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            mtime_s, size_s, relp = line.split("\t", 2)
            if relp.startswith("./"):
                relp = relp[2:]
            mtime_epoch = int(float(mtime_s))  # %T@ is float -> cast to int
            items.append((relp.replace("\\","/"), mtime_epoch, int(size_s)))
        except Exception:
            continue
    return items

def refresh_remote_metadata(ssh_client, host, roots, algo):
    """Chỉ update metadata (size/mtime/last_seen); KHÔNG hash ở đây"""
    db_init()
    now = _now()
    with sqlite3.connect(CACHE_DB) as db:
        cur = db.cursor()
        for root in roots:
            items = list_remote_metadata(ssh_client, root)
            print(f"[remote-meta] {root}: {len(items)} files")

            for rel, mtime, size in items:
                cur.execute("""
                INSERT INTO filemeta_remote(host, root, rel, size, mtime, algo, hash, last_seen, last_hashed)
                VALUES(?,?,?,?,?,?,NULL,?,NULL)
                ON CONFLICT(host, root, rel, algo) DO UPDATE SET
                size=excluded.size,
                mtime=excluded.mtime,
                last_seen=excluded.last_seen,
                -- nếu metadata đổi => làm rỗng hash và last_hashed để buộc re-hash
                hash = CASE
                    WHEN filemeta_remote.size != excluded.size OR filemeta_remote.mtime != excluded.mtime
                    THEN NULL
                    ELSE filemeta_remote.hash
                END,
                last_hashed = CASE
                    WHEN filemeta_remote.size != excluded.size OR filemeta_remote.mtime != excluded.mtime
                    THEN NULL
                    ELSE filemeta_remote.last_hashed
                END
                """, (host, root, rel, size, mtime, algo, now))
        db.commit()

def _choose_remote_to_hash_budget(host, roots, algo, limit_files, limit_bytes):
    db_init()
    with sqlite3.connect(CACHE_DB) as db:
        db.row_factory = sqlite3.Row
        cur = db.cursor()
        q = """
        SELECT host, root, rel, size, mtime, last_hashed
        FROM filemeta_remote
        WHERE host=? AND algo=? AND (
            hash IS NULL OR last_hashed IS NULL OR last_hashed < mtime
        )
        """
        cur.execute(q, (host, algo))
        rows = cur.fetchall()

    # Prioritize stale first, then depend on 'mtime' or 'size'
    def sort_key(r):
        stale_rank = 0 if (r["last_hashed"] is None or (r["last_hashed"] < r["mtime"])) else 1
        secondary = r["mtime"] if REMOTE_HASH_SECONDARY == "mtime" else r["size"]
        # stale_rank ASC, secondary DESC
        return (stale_rank, -int(secondary))

    rows = sorted(rows, key=sort_key)

    selected, total_bytes = [], 0
    for r in rows:
        if limit_files and len(selected) >= limit_files:
            break
        if limit_bytes and (total_bytes + (r["size"] or 0)) > limit_bytes:
            break
        selected.append((r["root"], r["rel"], r["size"]))
        total_bytes += (r["size"] or 0)
    return selected

def hash_remote_batch(ssh_client, host, algo, entries):
    """
    entries: list of (root, rel, size)
    Run b3sum/sha256sum in batch to fill hash into DB.
    """
    if not entries:
        return 0
    algo_bin = "b3sum" if algo == "blake3" else "sha256sum"
    hashed = 0
    now = _now()
    with sqlite3.connect(CACHE_DB) as db:
        cur = db.cursor()
        # group by root to cd each root
        by_root = defaultdict(list)
        for root, rel, size in entries:
            by_root[root].append((rel, size))

        for root, rels in by_root.items():
            root_esc = root.replace("'", "'\\''")
            # build file list in shell-friendly
            # Use printf to avoid exceeding xargs limit when number is large
            rels_esc = "".join(["printf '%s\\0' " + shlex.quote(r) + " ; " for r, _ in rels])
            cmd = (
                f"cd '{root_esc}' ; "
                f"({rels_esc}) | xargs -0 -P 4 {algo_bin} 2>/dev/null"
            )
            out, err = _ssh_exec(ssh_client, cmd)
            if err.strip():
                print(f"[remote-hash:{root}] {err.strip()}")

            # parse line: "<hash> <path>"
            for line in out.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                h = parts[0]
                relp = " ".join(parts[1:]).lstrip("*").strip()
                if relp.startswith("./"): relp = relp[2:]
                relp = relp.replace("\\", "/")
                # update DB
                cur.execute("""UPDATE filemeta_remote
                               SET hash=?, last_hashed=?
                               WHERE host=? AND root=? AND rel=? AND algo=?""",
                            (h, now, host, root, relp, algo))
                hashed += 1
        db.commit()
    return hashed

# ===================== Compare & Plan =====================
def gather_local_hashes(root, algo, min_last_seen=None):
    with sqlite3.connect(CACHE_DB) as db:
        cur = db.cursor()
        if min_last_seen:
            cur.execute("""SELECT hash FROM filemeta_local
                           WHERE root=? AND algo=? AND hash IS NOT NULL AND last_seen>=?""",
                        (root, algo, min_last_seen))
        else:
            cur.execute("""SELECT hash FROM filemeta_local
                           WHERE root=? AND algo=? AND hash IS NOT NULL""", (root, algo))
        return {row[0] for row in cur.fetchall()}

def prune_deleted_records(ttl_seconds=7*24*3600):
    cutoff = _now() - ttl_seconds
    with sqlite3.connect(CACHE_DB) as db:
        db.execute("DELETE FROM filemeta_local  WHERE last_seen < ?", (cutoff,))
        db.execute("DELETE FROM filemeta_remote WHERE last_seen < ?", (cutoff,))
        db.commit()

def list_remote_hashed(host, roots, algo, min_last_seen=None):
    with sqlite3.connect(CACHE_DB) as db:
        db.row_factory = sqlite3.Row
        cur = db.cursor()
        if min_last_seen:
            q = """SELECT root, rel, size, hash FROM filemeta_remote
                   WHERE host=? AND algo=? AND hash IS NOT NULL AND last_seen>=?"""
            cur.execute(q, (host, algo, min_last_seen))
        else:
            q = """SELECT root, rel, size, hash FROM filemeta_remote
                   WHERE host=? AND algo=? AND hash IS NOT NULL"""
            cur.execute(q, (host, algo))
        rows = cur.fetchall()
    # Return map hash -> [combined_rel], same map size & origin
    aliases = _derive_aliases(roots, SERVER2_ROOT_ALIASES)
    multiroot = len(roots) > 1
    hash_to_paths = {}
    size_map = {}
    origin_map = {}
    for r in rows:
        root = r["root"]; rel = r["rel"]; size = r["size"]; h = r["hash"]
        alias = aliases[roots.index(root)]
        combined_rel = os.path.join(alias, rel).replace("\\","/") if (multiroot and USE_MERGE_SUBROOT) else rel
        hash_to_paths.setdefault(h, []).append(combined_rel)
        size_map[combined_rel] = size
        origin_map[combined_rel] = (root, alias, rel)
    return hash_to_paths, size_map, origin_map

def compute_planned(server1_root, host, roots, algo, min_last_seen=None):
    local_hashes = gather_local_hashes(server1_root, algo, min_last_seen=min_last_seen)
    hash2, remote_rel_size, rel_origin = list_remote_hashed(host, roots, algo, min_last_seen=min_last_seen)
   
    set1 = set(local_hashes)
    set2 = set(hash2.keys())
    only_in_2 = set2 - set1

    log_by_subfolder = defaultdict(lambda: {"files": 0, "bytes": 0})
    total_files = 0
    total_bytes = 0
    for h in only_in_2:
        for rel in hash2[h]:
            sz = remote_rel_size.get(rel, 0)
            subfolder = os.path.dirname(rel)
            log_by_subfolder[subfolder]["files"] += 1
            log_by_subfolder[subfolder]["bytes"] += sz
            total_files += 1
            total_bytes += sz

    planned_stats = {
        "per_subfolder_planned": log_by_subfolder,
        "planned_files": total_files,
        "planned_bytes": total_bytes
    }
    return hash2, only_in_2, remote_rel_size, rel_origin, planned_stats

# ===================== JSON LOG & Metrics =====================
def _fmt_int(n):
    try:
        return f"{int(n):,}".replace(",", ".")
    except Exception:
        return str(n)

def _fmt_bytes(n):
    try:
        n = float(n)
    except Exception:
        return str(n)
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    while n >= 1024 and i < len(units)-1:
        n /= 1024.0
        i += 1
    return f"{n:.1f} {units[i]}"

def build_merge_alert_markdown(
    source_host, roots, target_root,
    planned_files, planned_bytes,
    copied_files, copied_bytes,
    failed_files, failed_bytes,
    conflict_files, conflict_bytes,
    log_path, use_merge_subroot=None, on_conflict=None,
    run_id=None, duration_seconds=None, throughput_bps=None
):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    lines.append(f"[Merge Alert] {now}")
    if run_id:
        lines.append(f"Run ID            : {run_id}")
    lines.append(f"Source Host       : {source_host}")
    lines.append(f"Source Roots      : {', '.join(roots)}")
    lines.append(f"Target Root       : {target_root}")
    if use_merge_subroot is not None:
        lines.append(f"Use Merge Subroot : {use_merge_subroot}")
    if on_conflict is not None:
        lines.append(f"On Conflict       : {on_conflict}")
    lines.append("-" * 30)
    lines.append(f"Planned           : { _fmt_int(planned_files) } files, { _fmt_int(planned_bytes) } bytes ({ _fmt_bytes(planned_bytes) })")
    lines.append(f"Copied            : { _fmt_int(copied_files) } files, { _fmt_int(copied_bytes) } bytes ({ _fmt_bytes(copied_bytes) })")
    lines.append(f"Failed            : { _fmt_int(failed_files) } files, { _fmt_int(failed_bytes) } bytes ({ _fmt_bytes(failed_bytes) })")
    lines.append(f"Conflict          : { _fmt_int(conflict_files) } files, { _fmt_int(conflict_bytes) } bytes ({ _fmt_bytes(conflict_bytes) })")
    if duration_seconds is not None:
        lines.append(f"Duration          : {duration_seconds:.3f} s")
    if throughput_bps is not None:
        lines.append(f"Throughput        : { _fmt_int(int(throughput_bps)) } B/s ({ _fmt_bytes(throughput_bps) }/s)")
    lines.append("-" * 30)
    lines.append(f"Log JSON          : {log_path or '-'}")
    return "\n".join(lines)

def save_json_log(json_path, duplicates, folder, backup_root, moved_status_map=None,
                  rel_size_map=None, meta=None, errors_map=None):
    """
    moved_status_map[p] can be:
    - str: "copied" | "failed" | ...
    - dict: {
    "status": "...",
    "source_alias": str,
    "origin_root": str,
    "src_remote": str,
    "dest": str, # destination path (absolute or base-relative)
    "bytes": int,
    "note": str,
    "error": str
    }
    """
    log = {"hashes": []}
    if meta:
        log["meta"] = meta

    total_counts = Counter()
    total_bytes_by_status = Counter()

    for h, paths in duplicates.items():
        dup_entries = []
        for p in paths:
            # default
            status = "pending"
            entry_extra = {}

            if moved_status_map:
                raw = moved_status_map.get(p)
                if isinstance(raw, dict):
                    status = raw.get("status", "pending")
                    # Collect useful extra fields
                    for k in ("source_alias", "origin_root", "src_remote", "dest", "bytes", "note", "error"):
                        if raw.get(k) is not None:
                            entry_extra[k] = raw[k]
                elif isinstance(raw, str):
                    status = raw

            size_b = rel_size_map.get(p, 0) if rel_size_map else 0
            # If bytes is not in extra, add it
            if "bytes" not in entry_extra and size_b is not None:
                entry_extra["bytes"] = size_b

            # default dest: backup_root +p
            dest_path = os.path.join(backup_root, p).replace("\\", "/")
            if "dest" not in entry_extra:
                entry_extra["dest"] = dest_path

            dup_entries.append({
                "path": p,
                "status": status,
                **entry_extra
            })

            total_counts[status] += 1
            if size_b is not None:
                total_bytes_by_status[status] += size_b

        log["hashes"].append({
            "hash": h,
            "duplicates": dup_entries
        })

    log["summary"] = {
        "files_by_status": dict(total_counts),
        "bytes_by_status": dict(total_bytes_by_status)
    }

    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump(log, jf, indent=2, ensure_ascii=False)
    print(f"📦 The JSON log is saved at: {json_path}")

def _escape_label_value(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace("\n", "").replace('"', '\\"')

def write_prometheus_metrics(dir_path, basename, lines):
    os.makedirs(dir_path, exist_ok=True)
    tmp_path = os.path.join(dir_path, f"{basename}.prom.tmp")
    final_path = os.path.join(dir_path, f"{basename}.prom")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp_path, final_path)
    print(f"📈 Metrics Prometheus recorded at: {final_path}")

def build_metric_line(name, value, labels=None):
    label_str = ""
    if labels:
        parts = [f'{k}="{_escape_label_value(v)}"' for k, v in labels.items()]
        label_str = "{" + ",".join(parts) + "}"
    return f"{name}{label_str} {value}"

def fs_usage_metrics(path):
    total, used, free = shutil.disk_usage(path)
    return {"total": total, "used": used, "free": free}

# ===================== Conflict helpers =====================
def conflict_suffix_path(dest_local: str, ts_epoch: int | None = None) -> str:
    """
    Generate file version name based on timestamp.
    Prefer remote mtime (ts_epoch) if passed; otherwise use current time.
    For example: data.tar.gz -> data.v2025-08-10-123005.tar.gz
    If already exists, create data.v2025-08-10-123005-2.tar.gz, etc.
    """
    p = Path(dest_local)
    suffixes = "".join(p.suffixes)         # keep multiple tails
    root_name = p.name[:-len(suffixes)] if suffixes else p.name

    if ts_epoch:
        ts = datetime.fromtimestamp(ts_epoch)
    else:
        ts = datetime.now()
    tag = ts.strftime("%Y%m%d-%H%M%S")

    candidate = p.with_name(f"{root_name}.v{tag}{suffixes}")
    if not candidate.exists():
        return str(candidate)
    i = 2
    while True:
        candidate = p.with_name(f"{root_name}.v{tag}-{i}{suffixes}")
        if not candidate.exists():
            return str(candidate)
        i += 1

# ===================== Alert helpers =====================
def telegram_send_message_markdown(msg, botid, chatid):
    if not (HAVE_REQUESTS and botid and chatid):
        print("ℹ️ Ignoring sendMessage (missing requests/botid/chatid).")
        return
    msg = "```\n{}\n```".format(msg)
    msg = urllib.parse.quote_plus(msg)
    url = 'https://api.telegram.org/bot{}/sendMessage?chat_id={}&text={}&parse_mode=Markdown'.format(botid, chatid, msg)
    try:
        requests.get(url, timeout=10)
    except Exception as e:
        print(f"⚠️ Failed to send Telegram text: {e}")

def telegram_send_with_file_and_caption(bot_token, chat_id, caption_text, file_path):
    caption_md = "```\n" + str(caption_text) + "\n```"
    if len(caption_md) > 1024:
        caption_md = caption_md[:1016] + "\n```"
    if not (file_path and os.path.isfile(file_path)):
        telegram_send_message_markdown(caption_text, bot_token, chat_id)
        return
    try:
        subprocess.run([
            "curl", "-s",
            "-F", f"chat_id={chat_id}",
            "-F", f"caption={caption_md}",
            "-F", "parse_mode=Markdown",
            "-F", f"document=@{file_path}",
            f"https://api.telegram.org/bot{bot_token}/sendDocument"
        ], check=True)
    except Exception as e:
        print(f"⚠️ Telegram sendDocument failed: {e}")
        telegram_send_message_markdown(caption_text, bot_token, chat_id)

def send_email_report_with_attachment(subject, body_text, attachment_path):
    if not ENABLE_EMAIL_ALERT:
        return
    try:
        msg = MIMEMultipart()
        msg["Subject"] = EMAIL_SUBJECT_PREFIX + subject
        msg["From"] = EMAIL_FROM
        msg["To"] = ", ".join(EMAIL_TO)
        msg["Date"] = formatdate(localtime=True)

        msg.attach(MIMEText(body_text, _charset="utf-8"))

        if attachment_path and os.path.isfile(attachment_path):
            with open(attachment_path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(attachment_path)}"')
            msg.attach(part)

        context = ssl.create_default_context()
        with smtplib.SMTP(EMAIL_SMTP_HOST, EMAIL_SMTP_PORT, timeout=20) as server:
            server.starttls(context=context)
            if EMAIL_SMTP_USER:
                server.login(EMAIL_SMTP_USER, EMAIL_SMTP_PASS)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        print("📧 Email (attachment) sent.")
    except Exception as e:
        print(f"⚠️ Email sending (attachment) failed: {e}")

def build_telegram_and_email_body(run_id, res):
    return build_merge_alert_markdown(
        source_host=SERVER2_HOST,
        roots=SERVER2_ROOTS,
        target_root=SERVER1_ROOT,
        planned_files=res.get("planned_files", 0),
        planned_bytes=res.get("planned_bytes", 0),
        copied_files=res.get("copied", 0),
        copied_bytes=res.get("bytes_copied", 0),
        failed_files=res.get("failed", 0),
        failed_bytes=res.get("bytes_failed", 0),
        conflict_files=res.get("conflict", 0),
        conflict_bytes=res.get("bytes_conflict", 0),
        log_path=res.get("json_log", "-"),
        use_merge_subroot=USE_MERGE_SUBROOT,
        on_conflict=ON_CONFLICT,
        run_id=run_id,
        duration_seconds=res.get("duration_seconds"),
        throughput_bps=res.get("throughput_bytes_per_second")
    )

def send_alerts_combined(run_id, res):
    should_alert = (res.get("planned_files", 0) > 0
                    or res.get("failed", 0) > 0
                    or res.get("conflict", 0) > 0)
    if not should_alert:
        print("ℹ️ No planned/failed/conflict — skip sending alert.")
        return
    body_msg = build_telegram_and_email_body(run_id, res)
    log_path = res.get("json_log")
    # Telegram
    if ENABLE_TELEGRAM_ALERT and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        telegram_send_with_file_and_caption(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, body_msg, log_path)
    # Email
    if ENABLE_EMAIL_ALERT and EMAIL_FROM and EMAIL_TO:
        subject = f"{SERVER2_HOST} → {SERVER1_ROOT} (run {run_id})"
        send_email_report_with_attachment(subject, body_msg, log_path if (log_path and os.path.isfile(log_path)) else None)

# ===================== Merge =====================
def _derive_aliases(roots, provided):
    if provided and len(provided) == len(roots):
        return list(provided)
    aliases, used = [], set()
    for r in roots:
        a = Path(r).name or r.strip("/").replace("/", "_")
        base, i = a, 1
        while a in used:
            a = f"{base}_{i}"; i += 1
        used.add(a)
        aliases.append(a)
    return aliases

def connect_ssh(host, port, username, password=None, keyfile=None, key_passphrase=None):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kwargs = dict(
        hostname=host,
        port=port,
        username=username,
        timeout=25,
        banner_timeout=25,
        auth_timeout=25,
        look_for_keys=False,
        allow_agent=False
    )
    if keyfile:
        try:
            pkey = paramiko.RSAKey.from_private_key_file(keyfile, password=key_passphrase)
        except paramiko.PasswordRequiredException:
            print("Key requires passphrase but not provided. Enter SERVER2_KEY_PASSPHRASE.")
            raise
        connect_kwargs["pkey"] = pkey
    else:
        connect_kwargs["password"] = password
    client.connect(**connect_kwargs)
    client.get_transport().set_keepalive(30)
    return client

def confirm_and_merge(
    server1_root,
    ssh_client2,
    server2_roots,
    aliases,
    hashes2,
    only_in_2,
    remote_rel_size,
    rel_origin,
    planned_stats,
    run_id,
    auto=True
):
    """
    Copy unique files from remote -> local as planned (only_in_2).
    Support ON_CONFLICT modes: skip | overwrite | suffix | version
    - skip : skip existing files (count conflicts, do not copy)
    - overwrite : overwrite existing files (count as normal copy)
    - suffix : create version by timestamp (count conflicts)
    - version : same as 'suffix' (count conflicts)

    moved_status_map[rel_path] will be dict:
    {
    "status": "copied" | "failed" | "failed_missing_remote" |
    "skipped_conflict" | "copied_overwrite" | "conflict_versioned",
    "source_alias": <alias>,
    "origin_root": <root on remote>,
    "src_remote": <full path on remote>,
    "dest": <path relative to dest_base>,
    "bytes": <number of bytes>,
    "error": <error string if any>
    }
    """

    subroot = "merge_from_server2"
    dest_base = os.path.join(server1_root, subroot) if USE_MERGE_SUBROOT else server1_root
    logs_dir = os.path.join(server1_root, subroot if USE_MERGE_SUBROOT else "", "_logs")
    logs_dir = os.path.normpath(logs_dir)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_log_path = os.path.join(logs_dir, f"merge_{ts}.json")

    # Prepare planned for this round (only hashes in only_in_2)
    planned = {h: hashes2[h] for h in only_in_2}
    planned_files = planned_stats["planned_files"]
    planned_bytes = planned_stats["planned_bytes"]

    # Log "open loop" (no copy result yet)
    fs = fs_usage_metrics(server1_root)
    meta = {
        "started_at": datetime.now().isoformat(),
        "source_host": SERVER2_HOST,
        "source_roots": server2_roots,
        "target_root": server1_root,
        "use_merge_subroot": USE_MERGE_SUBROOT,
        "dest_base": dest_base,
        "run_id": run_id,
        "planned_files": planned_files,
        "planned_bytes": planned_bytes,
        "target_fs_total_bytes": fs["total"],
        "target_fs_used_bytes": fs["used"],
        "target_fs_free_bytes": fs["free"],
        "on_conflict": ON_CONFLICT,
    }
    save_json_log(json_log_path, planned, server1_root, dest_base, None, remote_rel_size, meta)

    if not auto:
        print(f"\n✅ Bạn có muốn tiếp tục merge {planned_files} file (~{planned_bytes} bytes) vào:")
        print(f"   {dest_base}")
        confirm = input("Nhập 'yes' để tiếp tục: ").strip().lower()
        if confirm != "yes":
            _write_metrics_end(planned_stats, 0, 0, 0, 0, 0, 0, server1_root, run_id, server2_roots, aliases)
            print("❌ Đã hủy merge.")
            return {
                "copied": 0, "failed": 0, "conflict": 0,
                "bytes_copied": 0, "bytes_failed": 0, "bytes_conflict": 0
            }

    start_time = time.time()
    moved_status_map, errors_map = {}, {}
    copied_files = copied_bytes = 0
    failed_files = failed_bytes = 0
    conflict_files = conflict_bytes = 0

    sftp2 = ssh_client2.open_sftp()
    os.makedirs(dest_base, exist_ok=True)

    # Iterate over each hash and each rel_path expected to be copied
    for h, rel_list in planned.items():
        for rel_path in rel_list:
            # rel_origin: map combined_rel -> (root, alias, orig_rel)
            src_root, alias, orig_rel = rel_origin.get(rel_path, (None, None, rel_path))
            src_remote = f"{src_root.rstrip('/')}/{orig_rel}"
            dest_rel = rel_path
            dest_local = os.path.join(dest_base, dest_rel)
            os.makedirs(os.path.dirname(dest_local), exist_ok=True)

            size = remote_rel_size.get(rel_path, 0)

            try:
                # If the file already exists locally -> process according to ON_CONFLICT
                versioned = False
                if os.path.exists(dest_local):
                    if ON_CONFLICT == "skip":
                        conflict_files += 1
                        conflict_bytes += size
                        moved_status_map[rel_path] = {
                            "status": "skipped_conflict",
                            "source_alias": alias,
                            "origin_root": src_root,
                            "src_remote": src_remote,
                            "dest": os.path.relpath(dest_local, dest_base),
                            "bytes": size,
                        }
                        print(f"⚠️  Conflict (skip): {dest_rel}")
                        continue
                    elif ON_CONFLICT == "overwrite":
                        # for overwrite – counts as normal copy
                        pass
                    else:
                        # 'suffix' or 'version' -> create version name based on timestamp
                        try:
                            st_for_name = sftp2.stat(src_remote)
                            ts_epoch = getattr(st_for_name, "st_mtime", None)
                        except Exception:
                            ts_epoch = None
                        dest_local = conflict_suffix_path(dest_local, ts_epoch=ts_epoch)
                        versioned = True

                # Verify existence on remote before get
                try:
                    sftp2.stat(src_remote)
                except IOError as e:
                    failed_files += 1
                    failed_bytes += size
                    err_msg = f"remote-missing: {e}"
                    errors_map[rel_path] = err_msg
                    moved_status_map[rel_path] = {
                        "status": "failed_missing_remote",
                        "source_alias": alias,
                        "origin_root": src_root,
                        "src_remote": src_remote,
                        "dest": os.path.relpath(dest_local, dest_base),
                        "bytes": size,
                        "error": err_msg,
                    }
                    continue

                # Make a copy
                sftp2.get(src_remote, dest_local)

                # Preserve times (best-effort)
                try:
                    st = sftp2.stat(src_remote)
                    os.utime(dest_local, (st.st_atime, st.st_mtime))
                except Exception:
                    pass

                # Assign status & add data
                if versioned:
                    conflict_files += 1
                    conflict_bytes += size
                    moved_status_map[rel_path] = {
                        "status": "conflict_versioned",
                        "source_alias": alias,
                        "origin_root": src_root,
                        "src_remote": src_remote,
                        "dest": os.path.relpath(dest_local, dest_base),
                        "bytes": size,
                        "note": "created versioned copy due to conflict"
                    }
                else:
                    copied_files += 1
                    copied_bytes += size
                    status = "copied_overwrite" if os.path.exists(os.path.join(dest_base, dest_rel)) and ON_CONFLICT == "overwrite" else "copied"
                    moved_status_map[rel_path] = {
                        "status": status,
                        "source_alias": alias,
                        "origin_root": src_root,
                        "src_remote": src_remote,
                        "dest": os.path.relpath(dest_local, dest_base),
                        "bytes": size,
                    }

                print(f"✅ Copied: {dest_rel} -> {os.path.relpath(dest_local, dest_base)}")

            except Exception as e:
                failed_files += 1
                failed_bytes += size
                err_msg = str(e)
                errors_map[rel_path] = err_msg
                moved_status_map[rel_path] = {
                    "status": "failed",
                    "source_alias": alias,
                    "origin_root": src_root,
                    "src_remote": src_remote,
                    "dest": os.path.relpath(dest_local, dest_base),
                    "bytes": size,
                    "error": err_msg,
                }
                print(f"❌ Error while copying {dest_rel}: {e}")

    try:
        sftp2.close()
    except Exception:
        pass

    duration = round(time.time() - start_time, 3)
    throughput = round((copied_bytes / duration), 3) if duration > 0 else 0.0

    # Update meta + last log
    meta.update({
        "finished_at": datetime.now().isoformat(),
        "copied_files": copied_files,
        "copied_bytes": copied_bytes,
        "failed_files": failed_files,
        "failed_bytes": failed_bytes,
        "conflict_files": conflict_files,
        "conflict_bytes": conflict_bytes,
        "duration_seconds": duration,
        "throughput_bytes_per_second": throughput
    })
    save_json_log(
        json_log_path,
        planned,
        server1_root,
        dest_base,
        moved_status_map,
        remote_rel_size,
        meta,
        errors_map
    )

    # Record cycle summary metrics
    _write_metrics_end(
        planned_stats,
        copied_files, copied_bytes,
        failed_files, failed_bytes,
        conflict_files, conflict_bytes,
        server1_root, run_id, server2_roots, aliases
    )

    # Save snapshot: set of relpaths planned in this round (helps detect changes in the next round)
    _save_snapshot(
        os.path.join(logs_dir, "_state.json"),
        set().union(*[set(hashes2[h]) for h in only_in_2]) if only_in_2 else set()
    )

    return {
        "copied": copied_files,
        "failed": failed_files,
        "conflict": conflict_files,
        "bytes_copied": copied_bytes,
        "bytes_failed": failed_bytes,
        "bytes_conflict": conflict_bytes,
        "planned_files": planned_files,
        "planned_bytes": planned_bytes,
        "json_log": json_log_path,
        "duration_seconds": duration,
        "throughput_bytes_per_second": throughput
    }

# ===================== Conflict helpers =====================
def conflict_suffix_path_legacy(dest_local: str) -> str:
    """
    Old style 'suffix': add .conflict, .conflict2, ...
    For example: report.pdf -> report.conflict.pdf -> report.conflict2.pdf, ...
    """
    p = Path(dest_local)
    base = p.stem
    suf = p.suffix
    candidate = p.with_name(f"{base}.conflict{suf}")
    if not candidate.exists():
        return str(candidate)
    i = 2
    while True:
        candidate = p.with_name(f"{base}.conflict{i}{suf}")
        if not candidate.exists():
            return str(candidate)
        i += 1

def conflict_version_path(dest_local: str, ts_epoch: int | None = None) -> str:
    """
    Timestamp 'version' type:
    Preserve multiple extensions (eg: data.tar.gz -> data.v20250810-123005.tar.gz).
    Use remote mtime if available (ts_epoch), otherwise use current time.
    If duplicate, add -2, -3, ...
    """
    p = Path(dest_local)
    suffixes = "".join(p.suffixes)         # keep multiple tails
    root_name = p.name[:-len(suffixes)] if suffixes else p.name

    ts = datetime.fromtimestamp(ts_epoch) if ts_epoch else datetime.now()
    tag = ts.strftime("%Y%m%d-%H%M%S")

    candidate = p.with_name(f"{root_name}.v{tag}{suffixes}")
    if not candidate.exists():
        return str(candidate)
    i = 2
    while True:
        candidate = p.with_name(f"{root_name}.v{tag}-{i}{suffixes}")
        if not candidate.exists():
            return str(candidate)
        i += 1

def _write_metrics_end(planned_stats, copied_files, copied_bytes, failed_files, failed_bytes, conflict_files, conflict_bytes,
                       server1_root, run_id, roots, aliases):
    fs = fs_usage_metrics(server1_root)
    metric_lines = [
        "# HELP merge_unique_files_total Number of unique files only available on server2 (planned).",
        "# TYPE merge_unique_files_total gauge",
        "# HELP merge_unique_bytes_total Total unique bytes available only on server2 (planned).",
        "# TYPE merge_unique_bytes_total gauge",
        "# HELP merge_files_copied_total Number of files copied successfully.",
        "# TYPE merge_files_copied_total counter",
        "# HELP merge_bytes_copied_total Total bytes copied successfully.",
        "# TYPE merge_bytes_copied_total counter",
        "# HELP merge_files_failed_total Number of failed copies.",
        "# TYPE merge_files_failed_total counter",
        "# HELP merge_bytes_failed_total Total bytes copied failed.",
        "#TYPE merge_bytes_failed_total counter",
        "# HELP merge_files_conflict_total Number of files in conflict.",
        "# TYPE merge_files_conflict_total counter",
        "# HELP merge_bytes_conflict_total Total bytes in conflict.",
        "# TYPE merge_bytes_conflict_total counter",
        "# HELP merge_last_run_timestamp_seconds End time.",
        "# TYPE merge_last_run_timestamp_seconds gauge",
    ]

    base_labels = {
        "source_host": SERVER2_HOST,
        "target_root": server1_root,
        "run_id": run_id,
        "source_roots": ",".join(roots),
        "source_aliases": ",".join(_derive_aliases(roots, SERVER2_ROOT_ALIASES))
    }
    metric_lines.append(build_metric_line("merge_unique_files_total", planned_stats["planned_files"], base_labels))
    metric_lines.append(build_metric_line("merge_unique_bytes_total", planned_stats["planned_bytes"], base_labels))
    metric_lines.append(build_metric_line("merge_files_copied_total", copied_files, base_labels))
    metric_lines.append(build_metric_line("merge_bytes_copied_total", copied_bytes, base_labels))
    metric_lines.append(build_metric_line("merge_files_failed_total", failed_files, base_labels))
    metric_lines.append(build_metric_line("merge_bytes_failed_total", failed_bytes, base_labels))
    metric_lines.append(build_metric_line("merge_files_conflict_total", conflict_files, base_labels))
    metric_lines.append(build_metric_line("merge_bytes_conflict_total", conflict_bytes, base_labels))
    metric_lines.append(build_metric_line("merge_last_run_timestamp_seconds", int(time.time()), base_labels))
    write_prometheus_metrics(TEXTFILE_COLLECTOR_DIR, PROM_METRIC_BASENAME, metric_lines)

# ===================== Snapshot (change detection) =====================
def _load_snapshot(state_path):
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("last_only_in_2_relpaths", []))
    except Exception:
        return set()

def _save_snapshot(state_path, relpaths_set):
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"last_only_in_2_relpaths": sorted(list(relpaths_set)),
                   "saved_at": datetime.now().isoformat()}, f)

# ===================== Scrub =====================
def _should_run_scrub(now_dt):
    if not SCRUB_ENABLED:
        return False
    return now_dt.hour == SCRUB_AT_HOUR and now_dt.minute == SCRUB_AT_MIN

def _pick_random_rows(db, table, where, limit, percent):
    sql_count = f"SELECT COUNT(*) FROM {table} {where}"
    cnt = db.execute(sql_count).fetchone()[0]
    if cnt == 0:
        return []
    if limit and limit > 0:
        n = min(limit, cnt)
    else:
        n = max(1, int(cnt * percent))
    # randomize using ORDER BY RANDOM() — ok for moderate
    sql_pick = f"SELECT rowid, * FROM {table} {where} ORDER BY RANDOM() LIMIT {n}"
    return db.execute(sql_pick).fetchall()

def run_scrub_local(root, algo):
    db_init()
    with sqlite3.connect(CACHE_DB) as db:
        rows = _pick_random_rows(db, "filemeta_local",
                                 f"WHERE root='{root}' AND algo='{algo}'",
                                 SCRUB_LIMIT_LOCAL, SCRUB_PERCENT_LOCAL)
        if not rows:
            return 0
        now = _now()
        ok = 0
        for r in rows:
            rel = r[2]  # root,rel,size,mtime...
            full = os.path.join(root, rel)
            try:
                h = compute_hash_local(full, algo)
            except Exception:
                h = None
            db.execute("""UPDATE filemeta_local SET hash=?, last_hashed=?, last_seen=?
                          WHERE root=? AND rel=? AND algo=?""",
                       (h, now if h else None, now, root, rel, algo))
            ok += 1
        db.commit()
        return ok

def run_scrub_remote(ssh_client, host, roots, algo):
    db_init()
    with sqlite3.connect(CACHE_DB) as db:
        rows = _pick_random_rows(db, "filemeta_remote",
                                 f"WHERE host='{host}' AND algo='{algo}'",
                                 SCRUB_LIMIT_REMOTE, SCRUB_PERCENT_REMOTE)
    # gom theo root
    by_root = defaultdict(list)
    for row in rows:
        # row format: rowid, host, root, rel, size, mtime, algo, hash, last_seen, last_hashed
        root = row[2]; rel = row[3]
        by_root[root].append((rel, row[4]))
    # hash batch tương tự remote batch
    total = 0
    for root, rels in by_root.items():
        entries = [(root, rel, sz) for rel, sz in rels]
        total += hash_remote_batch(ssh_client, host, algo, entries)
    return total

# ===================== One-shot =====================
def one_shot(run_id, auto_merge=True):
    # SSH
    try:
        client2 = connect_ssh(
            SERVER2_HOST, SERVER2_PORT, SERVER2_USER,
            password=SERVER2_PASSWORD, keyfile=SERVER2_KEY_FILE, key_passphrase=SERVER2_KEY_PASSPHRASE
        )
    except Exception as e:
        print(f"Unable to connect to SSH: {e}")
        return None

    # select algorithm by remote
    algo = detect_remote_hash_algo(client2)

    # refresh local cache (metadata + hash dirty)
    print(f"🔐 Shared hash algorithm: {algo}")

    # timestamp of current round
    cycle_ts = _now()

    #1) Local: scan + hash dirty (last_seen = cycle_ts)
    local_h = refresh_local_cache(SERVER1_ROOT, algo)
    if local_h:
        print(f"🏠 Local hashed/updated: {local_h} file")

    # 2) Remote: refresh metadata (last_seen = cycle_ts)
    refresh_remote_metadata(client2, SERVER2_HOST, SERVER2_ROOTS, algo)

    #3) Remote: hash by budget
    budget_list = _choose_remote_to_hash_budget(SERVER2_HOST, SERVER2_ROOTS, algo,
                                                REMOTE_HASH_BUDGET_FILES, REMOTE_HASH_BUDGET_BYTES)
    if budget_list:
        hashed_remote = hash_remote_batch(client2, SERVER2_HOST, algo, budget_list)
        if hashed_remote:
            print(f"🌐 Remote hashed this round: {hashed_remote} file")

    # 4) Planned: only use “just seen” records in this round
    hashes2, only_in_2, remote_rel_size, rel_origin, planned_stats = compute_planned(
        SERVER1_ROOT, SERVER2_HOST, SERVER2_ROOTS, algo, min_last_seen=cycle_ts
    )

    # summary of planned results
    result_summary = {
        "planned_files": planned_stats["planned_files"],
        "planned_bytes": planned_stats["planned_bytes"],
        "copied": 0, "failed": 0, "conflict": 0,
        "bytes_copied": 0, "bytes_failed": 0, "bytes_conflict": 0,
        "json_log": None,
        "only_in_2_relpaths": set().union(*[set(hashes2[h]) for h in only_in_2]) if only_in_2 else set()
    }

    if not only_in_2:
        print("✅ There is no unique (hashed) file to merge in this round.")
        try: client2.close()
        except Exception: pass
        return result_summary

    # Merge unique identified files immediately
    aliases = _derive_aliases(SERVER2_ROOTS, SERVER2_ROOT_ALIASES)
    summary = confirm_and_merge(
        SERVER1_ROOT, client2, SERVER2_ROOTS, aliases,
        hashes2, only_in_2, remote_rel_size, rel_origin, planned_stats, run_id,
        auto=auto_merge
    )

    try: client2.close()
    except Exception: pass
    summary["only_in_2_relpaths"] = result_summary["only_in_2_relpaths"]
    return summary

# ===================== Main loop =====================
def main():
    db_init()
    last_prune = 0

    if not os.path.isdir(SERVER1_ROOT):
        print(f"Destination directory (server1) does not exist: {SERVER1_ROOT}")
        sys.exit(1)

    logs_dir = os.path.join(SERVER1_ROOT, "merge_from_server2" if USE_MERGE_SUBROOT else "", "_logs")
    logs_dir = os.path.normpath(logs_dir)
    state_path = os.path.join(logs_dir, "_state.json")

    print(f"🌀 Start {'daemon' if RUN_FOREVER else 'one-shot'}… mode (Ctrl+C to stop)")
    last_scrub_day = None

    try:
        while True:
            now_dt = datetime.now()

            # prune every 1 hour
            if time.time() - last_prune > 3600:
                prune_deleted_records(ttl_seconds=7*24*3600)  # 7 days
                last_prune = time.time()

            # Scrub on schedule (once a day at the specified time/minute)
            if SCRUB_ENABLED:
                if _should_run_scrub(now_dt) and (last_scrub_day != now_dt.date()):
                    print("🧹 Start periodic SCRUB…")
                    # temporary SSH connection for scrub remote
                    try:
                        ssh = connect_ssh(SERVER2_HOST, SERVER2_PORT, SERVER2_USER,
                                          password=SERVER2_PASSWORD, keyfile=SERVER2_KEY_FILE, key_passphrase=SERVER2_KEY_PASSPHRASE)
                        algo = detect_remote_hash_algo(ssh)
                        lc = run_scrub_local(SERVER1_ROOT, algo)
                        rc = run_scrub_remote(ssh, SERVER2_HOST, SERVER2_ROOTS, algo)
                        try: ssh.close()
                        except Exception: pass
                        print(f"🧹 SCRUB done — local rehash: {lc}, remote rehash: {rc}")
                    except Exception as e:
                        print(f"⚠️ SCRUB failed: {e}")
                    last_scrub_day = now_dt.date()

            run_id = now_dt.strftime("%Y%m%d-%H%M%S")
            res = one_shot(run_id, auto_merge=True)
            if res is not None:
                prev = _load_snapshot(state_path)
                curr = res.get("only_in_2_relpaths", set())
                _save_snapshot(state_path, curr)  # always save current snapshot

                change_detected = (curr != prev) or (res.get("copied", 0) > 0 or res.get("failed", 0) > 0 or res.get("conflict", 0) > 0)

                if change_detected:
                    send_alerts_combined(run_id, res)
                else:
                    print("ℹ️ No new changes since last time — no alert sent.")

            if not RUN_FOREVER:
                break
            time.sleep(SLEEP_SECONDS)
    except KeyboardInterrupt:
        print("\n👋 Stop at user request.")

if __name__ == "__main__":
    main()