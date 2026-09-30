#!/bin/zsh
# 本地一键运行：GLM 配置 + 国内信源绕过代理（海外源继续走系统代理）。
#
# 用法：
#   LLM_API_KEY=xxx ./run-local.sh              # 完整流程，启用 LLM
#   ./run-local.sh --no-llm                     # 不调用 LLM
#   ./run-local.sh --hours 12
#
# 也可以把  export LLM_API_KEY=xxx  写进仓库根目录的 .env（已被 .gitignore 忽略）。
set -e
cd "$(dirname "$0")"
[[ -f .env ]] && source .env

if [[ -z "${LLM_API_KEY:-}" ]]; then
  echo "未设置 LLM_API_KEY（环境变量或 .env），将以规则模式运行。"
fi

export LLM_BASE_URL="${LLM_BASE_URL:-https://open.bigmodel.cn/api/paas/v4}"
export LLM_MODEL="${LLM_MODEL:-glm-5.3-flash}"
export LLM_BODY_EXTRA="${LLM_BODY_EXTRA:-{\"thinking\":{\"type\":\"low\"}}}"
export LLM_TRUST_ENV="${LLM_TRUST_ENV:-0}"   # GLM API 直连，不走本机代理
# 国内信源与行情接口直连；海外源（CNBC/MarketWatch/美联储）继续走系统代理
export NO_PROXY="${NO_PROXY:-cls.cn,eastmoney.com,sina.com.cn,jin10.com,10jqka.com.cn,yicai.cn,stcn.com,wallstreetcn.com,gov.cn,bigmodel.cn}"

exec .venv/bin/python -m finhot run "$@"
