# SalesPilot — 待办记录

## Step 5 之后做

### RAG 检索 eval
写几条"问题 → 期望文件名"配对，自动验检索准确性：
- "What's the contract discount for Acme Corp?" → acme_corp_msa.md
- "What are Cogsworth's payment terms?" → cogsworth_co_msa.md
- "What is Acme's contract price for PX-1000?" → acme_corp_msa.md

程序自动跑，核对"捞回来的第一块文件名 = 期望文件名"，对了打勾，错了报警。
Step 5 后可扩展成验最终答案里的数字（"12%"有没有出现在答案里）。
补 eval harness 求职短板，和 Upwork Claude Code 岗位要求直接对应。

### 产品名美化
seed_data.py 用 Faker 生成的产品名（"Wait 360 653"、"Some Plus 294"）太随机。
录 demo 视频前换成像样的名字。
