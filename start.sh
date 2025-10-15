#!/bin/bash
# 快速启动脚本

set -e

echo "🚀 启动 Abacus Ratings (Lecture Presentation Rating System)..."

# 检查虚拟环境
if [ ! -d ".venv" ]; then
    echo "📦 创建虚拟环境..."
    python3 -m venv .venv
fi

# 激活虚拟环境
source .venv/bin/activate

# 安装依赖
echo "📥 安装依赖..."
pip install -q -r requirements.txt

# 获取本机IP
echo ""
echo "📡 本机IP地址："
ifconfig | grep "inet " | grep -v 127.0.0.1 | awk '{print "   http://"$2":8000"}'

echo ""
# Load .env if present
if [ -f ".env" ]; then
    echo "🔐 加载 .env 环境变量"
    set -a
    . ./.env
    set +a
fi

# Env checks
if [ -z "$ADMIN_KEY" ]; then
    echo "❌ 缺少必要环境变量: ADMIN_KEY"
    echo "请先设置，例如:"
    echo "  export ADMIN_KEY=\"change-me\""
    echo "或创建 .env 文件:"
    echo "  echo ADMIN_KEY=\"change-me\" > .env"
    exit 1
fi

# 如果未设置 SESSION_SECRET，则为本地开发自动生成一个临时值（生产仍应显式设置）
if [ -z "$SESSION_SECRET" ]; then
    export SESSION_SECRET="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
    echo "ℹ️ 未检测到 SESSION_SECRET，已为本地开发自动生成临时密钥。"
fi

echo "✅ 服务启动中..."
echo "   学生访问: http://你的IP:8000/"
echo "   管理面板: http://你的IP:8000/admin  (首次访问需输入密钥)"
echo ""
echo "按 Ctrl+C 停止服务"
echo ""

# 启动服务
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload