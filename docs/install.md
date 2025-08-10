# 📦 Merge File Storage — Hướng Dẫn Cài Đặt & Quản Lý

## 1. Giới thiệu

Hướng dẫn này dùng để triển khai, cập nhật, hoặc gỡ bỏ dịch vụ **Merge File Storage** trên Linux.
Các chức năng chính:

* **Deploy**: Cài đặt và khởi tạo dịch vụ
* **Renew**: Cập nhật code và config
* **Destroy**: Xóa toàn bộ dịch vụ và dữ liệu

---

## 2. Yêu cầu hệ thống

* **Hệ điều hành**: Debian/Ubuntu (hoặc distro hỗ trợ `apt`)
* **Quyền**: Chạy với quyền `root`
* **Kết nối Internet**: Truy cập được Internet (hoặc thiết lập proxy)
* **Công cụ cần thiết**:

  * `python3-pip`
  * `python3-venv`
  * `git`

---

## 3. Triển khai (Deploy)

### Bước 1: Cài đặt gói cần thiết

Script sẽ kiểm tra và cài đặt nếu thiếu:

```bash
apt update
apt install -y python3-pip python3-venv git
```

---

### Bước 2: Tạo thư mục làm việc

```bash
mkdir -p /etc/merge_file_storage
```

---

### Bước 3: Tạo virtual environment Python

```bash
python3 -m venv /etc/merge_file_storage/venv
```

---

### Bước 4: Clone source code

```bash
git clone https://github.com/hoanghd164/merge_file_storage.git /opt/merge_file_storage
ln -s /opt/merge_file_storage /etc/merge_file_storage/source
```

---

### Bước 5: Cài đặt module Python

Tạo file `requirements.txt` tạm:

```text
tqdm
blake3
sqlite3
```

Cài đặt:

```bash
/etc/merge_file_storage/venv/bin/python -m pip install -r /etc/merge_file_storage/source/requirements.txt
rm /etc/merge_file_storage/source/requirements.txt
```

---

### Bước 6: Tạo systemd service

Tạo file `/etc/systemd/system/merge_file_storage.service`:

```ini
[Unit]
Description=Exporter Custom Metrics Service
After=network.target

[Service]
WorkingDirectory=/etc/merge_file_storage/source
ExecStart=/etc/merge_file_storage/venv/bin/python /etc/merge_file_storage/source/run.py --project <tên_project>
Restart=always
User=root
Group=root
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

---

### Bước 7: Khởi động dịch vụ

```bash
systemctl daemon-reload
systemctl enable merge_file_storage.service
systemctl restart merge_file_storage.service
systemctl is-active merge_file_storage.service
```

---

### Bước 8: Xóa cấu hình không dùng

```bash
find /etc/merge_file_storage/source/config/* -maxdepth 0 -type f -name "*.yml" \
    ! -name "<tên_project>.yml" \
    ! -name "delete.yml" \
    -exec rm -f {} +
```

---

### Bước 9: Xem log dịch vụ

```bash
journalctl -u merge_file_storage.service -n 100 --no-pager
```

---

## 4. Cập nhật code

1. **Dừng dịch vụ**

   ```bash
   systemctl stop merge_file_storage.service
   ```

2. **Cập nhật source code**

   ```bash
   cd /etc/merge_file_storage/source
   git fetch
   git reset --hard origin/main
   ```

4. **Cài lại module Python**
   (Tương tự bước Deploy)

5. **Xóa proxy tạm**

   ```bash
   unset http_proxy https_proxy
   ```

6. **Khởi động lại dịch vụ**

   ```bash
   systemctl start merge_file_storage.service
   ```

7. **Xóa config không dùng**
   (Tương tự bước Deploy)

8. **Xem log dịch vụ**

   ```bash
   journalctl -u merge_file_storage.service -n 100 --no-pager
   ```

---

## 5. Gỡ bỏ (Destroy)

1. **Dừng và vô hiệu hóa dịch vụ**

   ```bash
   systemctl stop merge_file_storage.service
   systemctl disable merge_file_storage.service
   ```

2. **Xóa toàn bộ file và thư mục**

   ```bash
   rm -rf /etc/systemd/system/merge_file_storage.service
   rm -rf /etc/merge_file_storage
   rm -rf /opt/merge_file_storage
   ```

3. **Reload systemd**

   ```bash
   systemctl daemon-reload
   ```