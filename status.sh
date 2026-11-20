#!/data/data/com.termux/files/usr/bin/bash
R="$(gh api user -q .login)/youtube-lite-server"
gh run list -R "$R" -w run.yml --limit 3
echo "URL: $(gh api "repos/$R/contents/url.txt" -q .content 2>/dev/null | base64 -d 2>/dev/null)"
