#!/usr/bin/env bash
# 提交托管步骤（仅提交生成的产物）
# 放在单独脚本里，便于本地复现调试
set -e

git config user.name  "github-actions[bot]"
git config user.email "github-actions[bot]@users.noreply.github.com"

# 1) 全量 add 已跟踪变更 + 新增文件；忽略被 .gitignore 排除的项（不致命）
git add -A

# 2) 无变更则跳过 commit（避免 "nothing to commit" 报 fatal）
if git diff --cached --quiet; then
  echo "::notice::无新变更, 跳过 commit"
  exit 0
fi

git commit -m "chore(sync): 采集资源 [skip ci]"

# 有 remote 才 push（本地无 remote / 孤儿仓自动跳过，不致命）
if git remote get-url origin >/dev/null 2>&1; then
  # --set-upstream 兼容 detached HEAD / 新分支（actions/checkout 常见）
  BRANCH=$(git rev-parse --abbrev-ref HEAD)
  if [ "$BRANCH" = "HEAD" ]; then
    git push origin HEAD:main
  else
    git push --set-upstream origin "$BRANCH"
  fi
else
  echo "::notice::未检测到 origin remote, 跳过 push"
fi
