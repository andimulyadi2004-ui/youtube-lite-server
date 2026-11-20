#!/data/data/com.termux/files/usr/bin/bash
R="$(gh api user -q .login)/youtube-lite-server"
IDS=$(gh run list -R "$R" -w run.yml --json databaseId,status -q '.[]|select(.status=="in_progress" or .status=="queued" or .status=="waiting")|.databaseId')
if [ -z "$IDS" ]; then echo "Server cloud sudah mati."; exit 0; fi
for id in $IDS; do gh run cancel -R "$R" "$id"; done
echo "Server cloud dimatikan."
