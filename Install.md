```bash
apt update
apt install -y python3-pip python3-venv

mkdir -p mkdir -p /etc/merge_file_storage
python3 -m venv /etc/merge_file_storage/venv

cat > /etc/merge_file_storage/requirements.txt << 'OEF'
tqdm
blake3
paramiko
OEF

/etc/merge_file_storage/venv/bin/python -m pip install -r /etc/merge_file_storage/requirements.txt
rm /etc/merge_file_storage/requirements.txt

mkdir /etc/merge_file_storage/source
cp ./run.py /etc/merge_file_storage/source/run.py

## Create service
cat > /etc/systemd/system/merge_file_storage.service << EOF
[Unit]
Description=Exporter Custom Metrics Service
After=network.target

[Service]
WorkingDirectory=/etc/merge_file_storage/source
ExecStart=/etc/merge_file_storage/venv/bin/python /etc/merge_file_storage/source/run.py
Restart=always
User=root
Group=root
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

## Reload and restart service
systemctl daemon-reload
systemctl enable merge_file_storage.service
systemctl restart merge_file_storage.service

## Service status
systemctl is-active merge_file_storage.service

## Service log
journalctl -u merge_file_storage.service -n 100 --no-pager -f
```