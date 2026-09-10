# 解析与状态

## 1. 冻结口径，避免把旧文档冲突带入新实现

当前固定评价器决定接受口径；用户要求有限批、硬 Q-time、精确族匹配和严格词典序不得被旧项目的 relaxed 配置覆盖。若固定评价器与这些明确要求直接冲突，记录具体字段/规则冲突，不能悄悄改题。

| 对象 | 独立内部表示 | 容易误读的地方 |
|---|---|---|
| 时间 | 整数 tick 或评价器规定精度的精确数 | `H` 是绝对截止值，不是 `current_time + H`；日历转换 `datetime(t)=current_datetime+(t-current_time)` |
| 路线 | `routes[j][r] = ordered operations + Q edges` | 每任务只选一条；保留其它路线供回退，不混搭两条路线的工序 |
| 工序 | 唯一键 `(job,route,seq)` | 同序号不同路线/任务是不同对象；数字序号按数值顺序而非字符串顺序，显式工艺顺序则依 IO |
| 候选设备 | `options[op][m]={duration,batch,capacity,priority}` | 只允许该路线工序的候选；加工时长随机器变；批属性是否允许候选级覆盖须核对评价器 |
| 跨厂 | `allowed_out[op]` 有向厂区对集合 | 前工序控制离开它时的许可，同厂可行；许可不是运输时间表 |
| 运输 | `transport[from_m][to_m]` | 有向；同厂不同机器也可能非零；不能擅自对称化 |
| Q-time | `(a,anchor_a,b,anchor_b,l,u)` | 两锚点各为 start/end，任意前后非相邻工序；null 表示无该侧界，0 是有效界 |
| 普通换型 | `setup[proc_u][proc_v]` | 有向且依真实紧邻工序身份；族相同不能直接断言 setup=0 |
| 有限批 | 精确 family tuple，成员数，容量，公共 S/E | `|B| <= min(cap_i)`；不是无限容量，也不是 `sum(1/cap_i)<=1` 的另一种模型 |
| 日历 | 排序、合并后的不可用区间 | 通常加工使用 `[S,E)`；是否允许边界容差、setup 是否也必须避开日历，明确写入台账 |

旧需求文档含模糊 batch family 匹配及空间比例措辞，旧有限批评价器实际使用**有序字符串元组完全相等**与**成员数不超过最小容量**。本指南采用后者，不能混用。旧实现有维修结束前容许一分钟启动的口径；新任务只有固定评价器明确允许时才使用相同容差，默认示例均严格半开日历。旧输入解析还把维修结束点按闭区间处理，加一个 tick 转成半开区间；新 IO 若原本就是半开区间，不能再加一，也不能照搬历史 current_time/维修平移开关。

旧普通机评价器要求 `S_v >= E_u + setup(u,v)`，仅检查加工区间避开维修，并未检查整个换型段是否避开维修。本指南的基础公式对应这种“时间间隔换型”口径。若当前要求换型是独立占机活动，需要另外安置换型段，见合法构造文档，不能只留一个数值间隔。

## 2. 嵌套表解析

旧 IO 的 setup 是字典的字典，不是记录列表：

```json
{"setup":{"('A', 'r', '1')":{"('B', 'r', '2')":7}},
 "transition":{"M1":{"M2":4}}}
```

可将 IO 工序键作为 opaque 字符串保存，并由当前 IO 的统一编码函数产生查询键。若转换成 tuple，可用安全的字面量解析并严格验证三个字符串，禁止 eval。不要随意去掉空格再与未规范化键查询；不可把机器 ID 误用为 setup 行键。

小型解析内核（缺项为零只用于当前已核对采用该规则的 IO）：

```python
def read_nested_times(raw):
    result = {}
    for src, row in raw.items():
        if not isinstance(row, dict):
            raise ValueError("expected nested time table")
        result[str(src)] = {}
        for dst, value in row.items():
            number = int(value)
            if number != float(value) or number < 0:
                raise ValueError("time must be a nonnegative integer tick")
            result[str(src)][str(dst)] = number
    return result

def setup_time(table, left, right):
    return 0 if left is None else table.get(left, {}).get(right, 0)
```

不把无效容量统一钳成 1、浮点时间统一截断或未知锚点统一当 end；这些是旧实现的宽松解析行为，不能成为新 schema 的默认规则。空 family 是否允许相互成批按评价器处理；精确 tuple 模型中两个空 tuple 相等。

## 3. 静态预处理

检查 route 的每个 Q 端点存在、锚点枚举合法、每道工序候选非空、候选机器存在、duration 正、batch capacity 为合法整数、`l<=u`。维护每个工序所有**入边和出边** Q 索引，而不只存相邻约束。

按机器资格建立路线分层图：第 i 层是工序 i 的可选机器；边 `m->n` 当且仅当同厂或 `(factory(m),factory(n)) in allowed_out[op_i]`。从末层反向计算可达机器，再正向删除不可达起点的机器。空层可证明这条路线跨厂不可行；只查下一道是否有机器会漏掉更深的死路。运输时间仍另外计入时间约束。

路线排名可用最短加工总时长、普通机工作量和预计运输负载。它们是排序代理，不是可行性证明。旧路线得分把所有最小 Q 间隔直接加起来，对非相邻/嵌套约束可能重复计算；不能当严格尾部下界。安全的粗下界可只用最短加工时长之和；更紧的下界从时间约束图计算。

## 4. 可变状态与不变量

最容易正确实现的初版使用深拷贝快照，后续才换撤销日志。

```text
State:
  chosen_route[j]                         # 未选或一条确定路线
  records[(j,r,seq)] = {m,S,E,batch_id}    # 只存已安置工序
  next_index[j], job_status[j]            # 已排连续前缀，或已完成
  timeline[m] = sorted activity blocks    # 普通工序块或原子的有限批块
  batches[id] = {m,members,family,S,E,D,cap}
  time_domains[event] = [LB,UB]           # start 和 end 分别存
  selected_machine_domains[op]           # 若进行了资格传播
  ready_set, completion[j], setup_count, completed_weight
  version, candidate_cache               # 缓存与状态版本绑定
  next_batch_id, tie_break_rng_state     # 可重复恢复所需状态
```

`timeline[m]` 应包含该物理机器全部占用。若允许同机普通/批混用，不能分别验证两个列表后声称资源合法；计分所用换型序列与物理占用序列也可能不同，需分别按评价器定义维护。

每批只占一个机器块；成员输出记录可多条但必须指向同一批且 S/E 完全相同。`D=max p_i(m)`，`cap=min cap_i(m)`。后续边使用共同 E，不使用某成员较短的单件完成时间。

状态必须满足：工序记录唯一；records/timeline/batches 双向一致；next_index 对应真实前缀；所有已知端点的硬边成立；没有批成员“幽灵占用”；计分可从原始记录重新计算。搜索栈、已尝试分支和总预算计数放在 State 外，避免回滚后把“已失败”也忘掉而无限循环。最佳完整合法解 incumbent 也独立保存，不让试探覆盖它。
