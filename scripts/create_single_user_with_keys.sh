#!/usr/bin/env bash
# create_single_user_with_keys.sh
# - Tạo 1 user tùy ý (nếu chưa có) sử dụng sshkey để xác thực
# - Thêm vào AllowUsers và cấp sudo NOPASSWD
# - Không tạo group riêng
# Usage:
#   sudo USERNAME=syncfile ./create_single_user_with_keys.sh
#   # hoặc sửa biến USERNAME ngay trong script

set -euo pipefail

# ====== NHẬP NỘI DUNG KEY TẠI ĐÂY ======
# Dán private key bao gồm cả BEGIN/END vào giữa 2 dấu EOF
id_rsa_private_key_content=$(cat <<'EOF'
-----BEGIN RSA PRIVATE KEY-----
-----END RSA PRIVATE KEY-----
EOF
)

# Dán nội dung authorized_keys (public key) vào đây (mỗi key 1 dòng)
authorized_keys_content=$(cat <<'EOF'
EOF
)
# ======================================

USERNAME="${USERNAME:-cephadmin}"

# 1) Tạo user nếu chưa có (không tạo group riêng)
if ! id -u "$USERNAME" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "$USERNAME"
else
  HOMEDIR="$(getent passwd "$USERNAME" | cut -d: -f6)"
  HOMEDIR="${HOMEDIR:-/home/$USERNAME}"
  mkdir -p "$HOMEDIR"
  usermod -d "$HOMEDIR" -s /bin/bash "$USERNAME" || true
fi

HOMEDIR="$(getent passwd "$USERNAME" | cut -d: -f6)"
HOMEDIR="${HOMEDIR:-/home/$USERNAME}"

# 2) SSH: tạo ~/.ssh
mkdir -p "$HOMEDIR/.ssh"
chmod 700 "$HOMEDIR/.ssh"

# Ghi private key (0600)
umask 077
printf "%s\n" "$id_rsa_private_key_content" > "$HOMEDIR/.ssh/id_rsa"
chmod 600 "$HOMEDIR/.ssh/id_rsa"

# Ghi authorized_keys (0600)
printf "%s\n" "$authorized_keys_content" > "$HOMEDIR/.ssh/authorized_keys"
chmod 600 "$HOMEDIR/.ssh/authorized_keys"

chown -R "$USERNAME:$USERNAME" "$HOMEDIR/.ssh"

# SELinux (nếu có)
if command -v restorecon >/dev/null 2>&1; then
  restorecon -R "$HOMEDIR/.ssh" || true
fi

# 3) Thêm vào AllowUsers trong sshd_config (idempotent)
SSHD_CONFIG="/etc/ssh/sshd_config"
if ! grep -qE '^\s*AllowUsers\b' "$SSHD_CONFIG"; then
  cp -a "$SSHD_CONFIG" "${SSHD_CONFIG}.bak.$(date +%s)"
  echo "AllowUsers $USERNAME" >> "$SSHD_CONFIG"
elif ! grep -qE "\b${USERNAME}\b" "$SSHD_CONFIG"; then
  cp -a "$SSHD_CONFIG" "${SSHD_CONFIG}.bak.$(date +%s)"
  sed -i "/^\s*AllowUsers\b/ s/\$/ ${USERNAME}/" "$SSHD_CONFIG"
fi

# 4) Sudo không cần mật khẩu (dùng /etc/sudoers.d an toàn)
echo "$USERNAME ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/99-${USERNAME}"
chmod 440 "/etc/sudoers.d/99-${USERNAME}"
visudo -cf /etc/sudoers >/dev/null

# 5) Restart sshd
if systemctl list-unit-files | grep -q '^sshd\.service'; then
  systemctl restart sshd
elif systemctl list-unit-files | grep -q '^ssh\.service'; then
  systemctl restart ssh
else
  service sshd restart 2>/dev/null || service ssh restart 2>/dev/null || true
fi

echo "✅ Done. User '$USERNAME' đã được tạo/cấu hình với SSH key và sudo NOPASSWD."