#!/data/data/com.termux/files/usr/bin/bash
R="$(gh api user -q .login)/youtube-lite-server"
RUN=$(gh run list -R "$R" -w run.yml --json status -q '.[]|select(.status=="in_progress" or .status=="queued")|.status' | head -1)
getu(){ gh api "repos/$R/contents/url.txt" -q .content 2>/dev/null | base64 -d 2>/dev/null | tr -d '\n'; }
if [ -n "$RUN" ]; then echo "Server cloud SUDAH nyala: $(getu)"; exit 0; fi
OLD=$(getu)
gh workflow run run.yml -R "$R" -f n=1 || exit 1
echo "Menyalakan server di GitHub (tunggu ±1 menit)..."
for i in $(seq 1 60); do
  sleep 5
  NEW=$(getu)
  if [ -n "$NEW" ] && [ "$NEW" != "$OLD" ]; then
    echo "SERVER HIDUP: $NEW"
    echo "Aktif ±180 jam. Matikan: yt-off"
    exit 0
  fi
done
echo "Belum muncul, cek: yt-status"
