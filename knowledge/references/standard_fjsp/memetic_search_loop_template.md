# 群体与 Memetic 搜索模板

来源依据：`knowledge/references/standard_fjsp/population_memetic_blueprint.md`。

```python
@dataclass(frozen=True)
class Genome:
    machine_gene: tuple[int, ...]  # 每道工序候选机器索引
    order_gene: tuple[int, ...]    # job id 重复序列，第 k 次代表第 k 道工序

def reproduce(a, b, rng):
    machines = uniform_eligible_crossover(a.machine_gene, b.machine_gene, rng)
    order = precedence_preserving_order_crossover(a.order_gene, b.order_gene, rng)
    machines = bounded_machine_mutation(machines, rng)
    order = bounded_order_window_mutation(order, rng)
    return decode_or_none(Genome(machines, order))
```

order crossover 后每个 job 出现次数必须等于其 operation 数；machine gene 永远只存候选索引。初始化混合启发式、受控随机和 incumbent，并按 assignment/order 指纹去重。

```python
population = distinct_legal_initial_population(problem, incumbent, rng)
best = incumbent.clone()
while before_deadline():
    offspring = [reproduce(select(population), select(population), rng) for _ in quota()]
    offspring = [x for x in offspring if x is not None]
    refine_selected_novel_elites(offspring, shared_deadline)
    population = diversity_aware_elitist_replacement(population, offspring)
    best = better_legal(best, min(population, key=objective))
    if structurally_stagnant(population):
        population = bounded_restart(population, keep=best)
return best
```

记录每代 unique fingerprints、交叉/变异/解码成功数、局部精修次数、重启和 best trajectory。种群大小和代数必须由每代实测成本与 deadline 共同限制。
