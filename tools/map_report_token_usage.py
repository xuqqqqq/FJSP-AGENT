"""Join the published report rows to existing, read-only token audit evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
FAMILIES = {
    "标准": "standard_fjsp",
    "SDST": "fjsp_sdst",
    "多目标负荷": "fjsp_multiobjective_workload",
    "单元运输组合": "fjsp_cell_sdst_transport_tardiness",
    "释放时间": "fjsp_release_time",
    "最大间隔": "fjsp_max_time_lag",
    "最小间隔": "fjsp_min_time_lag",
    "可重入": "fjsp_reentrant",
    "替代路径": "fjsp_alternative_path",
    "设备维修": "fjsp_machine_availability",
    "工件优先级": "fjsp_priority",
    "并行组批": "fjsp_pbpm",
    "跨厂转运": "fjsp_distributed_transfer",
}
# Industrial runs use a different three-total-round runner and have no standard
# comparison JSON. These paths are identified in the report's source ledger.
INDUSTRIAL = {
    "90 mixed": "outputs/contract_industrial_small90_zero_start_20260907",
    "90 random": "outputs/izs0908/small_90_random_seed20260717",
    "80 mixed": "outputs/izs0908/small_80_mixed_seed20260716",
    "90 due": "outputs/izs0908/small_90_due_seed20260716",
    "300 mixed": "outputs/iz300b",
    "500 mixed": "outputs/iz500b",
    "800 mixed": "outputs/iz800b",
}
# The ledger explicitly excludes earlier versions that happened to reach the
# same objective while violating repair/lane budgets.
REPORT_VARIANTS = {
    ("最大间隔", "seti5xx"): "/full_v4_qiming/",
    ("最小间隔", "seti5x"): "/full_v3_qiming/",
}
GAP_NAMES = {
    "db_assistant_message_without_usage": "数据库有未记录用量的消息",
    "step_start_without_metered_finish": "部分模型步骤缺少结束用量",
    "worker_session_not_completed": "部分Worker会话未正常结束",
    "worker_attempt_without_proven_complete_usage": "部分Worker调用用量覆盖不完整",
    "provider_error_may_have_unreported_usage": "服务错误请求可能缺少用量",
    "empty_or_unmetered_invocation": "部分调用未留存用量",
    "empty_usage_artifact": "部分用量文件为空",
    "provider_total_not_recorded": "服务未返回total",
    "reflection_origin_not_proven": "部分反思调用来源未确认",
    "candidate_aborted_without_proven_usage_coverage": "中断候选的用量覆盖不完整",
    "aborted_stage_without_proven_usage_coverage": "中断阶段的用量覆盖不完整",
    "no_llm_evidence_files": "未找到模型用量日志",
}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def path_key(value):
    return str(value).replace("\\", "/").casefold()


def report_tables(path):
    with ZipFile(path) as archive:
        tree = ET.fromstring(archive.read("word/document.xml"))
    return [
        [["".join(node.text or "" for node in cell.findall(".//w:t", NS)).strip()
          for cell in row.findall("w:tc", NS)]
         for row in table.findall("w:tr", NS)]
        for table in tree.findall("w:body/w:tbl", NS)
    ]


def instance_matches(short_name, filename):
    name = short_name.lower()
    if name.startswith("dp"):
        name = name[2:]
    return name in re.split(r"[._/\\-]+", filename.lower())


def close(a, b):
    return isinstance(a, (int, float)) and abs(float(a) - float(b)) < 0.005


def industrial_matches(display, manifest):
    if "无合法解" in display:
        return not manifest.get("has_accepted_solver") and not manifest.get("final_key")
    expected = [float(s) for s in display.replace("／", "/").split("/")]
    actual = manifest.get("final_key") or []
    return len(actual) >= 2 and close(actual[0], expected[0]) and close(-actual[1], expected[1])


def write_csv(path, rows):
    if not rows:
        return
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("C:/Users/ASUS/Downloads/FJSP算法自演进框架测试报告_v2.docx"))
    parser.add_argument("--audit-dir", type=Path, default=ROOT / "exports/none_full_token_usage_20261021")
    parser.add_argument("--output", type=Path, default=ROOT / "exports/report_token_usage_20261021")
    args = parser.parse_args()
    audit = read_json(args.audit_dir / "audit.json")
    runs = {r["audit_run_id"]: r for r in audit["runs"]}
    by_path = {path_key(r["manifest"]): r for r in runs.values()}
    manifests = {uid: read_json(ROOT / r["manifest"]) for uid, r in runs.items()}
    tables = report_tables(args.report)
    main_table = next(t for t in tables if t[0] == ["问题族", "算例", "None", "Full", "质量提升"])
    industrial_table = next(t for t in tables if t[0][0] == "算例" and "产量" in t[0][1])
    selected, details, alternatives = [], [], []

    def add_row(row, full, none, source, aliases=()):
        row.update({"source_comparison": source, "source_pair_ids": ";".join(aliases)})
        for mode, run in (("none", none), ("full", full)):
            uid = run["audit_run_id"]
            row[mode + "_run_id"] = uid
            row[mode + "_recorded_tokens"] = run["observed"]["total_tokens"]
            row[mode + "_exact_tokens"] = run["exact_job_total_tokens"]
            row[mode + "_coverage"] = "完整" if run["exact_job_total_tokens"] is not None else "仅已记录小计"
            row[mode + "_manifest"] = run["manifest"]
            row[mode + "_calls"] = run["llm_call_count_observed"]
            row[mode + "_rounds"] = run["completed_rounds"]
            row[mode + "_gaps"] = "；".join(sorted({GAP_NAMES.get(g["kind"], g["kind"]) for g in run["gaps"]}))
            details.append({
                "报告行": row["report_row"], "报告表": row["report_table"], "问题族": row["family_label"],
                "报告算例": row["instance_label"], "实际输入文件": run["instance"], "模式": mode,
                "统计运行ID": uid, "完成轮次": run["completed_rounds"],
                "已记录Token小计": row[mode + "_recorded_tokens"], "完整总Token": row[mode + "_exact_tokens"],
                "记录覆盖": row[mode + "_coverage"], "有用量的消息数": run["llm_call_count_observed"],
                **{role + "_tokens": run["role_usage"][role]["total_tokens"]
                   for role in ("main", "coding_worker", "semantic_review", "reflection", "subagent", "other")},
                "缺口说明": row[mode + "_gaps"], "原始运行清单": run["manifest"],
                "原始清单SHA256": hashlib.sha256((ROOT / run["manifest"]).read_bytes()).hexdigest(),
            })
        row["both_exact"] = all(row[m + "_exact_tokens"] is not None for m in ("none", "full"))
        selected.append(row)

    for index, values in enumerate(main_table[1:], 1):
        family, instance, none_result, full_result, gain = values
        candidates = []
        seen = set()
        for pair in audit["pairs"]:
            if pair["family"] != FAMILIES[family] or not pair["protocol_valid"]:
                continue
            f, n = pair["full_run"], pair["none_run"]
            required_variant = REPORT_VARIANTS.get((family, instance))
            if required_variant and required_variant not in runs[f]["manifest"]:
                continue
            if not instance_matches(instance, runs[f]["instance"]):
                continue
            if not close(-(manifests[f].get("final_key") or [float("inf")])[0], full_result):
                continue
            if not close(-(manifests[n].get("final_key") or [float("inf")])[0], none_result):
                continue
            if (f, n) not in seen:
                candidates.append(pair)
                seen.add((f, n))
        if len(candidates) != 1:
            raise ValueError(f"Report row {index}: expected exactly one matching version, got {[(p['full_run'],p['none_run']) for p in candidates]}: {values}")
        pair = candidates[0]
        aliases = [p["pair_id"] for p in audit["pairs"] if (p["full_run"], p["none_run"]) == (pair["full_run"], pair["none_run"])]
        source = pair["source_comparison"]
        add_row({"report_row": f"T6-{index:02d}", "report_table": "表6", "family_label": family,
                 "instance_label": instance, "family": FAMILIES[family], "none_result": none_result,
                 "full_result": full_result, "quality_gain": gain, "note": "",
                 "instance_file": runs[pair["full_run"]]["instance"]},
                runs[pair["full_run"]], runs[pair["none_run"]], source, aliases)

    for index, values in enumerate(industrial_table[1:], 1):
        instance, none_result, full_result, _ = values
        base = INDUSTRIAL[instance]
        f = by_path[path_key(base + "/full/manifest.json")]
        n = by_path[path_key(base + "/none/manifest.json")]
        if not industrial_matches(full_result, manifests[f["audit_run_id"]]) or not industrial_matches(none_result, manifests[n["audit_run_id"]]):
            raise ValueError(f"Industrial result does not match report: {values}")
        note = ("报告行名为90 mixed；对应源台账及输入实际为small_90_random_seed20260716.json。按228.80/89与757.43/126定位，未改动报告。"
                if instance == "90 mixed" else "")
        add_row({"report_row": f"T8-{index:02d}", "report_table": "表8", "family_label": "工业多特性",
                 "instance_label": instance, "family": "industrial_fjsp", "none_result": none_result,
                 "full_result": full_result, "quality_gain": "", "note": note,
                 "instance_file": f["instance"]}, f, n, "docs/contract_acceptance_test_ledger.md")

    selected_ids = {r[m + "_run_id"] for r in selected for m in ("none", "full")}
    # Do not add earlier repetitions, baseline generation, or the expanded-budget
    # industrial arm to the report's 46 paired rows.
    for run in runs.values():
        if run["audit_run_id"] not in selected_ids:
            alternatives.append({"统计运行ID": run["audit_run_id"], "问题族": run["family"],
                                 "实际输入文件": run["instance"], "模式": run["mode"],
                                 "运行用途": run["run_purpose"], "已记录Token小计": run["observed"]["total_tokens"],
                                 "完整总Token": run["exact_job_total_tokens"], "原始运行清单": run["manifest"]})

    summary = []
    for family in dict.fromkeys(row["family_label"] for row in selected):
        group = [row for row in selected if row["family_label"] == family]
        summary.append({"问题族": family, "报告配对组数": len(group),
                        **{f"{mode}已记录Token小计": sum(row[f"{mode}_recorded_tokens"] or 0 for row in group) for mode in ("none", "full")},
                        **{f"{mode}完整运行数": sum(row[f"{mode}_exact_tokens"] is not None for row in group) for mode in ("none", "full")},
                        "双方均完整的组数": sum(row["both_exact"] for row in group)})
    if len(selected_ids) != len(details) or len(selected) != 46:
        raise ValueError("Report coverage must consist of 46 different pairs and 92 different runs")
    supplementary, supplement_details = [], []
    for label, base, section in (("20 mixed", "outputs/if20a", "4.6节20任务补充实验"),
                                 ("800 mixed 扩大预算", "outputs/if800a", "4.5/4.6节扩大实现预算实验")):
        run = by_path[path_key(base + "/full/manifest.json")]
        uid = run["audit_run_id"]
        supplementary.append({"报告位置": section, "算例": label, "模式": "full", "统计运行ID": uid,
                              "完成轮次": run["completed_rounds"], "已记录Token小计": run["observed"]["total_tokens"],
                              "完整总Token": run["exact_job_total_tokens"],
                              "记录覆盖": "完整" if run["exact_job_total_tokens"] is not None else "仅已记录小计",
                              "原始运行清单": run["manifest"]})
        supplement_details.append({
            "报告行": "SUP-" + str(len(supplementary)), "报告表": "正文补充", "问题族": "工业Full单臂",
            "报告算例": label, "实际输入文件": run["instance"], "模式": "full", "统计运行ID": uid,
            "完成轮次": run["completed_rounds"], "已记录Token小计": run["observed"]["total_tokens"],
            "完整总Token": run["exact_job_total_tokens"], "记录覆盖": supplementary[-1]["记录覆盖"],
            "有用量的消息数": run["llm_call_count_observed"],
            **{role + "_tokens": run["role_usage"][role]["total_tokens"]
               for role in ("main", "coding_worker", "semantic_review", "reflection", "subagent", "other")},
            "缺口说明": "；".join(sorted({GAP_NAMES.get(g["kind"], g["kind"]) for g in run["gaps"]})),
            "原始运行清单": run["manifest"],
            "原始清单SHA256": hashlib.sha256((ROOT / run["manifest"]).read_bytes()).hexdigest(),
        })
    supplementary_ids = {r["统计运行ID"] for r in supplementary}
    alternatives = [r for r in alternatives if r["统计运行ID"] not in supplementary_ids]
    manifest = {"report_path": str(args.report), "report_sha256": hashlib.sha256(args.report.read_bytes()).hexdigest(),
                "audit_path": str(args.audit_dir / "audit.json"), "audit_sha256": hashlib.sha256((args.audit_dir / "audit.json").read_bytes()).hexdigest(),
                "counts": {"report_pairs": len(selected), "report_runs": len(selected_ids), "table6_pairs": 39,
                           "table8_pairs": 7, "fully_recorded_pairs": sum(r["both_exact"] for r in selected),
                           "fully_recorded_runs": sum(r[m + "_exact_tokens"] is not None for r in selected for m in ("none", "full")),
                           "supplementary_full_runs": len(supplementary), "outside_report_runs": len(alternatives)},
                "rows": selected, "run_details": details, "family_summary": summary,
                "supplementary": supplementary, "supplement_details": supplement_details,
                "outside_report": alternatives}
    evidence_ids = selected_ids | supplementary_ids
    selected_calls = [r for r in read_csv(args.audit_dir / "calls.csv") if r["audit_run_id"] in evidence_ids]
    for uid in evidence_ids:
        total = sum(int(c["total_tokens"]) for c in selected_calls if c["audit_run_id"] == uid and c["total_tokens"])
        if total != runs[uid]["observed"]["total_tokens"]:
            raise ValueError(f"Exported call totals do not reconcile for {uid}")
    manifest["counts"]["usage_messages"] = len(selected_calls)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report_mapping.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    display_rows = [{"报告行": r["report_row"], "报告表": r["report_table"], "问题族": r["family_label"], "算例": r["instance_label"],
                     "None最终结果": r["none_result"], "Full最终结果": r["full_result"],
                     "None已记录Token": r["none_recorded_tokens"], "Full已记录Token": r["full_recorded_tokens"],
                     "None记录覆盖": r["none_coverage"], "Full记录覆盖": r["full_coverage"],
                     "None完整总Token": r["none_exact_tokens"], "Full完整总Token": r["full_exact_tokens"],
                     "实际输入文件": r["instance_file"], "说明": r["note"],
                     "None统计运行ID": r["none_run_id"], "Full统计运行ID": r["full_run_id"],
                     "None原始运行清单": r["none_manifest"], "Full原始运行清单": r["full_manifest"],
                     "对应比较报告": r["source_comparison"]} for r in selected]
    write_csv(args.output / "报告逐组Token对照.csv", display_rows)
    write_csv(args.output / "报告运行用量明细.csv", details + supplement_details)
    write_csv(args.output / "报告正文补充实验.csv", supplementary)
    write_csv(args.output / "报告问题族汇总.csv", summary)
    write_csv(args.output / "报告外历史运行.csv", alternatives)
    write_csv(args.output / "报告逐调用记录.csv", selected_calls)
    lines = ["# 测试报告对应的Token用量", "", f"依据：{args.report.name}（SHA256：{manifest['report_sha256']}）。",
             "按表6的39组和表8的7组原顺序列齐，共46组、92个独立运行。每组均核对原始最终成绩；未将同名算例的早期复测替换进来。",
             "", "Token包含所选运行内的Main、全部候选Coding Worker、修复、模型反思、语义审查和子会话；以原始服务返回total去重统计。工业从零生成在本运行内的用量包含在内，共享中性起点在运行外的生成用量另列报告外历史运行。",
             "“仅已记录小计”表示历史日志存在用量缺口，数字可用来说明留存用量，不能作为完整总消耗计算None/Full节省率。缺失值不按零处理。完整组数仅说明用量覆盖，不评价实验质量。",
             "", f"其中双方用量均完整的配对为{manifest['counts']['fully_recorded_pairs']}组，单侧完整运行{manifest['counts']['fully_recorded_runs']}个。",
             "", "|报告行|问题族|算例|None已记录Token|Full已记录Token|None覆盖|Full覆盖|", "|---|---|---|---:|---:|---|---|"]
    for r in selected:
        lines.append(f"|{r['report_row']}|{r['family_label']}|{r['instance_label']}|{r['none_recorded_tokens']:,}|{r['full_recorded_tokens']:,}|{r['none_coverage']}|{r['full_coverage']}|")
    lines += ["", "## 正文补充实验", "", "正文提及的20任务Full单臂及800任务扩大预算Full单臂另列，不并入46组配对。",
              "", "|报告位置|算例|模式|完成轮次|已记录Token|覆盖|", "|---|---|---|---:|---:|---|"]
    for r in supplementary:
        lines.append(f"|{r['报告位置']}|{r['算例']}|Full|{r['完成轮次']}|{r['已记录Token小计']:,}|{r['记录覆盖']}|")
    lines += ["", "## 报告行名与原始输入", "", selected[39]["note"], "", "## 文件说明", "",
              "- 测试报告Token用量对照.xlsx：逐组对照、问题族汇总及运行来源；正文单臂实验在逐组表下方单列。",
              "- 报告逐组Token对照.csv：46组，顺序对应表6和表8；保留最终成绩、实际输入名及运行路径。",
              "- 报告运行用量明细.csv：92个配对运行及2个正文补充运行，含角色用量、覆盖缺口与原始清单哈希。",
              "- 报告正文补充实验.csv：正文单独提及的两个Full单臂实验。",
              "- 报告问题族汇总.csv：只汇总本报告采用的运行，不包含其他版本。",
              "- 报告外历史运行.csv：共享起点准备、早期复测、补充算例及扩大预算等其余历史运行。",
              "- 报告逐调用记录.csv：所选94个运行的逐消息用量与来源路径，按统计运行ID关联明细；已核对各运行消息小计。",
              "- report_mapping.json：报告与审计源哈希、逐行匹配关系及原始标识。",
              "", "完整逐调用证据保留在同工作区 exports/none_full_token_usage_20261021/calls.csv；通过统计运行ID与运行明细关联。"]
    (args.output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(manifest["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
