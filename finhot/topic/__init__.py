"""题材档案：陌生题材的认知加速器。

一个题材 = 知识层（topics/<名称>.yaml，人工校对）+ 数据层（问财 + FinHOT 本地数据，每次现取）
→ reports/topics/<slug>.md 档案卡。命令见 `python -m finhot topic -h`。

  kb.py      读知识层
  local.py   FinHOT 存下的快讯、涨停、板块
  fetch.py   问财：成分股、行情、基本面、阶段指标、新闻研报公告
  stage.py   阶段判断（启动 / 发酵 / 主升 / 分歧 / 退潮 / 蛰伏）
  render.py  档案卡 Markdown
  build.py   串起来，存快照
  draft.py   模型起草新题材的知识层
"""
