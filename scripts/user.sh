#!/usr/bin/env bash
# create_single_user_with_keys.sh
# - Tạo 1 user tùy ý (nếu chưa có), setup SSH keys từ biến nội dung ngay trong script
# - Thêm vào AllowUsers và cấp sudo NOPASSWD
# - Không tạo group riêng
# Usage:
#   sudo USERNAME=nhungttc ./create_single_user_with_keys.sh
#   # hoặc sửa biến USERNAME ngay trong script

set -euo pipefail

# ====== NHẬP NỘI DUNG KEY TẠI ĐÂY ======
# Dán nguyên văn private key (bao gồm BEGIN/END) vào giữa 2 dấu EOF
id_rsa_private_key_content=$(cat <<'EOF'
-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEAuY2ERXpfftdqv8UkgX+PWNmJGetjBc83C69Q/ULJ2fcOYyvu
XtN7EAPaTysCZwp27hAxh9SdMWY0N3GdN0EKyyfd5RUpUdrwHFeZl0LbWkc2iihf
EeppT128KEB8rAR/9N1u9rAWdlji1s5mDOCCIKGP8CUKHV5jU8Peo5J/F/vHB6Hc
eeZtSvNrVgYRnTHNzpIkvfKsiUeJ7GEavt5QG3f2lD/L+zeIUj+844a4E2jtozqZ
2wyY12LivFsk/znp0LW8z7HSXKJjxGwzd77XfB5nvRmdH6ktka5oaUs8nUoWxgPs
rVuYfNsK1wCkFiGookusy4obC3eJwBYyDWG8QQIDAQABAoIBAEH4q8+cC7noUz7t
k+Yq+UdoyJMbmrBlFTglVBFHnsbNTSM7alvyqu1twT+mlgsWsGRCA6o8kMsQgH45
+eC8Ul8axIz/chp1Uitxhd0+2wiFC0IhynNvOZQLSquxCeKLEwd3d01kHAhl3/jp
l2T6qal6Z9fFA4yfk4cju9PCcUeQFQjIE0kN4zpeIwOU7nc4ZDHKPvCjtEMDgbix
XVYyAMivp9G/hi/ltgjN67xplv1RPWiFcLxScyepy/UQGnq0On5EfWe2MrEs9uRB
7ug5iY3zryBhrFQDdy/t4oB+BaeVkPu37pv2WK8mDNjpS43q3B9ob54TqRSwHF4M
8gwe/CUCgYEA4W57jh84j1sskVv6L/3opCGGVM1AFfqs9MVBSg54iIwxoMXcjERC
RARFYiS3TEKn4lPpILp+DBFdvG3n1FNxDufihfBQvVfIx+JAJT7/28/4URlDUmTm
NzjtfVDkGtRWbVI0jT8mkTF8q1PjxxgTuuVP75uCTtrIVQ34AmCp/KsCgYEA0raz
2Ky8kGEIvsbh1buk14QKA2+Y7KP8hUORWdccXe/5dL18h6aRLd07wIh0p+94YagU
whH26vjKO32BcCDAb+p+xkd1zGo/56fvC3v37elrMZFLNw5ObLZp/LmJ0LJh/qD3
U9+qsw3mt64zaep17Ymah2FeU6baz67GR+vm0sMCgYEAzhU6ToqsIiGvdJMo/Iaa
DrG3I/8e/vjS9FD/hrwD5JCFLfyzymb8TUG6TCZUixrEb1tWW90hLdcSYhf3P1uo
l3/Uza0LooyFuHVVPreBH2nYEAuQR9qFuyYHtfAlF4HWIMpt0FJS55jd56IhMPkJ
0Gmh0eHQFlZbnaXPfBzySVECgYBdvR+m/blpNXGxhUKEVdTQd5II00WhyJYXJubr
o7Gf7Jj6IS3cHvKpB6mETnAvIW5Za2/IojtJbuJwsrW5jyhs4VICnVm/VWkWgnPq
lPzH3zZrt6pRVND4tfHSlyvDJwhHQY6lxnPm8gE4p4uBy+cohDW1klBnQGxJRgQ5
jK2EBwKBgHZnF7PgM8V7ax1csPvkGvZ3QvmVPxgho9Wd8dD2M8+Qz7bh7wcbDA/L
2zGORXArJb+S9qykU+xDsOxkzTmJ29m2ijNMdB6yruueLbjpNI7hDEVs2EyIVzJc
1pXIGd1UWFG+MgmU/e1wkJGlNXN25aiJPPjnXGb+46+YuOp1nqrs
-----END RSA PRIVATE KEY-----
EOF
)

# Dán nội dung authorized_keys (public key) vào đây (mỗi key 1 dòng)
authorized_keys_content=$(cat <<'EOF'
ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC5jYRFel9+12q/xSSBf49Y2YkZ62MFzzcLr1D9QsnZ9w5jK+5e03sQA9pPKwJnCnbuEDGH1J0xZjQ3cZ03QQrLJ93lFSlR2vAcV5mXQttaRzaKKF8R6mlPXbwoQHysBH/03W72sBZ2WOLWzmYM4IIgoY/wJQodXmNTw96jkn8X+8cHodx55m1K82tWBhGdMc3OkiS98qyJR4nsYRq+3lAbd/aUP8v7N4hSP7zjhrgTaO2jOpnbDJjXYuK8WyT/OenQtbzPsdJcomPEbDN3vtd8Hme9GZ0fqS2RrmhpSzydShbGA+ytW5h82wrXAKQWIaiiS6zLihsLd4nAFjINYbxB
EOF
)
# ======================================

USERNAME="${USERNAME:-nhungttc}"

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

# 2) SSH: tạo ~/.ssh, ghi key trực tiếp từ biến
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
