#!/bin/bash

module_req='tqdm
blake3
paramiko'

function deploy() {
  echo "=== [DEPLOY MERGE FILE STORAGE] ==="

  echo "🔌 Check internet connection..."
  if ! curl -s --head --max-time 3 https://google.com.vn | grep -q "301\|200"; then
    echo "❌ No network connection. Set up a proxy or check your network."
    return 1
  fi

  echo "🛠️  Install the required packages..."
  # List of required packages and their associated commands to verify installation
  declare -A packages=(
      ["python3-pip"]="pip3"
      ["python3-venv"]="python3"
      ["sqlite3"]="sqlite3"
      ["git"]="git"
  )

  # Array to hold packages that are not yet installed
  packages_to_install=()

  # Check each command; if not found, mark its package for installation
  for pkg in "${!packages[@]}"; do
      cmd=${packages[$pkg]}
      if ! command -v "$cmd" &> /dev/null; then
          packages_to_install+=("$pkg")
      else
          echo "$pkg is already installed."
      fi
  done

  # If any packages are missing, update once and install them
  if [ ${#packages_to_install[@]} -gt 0 ]; then
      echo "Updating package list..."
      apt update

      echo "Installing missing packages: ${packages_to_install[*]}"
      apt install -y "${packages_to_install[@]}"
  else
      echo "All required packages are already installed. Skipping installation."
  fi

  echo "📁 Create the /etc/merge_file_storage directory if it does not exist..."
  mkdir -p /etc/merge_file_storage

  if [ ! -d /etc/merge_file_storage/venv ]; then
    echo "🐍 Create the Python virtual environment..."
    python3 -m venv /etc/merge_file_storage/venv
  else
    echo "✅ Virtualenv already exists, skipping."
  fi

  if [ ! -d /opt/merge_file_storage ]; then
    echo "📥 Clone source code merge_file_storage..."
    git clone https://github.com/hoanghd164/merge-file-storage.git /opt/merge_file_storage
  else
    echo "✅ Source code is available at /opt/merge_file_storage"
  fi

  if [ ! -L /etc/merge_file_storage/source ]; then
    echo "🔗 Create symbolic link source..."
    ln -s /opt/merge_file_storage /etc/merge_file_storage/source
  else
    echo "✅ Symlink source already existsi"
  fi

  echo "📄 Create requirements.txt file..."
  echo "$module_req" > /etc/merge_file_storage/source/requirements.txt \
    && echo "✅ requirements.txt has been created." \
    || echo "❌ Error writing requirements.txt!"

  echo "📦 Install Python packages..."
  /etc/merge_file_storage/venv/bin/python -m pip install -r /etc/merge_file_storage/source/requirements.txt
  sleep 1
  rm /etc/merge_file_storage/source/requirements.txt

  echo "🛠️ Create systemd service merge_file_storage.service..."
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

  echo "🔄 Reload systemd & enable service..."
  systemctl daemon-reload
  systemctl enable merge_file_storage.service
  systemctl restart merge_file_storage.service

  echo "📌 Service status:"
  systemctl is-active merge_file_storage.service

  echo "📜 Service log:"
  journalctl -u merge_file_storage.service -n 100 --no-pager
}

function renew() {
  echo "=== [RENEW MERGE FILE STORAGE] ==="

  echo "🛑 Stop service..."
  systemctl stop merge_file_storage.service

  echo "🔌 Check network connection or use proxy..."
  if ! curl -s --head --max-time 3 https://google.com.vn | grep -q "301\|200"; then
    if [ -f ~/proxy ]; then
      echo "✅ Proxy file found, apply..."
      source ~/proxy
    else
      echo "❌ Proxy file not found!"
      return 1
    fi
  fi

  echo "🔄 Update source code..."
  cd /etc/merge_file_storage/source
  git fetch
  git reset --hard origin/main

  echo "📄 Create requirements.txt file..."
  echo "$module_req" > /etc/merge_file_storage/source/requirements.txt \
    && echo "✅ requirements.txt has been created." \
    || echo "❌ Error writing requirements.txt!"

  echo "📦 Install Python packages..."
  /etc/merge_file_storage/venv/bin/python -m pip install -r /etc/merge_file_storage/source/requirements.txt
  sleep 1
  rm /etc/merge_file_storage/source/requirements.txt

  echo "🧹 Cancel temporary proxy..."
  unset http_proxy https_proxy

  echo "🚀 Restart service..."
  systemctl start merge_file_storage.service

  echo "📌 Service status:"
  systemctl is-active merge_file_storage.service

  echo "📜 Service log:"
  journalctl -u merge_file_storage.service -n 100 --no-pager
}

function destroy() {
  echo "=== [DESTROY MERGE FILE STORAGE] ==="

  echo "🛑 Stop and disable service..."
  systemctl stop merge_file_storage.service
  systemctl disable merge_file_storage.service

  echo "🧽 Delete all files and folders related to..."
  rm -rf /etc/systemd/system/merge_file_storage.service
  rm -rf /etc/merge_file_storage
  rm -rf /opt/merge_file_storage

  echo "🔄 Reload systemd..."
  systemctl daemon-reload

  echo "✅ merge_file_storage deleted successfully"
}

case $choice in
    1) echo ""; deploy ;;
    2) echo ""; renew ;;
    3) echo ""; destroy ;;
    4) echo "👋 Exit the program."; exit 0 ;;
    *) echo "❌ Invalid option."; exit 1 ;;
esac