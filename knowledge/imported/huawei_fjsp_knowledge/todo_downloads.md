# 待补充下载清单

以下材料建议后续由人工确认或下载全文/代码。当前知识库已先建立摘要卡片，但部分细节仍需要全文或本地代码验证。

## 论文

1. `An effective hybrid genetic algorithm and tabu search for flexible job shop scheduling problem`
   - 方向：HGA + TS
   - 用途：提取 FJSP 编码、交叉变异、禁忌邻域
   - 当前状态：已有公开 PDF 链接，但尚未阅读全文并提取细节。

2. `A global-local neighborhood search algorithm and tabu search for flexible job shop scheduling problem`
   - 方向：全局-局部邻域搜索 + TS
   - 用途：提取局部搜索邻域和 tabu 细节
   - 当前状态：PMC 页面可读，建议下载 PDF 或保存全文。

3. `Dual Operation Aggregation Graph Neural Networks for Solving Flexible Job-Shop Scheduling Problem with Reinforcement Learning`
   - 方向：DOAGNN + RL
   - 用途：第三阶段策略网络/动作选择研究
   - 当前状态：OpenReview/ACM 页面可读，建议下载 PDF 和代码。

4. `Job Shop Scheduling Benchmark: Environments and Instances for Learning and Optimization`
   - 方向：benchmark environment
   - 用途：设计自演进框架的评估协议
   - 当前状态：arXiv PDF 可访问，建议保存本地。

## 代码

1. `https://github.com/guillaumebour/flexible-job-shop`
   - 方向：Python GA for FJSP
   - 建议动作：克隆后跑通 Mk02 示例。

2. `https://github.com/thxiwilldoit/DOAGNN`
   - 方向：FJSP RL/GNN
   - 建议动作：克隆后只先阅读数据格式和环境，不急于训练。

3. `https://github.com/google/or-tools/blob/stable/examples/python/flexible_job_shop_sat.py`
   - 方向：CP-SAT 精确建模 baseline
   - 建议动作：若本地未安装 OR-Tools，先不要加入主依赖；可放到可选 baseline。

4. `https://github.com/mcfadd/Job_Shop_Schedule_Problem`
   - 方向：TS/GA + sequence-dependent setup
   - 建议动作：后续研究华为 setup 局部搜索时再深入。

