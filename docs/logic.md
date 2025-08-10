# Program lifecycle (overview)

```bash
flowchart TD
  A([Start]) --> B{SERVER1_ROOT tồn tại?}
  B -- No --> BX[Thoát với lỗi: Destination không tồn tại] --> Z([End])
  B -- Yes --> C[db_init() & chuẩn bị đường dẫn logs/_state.json]
  C --> D[In: Bắt đầu daemon hay one-shot]
  D --> E[Set last_prune=0, last_scrub_day=None]

  subgraph LOOP[Vòng lặp chính (RUN_FOREVER)]
    E --> F{Đến lúc prune? (>=1h)}
    F -- Yes --> F1[prune_deleted_records(7 ngày)] --> G
    F -- No --> G[Kiểm tra lịch SCRUB]

    G --> H{SCRUB_ENABLED & đúng giờ & chưa chạy hôm nay?}
    H -- Yes --> H1[connect_ssh() tới server2] --> H2[detect_remote_hash_algo()]
    H2 --> H3[run_scrub_local()] --> H4[run_scrub_remote()] --> H5[Đóng SSH] --> I
    H -- No --> I[Chạy one_shot(run_id)]
    
    I --> J{res is None?}
    J -- Yes --> L{RUN_FOREVER?}
    J -- No --> K[prev=_load_snapshot(); curr=res.only_in_2_relpaths; _save_snapshot(curr)]
    K --> M{Có thay đổi hoặc có copy/failed/conflict?}
    M -- Yes --> N[send_alerts_combined(run_id, res)]
    M -- No --> O[Log: No changes — skip alert]
    
    N --> L{RUN_FOREVER?}
    O --> L
    L -- No --> P[Thoát vòng lặp]
    L -- Yes --> Q[time.sleep(SLEEP_SECONDS)] --> F
  end

  P --> Z([End])
```

---

# Quy trình one\_shot() (so sánh & lập kế hoạch & merge)

```bash
flowchart LR
  subgraph ONE_SHOT[one_shot(run_id, auto_merge=True)]
    A1[connect_ssh(server2)] --> A2[detect_remote_hash_algo()]
    A2 --> A3[cycle_ts = now()]
    A3 --> A4[refresh_local_cache(SERVER1_ROOT, algo)\n→ hash file 'dirty' & cập nhật DB local]
    A4 --> A5[refresh_remote_metadata(ssh, roots, algo)\n→ chỉ metadata vào DB remote]
    A5 --> A6[_choose_remote_to_hash_budget(...)\n→ chọn danh sách theo budget]
    A6 --> A7[hash_remote_batch(...)\n→ fill hash vào DB remote]
    A7 --> A8[compute_planned(..., min_last_seen=cycle_ts)\n→ tính only_in_2 + planned_stats]

    A8 --> B{Có file unique (only_in_2)?}
    B -- No --> A9[Đóng SSH, trả về summary rỗng] --> A10([Return])
    B -- Yes --> C1[aliases = _derive_aliases()]
    C1 --> C2[confirm_and_merge(..., planned, auto=True)]
    C2 --> A9
  end
```

---

# Quy trình confirm\_and\_merge() (chi tiết copy & log & metrics)

```bash
flowchart TD
  S0[Chuẩn bị dest_base, logs_dir, json_log_path] --> S1[save_json_log() lần 1\n(meta: planned, FS usage…)]
  S1 --> S2{auto==False?}
  S2 -- Yes --> S2a[Hỏi xác nhận 'yes'] -->|không yes| S2b[Hủy & _write_metrics_end() & Return]
  S2 -- No --> S3[Open SFTP]

  S3 --> S4[Khởi tạo counters: copied/failed/conflict/bytes]
  S4 --> S5[[Lặp qua từng hash & rel_path trong planned]]

  S5 --> S6[ xác định src_remote, dest_local, size ]
  S6 --> S7{dest_local đã tồn tại?}
  S7 -- No --> S9
  S7 -- Yes --> S8{ON_CONFLICT}
  S8 -- skip --> C1[Đếm conflict & ghi moved_status 'skipped_conflict'] --> S5
  S8 -- overwrite --> S9[Tiếp tục copy (ghi đè)]
  S8 -- suffix/version --> C2[dest_local = conflict_suffix_path(...)\nđặt cờ versioned=True] --> S9

  S9[Kiểm tra tồn tại file nguồn trên remote (sftp.stat)]
  S9 -->|không tồn tại| F1[failed_missing_remote: tăng failed, ghi lỗi] --> S5
  S9 -->|tồn tại| S10[sftp.get(src_remote, dest_local)]
  S10 --> S11[Cố gắng preserve atime/mtime]
  S11 --> S12{versioned?}
  S12 -- Yes --> C3[Tăng conflict & bytes_conflict\nmoved_status='conflict_versioned'] --> S5
  S12 -- No --> C4[Tăng copied/bytes_copied\nmoved_status='copied' hoặc 'copied_overwrite'] --> S5

  S5 -->|kết thúc vòng| S13[Đóng SFTP]
  S13 --> S14[Tính duration & throughput]
  S14 --> S15[save_json_log() lần 2\n(meta cập nhật kết quả, errors_map)]
  S15 --> S16[_write_metrics_end() → ghi Prometheus .prom]
  S16 --> S17[_save_snapshot(_state.json)]
  S17 --> S18([Return summary: copied/failed/conflict/…])
```

---

## Ghi chú nhanh

* **CSDL cache**: `sqlite` lưu metadata và hash cho **local** (`filemeta_local`) và **remote** (`filemeta_remote`) để so sánh theo fingerprint (hash).
* **Budget hashing**: tránh hash toàn bộ remote mỗi vòng; chọn theo **REMOTE\_HASH\_BUDGET\_FILES/bytes** và ưu tiên theo `REMOTE_HASH_SECONDARY` (size|mtime).
* **Kế hoạch copy**: chỉ copy các file có **hash** tồn tại ở remote nhưng **chưa có** trong local (theo tập `only_in_2`).
* **Xung đột** (`ON_CONFLICT`):

  * `skip`: bỏ qua, tính conflict.
  * `overwrite`: ghi đè, tính như copy thành công.
  * `suffix/version`: đổi tên đích theo timestamp (`.vYYYYMMDD-HHMMSS`), tính conflict.
* **Logging & Alerting**:

  * JSON log vòng chạy: `SERVER1_ROOT/(merge_from_server2)/_logs/merge_*.json`
  * Metrics Prometheus: `TEXTFILE_COLLECTOR_DIR/merge_compare.prom`
  * Alert Telegram/Email: gửi khi có planned/failed/conflict.