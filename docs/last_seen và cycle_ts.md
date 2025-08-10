Tìm hiều về 2 “mốc thời gian” này là để hiểu vì sao tool không còn planned ảo nữa:

# last\_seen là gì?

* Trường trong DB (cho **mỗi file**) lưu **lần gần nhất tool “thấy” file đó trong vòng quét hiện tại**.
* Được **cập nhật** ở:

  * `refresh_local_cache()` (local): nếu file còn trên đĩa → `last_seen = now`.
  * `refresh_remote_metadata()` (remote): nếu `find` còn liệt kê → `last_seen = now`.
* **Không** cập nhật nếu file **không còn tồn tại** (đã xóa, mất quyền, v.v.).

Hệ quả:

* File nào **vẫn tồn tại** ở vòng này ⇒ `last_seen` mới tinh.
* File bị xóa/mất ở vòng này ⇒ `last_seen` **không đổi** (vẫn là mốc cũ) ⇒ tự động bị “lọc ra” khi tính planned.
* Sau một thời gian (TTL), `prune_deleted_records()` sẽ xóa hẳn các bản ghi có `last_seen` quá cũ.

# cycle\_ts là gì?

* Biến “**mốc vòng**” lấy tại **đầu vòng one\_shot**: `cycle_ts = _now()`.
* Khi tính planned, code **lọc theo `last_seen >= cycle_ts`** để chỉ dùng các bản ghi **vừa được thấy trong đúng vòng này** (tránh lẫn dữ liệu cũ).

# Tại sao cần cả hai?

* `last_seen` là dấu “file này **được thấy** ở vòng này”.
* `cycle_ts` là ranh giới “**đúng vòng** hiện tại”.
* Ghép lại: chỉ tính những file có `last_seen >= cycle_ts` ⇒ so sánh local vs remote **đồng bộ theo cùng một vòng**, không bị dữ liệu từ lần quét trước chen vào.

---

## Nhìn nhanh bằng timeline

```
T0: cycle_ts = 100
S7: refresh_local_cache()      → file còn trên local   ⇒ last_seen = 100
S8: refresh_remote_metadata()  → file còn trên remote  ⇒ last_seen = 100
S9–S10: hash remote (một phần theo budget)
S11: compute_planned(min_last_seen=100)
     - chỉ lấy các record có last_seen >= 100
```

### Ví dụ 1 — Xóa file ở remote

* Trước vòng này DB còn record `X` (từ quá khứ), `last_seen = 50`.
* Vòng này remote **đã xóa `X`** ⇒ S8 **không** update `last_seen` cho `X`.
* S11 lọc `last_seen >= 100` ⇒ `X` **bị loại**, không còn “planned ảo”.
* Sau TTL, `X` bị `prune` khỏi DB.

### Ví dụ 2 — Xóa file ở local (tự phục hồi)

* `Y` bị xóa ở local trước vòng này (DB cũ `last_seen = 60`), remote còn `Y`.
* Vòng này:

  * S7: local **không còn `Y`** ⇒ **không** update `last_seen` (vẫn 60) ⇒ hash local của `Y` **không** được tính.
  * S8: remote **có `Y`** ⇒ `last_seen = 100`; nếu hash đã có/hoặc vừa hash ở S10, record remote đủ điều kiện.
* S11: so sánh trên tập “vừa thấy” → hash `Y` **chỉ có ở remote** ⇒ `Y` vào planned ⇒ copy về local.

### Ví dụ 3 — File mới ở remote nhưng **chưa kịp hash**

* S8 đã thấy file mới `Z` ⇒ `last_seen = 100`, nhưng `hash = NULL` (vì budget).
* S11 chỉ lấy **remote có hash** và `last_seen >= 100` ⇒ `Z` **chưa** vào planned.
* Vòng sau `Z` được hash ở S10 ⇒ vào planned và copy.

---

## So sánh nhanh với các mốc khác

* `mtime`: thời gian chỉnh sửa file (từ hệ thống file). Dùng để phát hiện “dirty” & reset `hash=NULL` khi remote đổi.
* `last_hashed`: lần **băm** gần nhất. Dùng cho SCRUB & biết hash đã “tươi” hay chưa.
* `last_seen`: lần **được thấy** trong vòng quét (quan trọng để lọc snapshot theo vòng).
* `cycle_ts`: mốc thời gian **của vòng hiện tại** (để lọc đồng nhất 2 phía).

---

## Tóm tắt

* Mỗi vòng: đặt `cycle_ts` → quét local/remote → cập nhật `last_seen` cho những file **thực sự còn tồn tại**.
* Tính planned chỉ dựa trên **các record vừa thấy** (`last_seen >= cycle_ts`) ⇒

  * Không planned nhầm file đã xóa/mất.
  * Không dùng hash quá khứ của thứ **vòng này chưa thấy**.
  * Kết quả ổn định, an toàn với trường hợp budget hash, remote thay đổi, hoặc file biến mất.