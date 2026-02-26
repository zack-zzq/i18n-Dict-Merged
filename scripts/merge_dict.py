"""
i18n-Dict-Merged: 合并用户贡献的翻译文件与上游 i18n-Dict-Extender 词典。

该脚本执行以下步骤：
1. 从 VM-Chinese-translate-group/i18n-Dict-Extender 最新 Release 下载 Dict-Sqlite.db
2. 扫描 assets/{version}/{modid}/ 目录下的 en_us.json + zh_cn.json 翻译对
3. 将本地翻译条目合并（upsert）到上游数据库
4. 重新生成 Dict.json、Dict-Mini.json、Dict-Sqlite.db、diff.json（格式与上游完全一致）
5. 生成 release_body.md 变更日志
"""

import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import requests
import ujson as json

# --- 配置常量 ---
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

DB_FILENAME = "Dict-Sqlite.db"
JSON_FILENAME = "Dict.json"
MINI_JSON_FILENAME = "Dict-Mini.json"
DIFF_JSON_FILENAME = "diff.json"
RELEASE_BODY_FILENAME = "release_body.md"

UPSTREAM_REPO = "VM-Chinese-translate-group/i18n-Dict-Extender"

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}


# --- 步骤 1: 下载上游数据库 ---

def get_upstream_release_info():
    """获取上游仓库最新 Release 的 tag 和 DB 下载信息。"""
    print(f"正在获取上游仓库 {UPSTREAM_REPO} 的最新 Release 信息...")
    release_url = f"https://api.github.com/repos/{UPSTREAM_REPO}/releases/latest"

    response = requests.get(release_url, headers=HEADERS)
    if response.status_code != 200:
        print(f"警告：无法获取上游 Release (HTTP {response.status_code})。将创建新数据库。")
        return None, None

    release_data = response.json()
    tag = release_data.get("tag_name", "")
    assets = release_data.get("assets", [])
    db_asset = next((a for a in assets if a["name"] == DB_FILENAME), None)

    return tag, db_asset


def download_upstream_db(db_asset, output_path):
    """下载上游 Dict-Sqlite.db 文件。"""
    if not db_asset:
        print(f"上游 Release 中未找到 {DB_FILENAME}，将创建新数据库。")
        return False

    print(f"正在下载上游 {DB_FILENAME}...")
    download_url = db_asset["url"]
    headers = HEADERS.copy()
    headers["Accept"] = "application/octet-stream"

    with requests.get(download_url, headers=headers, stream=True) as r:
        r.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

    print(f"上游 {DB_FILENAME} 下载完成。")
    return True


# --- 步骤 2: 初始化数据库 ---

def initialize_db(conn):
    """初始化数据库表结构（与上游一致）。"""
    print("正在初始化数据库表结构...")
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dict(
        ID INTEGER PRIMARY KEY AUTOINCREMENT,
        ORIGIN_NAME     TEXT    NOT NULL,
        TRANS_NAME      TEXT    NOT NULL,
        MODID           TEXT    NOT NULL,
        KEY             TEXT    NOT NULL,
        VERSION         TEXT    NOT NULL,
        CURSEFORGE      TEXT    NOT NULL
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_origin_name ON dict (ORIGIN_NAME);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lookup ON dict (MODID, KEY, VERSION, CURSEFORGE);")
    conn.commit()
    print("数据库表结构就绪。")


# --- 步骤 3: 扫描并合并本地翻译 ---

def scan_assets():
    """
    扫描 assets 目录，返回待处理的模组列表。

    目录结构：assets/{version}/{modid}/en_us.json + zh_cn.json
    可选：assets/{version}/{modid}/meta.json 用于覆盖 modid / curseforge 等字段。

    返回: list[dict]，每个 dict 包含 version, modid, curseforge, en_path, zh_path
    """
    entries = []

    if not ASSETS_DIR.exists():
        print(f"警告：assets 目录不存在: {ASSETS_DIR}")
        return entries

    for version_dir in sorted(ASSETS_DIR.iterdir()):
        if not version_dir.is_dir() or version_dir.name.startswith("."):
            continue

        version = version_dir.name

        for modid_dir in sorted(version_dir.iterdir()):
            if not modid_dir.is_dir() or modid_dir.name.startswith("."):
                continue

            en_path = modid_dir / "en_us.json"
            zh_path = modid_dir / "zh_cn.json"

            if not en_path.exists() or not zh_path.exists():
                print(f"  跳过 {version}/{modid_dir.name}：缺少 en_us.json 或 zh_cn.json")
                continue

            # 默认值：从目录名推断
            modid = modid_dir.name
            curseforge = modid.replace("_", "-")

            # 可选 meta.json 覆盖
            meta_path = modid_dir / "meta.json"
            if meta_path.exists():
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    modid = meta.get("modid", modid)
                    curseforge = meta.get("curseforge", curseforge)
                    version = meta.get("version", version)
                except Exception as e:
                    print(f"  警告：读取 {meta_path} 失败: {e}")

            entries.append({
                "version": version,
                "modid": modid,
                "curseforge": curseforge,
                "en_path": en_path,
                "zh_path": zh_path,
                "dir_label": f"{version_dir.name}/{modid_dir.name}",
            })

    return entries


def merge_mod_entries(cursor, mod_info):
    """
    将单个模组的翻译条目合并到数据库中。

    返回: (insert_count, update_count, skipped_count)
    """
    en_path = mod_info["en_path"]
    zh_path = mod_info["zh_path"]
    modid = mod_info["modid"]
    version = mod_info["version"]
    curseforge = mod_info["curseforge"]

    with open(en_path, "r", encoding="utf-8") as f:
        en_data = json.load(f)
    with open(zh_path, "r", encoding="utf-8") as f:
        zh_data = json.load(f)

    common_keys = en_data.keys() & zh_data.keys()

    # 查询现有条目，构建 key -> ID 的映射
    cursor.execute(
        "SELECT KEY, ID FROM dict WHERE MODID=? AND VERSION=? AND CURSEFORGE=?",
        (modid, version, curseforge),
    )
    existing_map = {row[0]: row[1] for row in cursor.fetchall()}

    to_update = []
    to_insert = []
    diff_entries = []
    skipped = 0

    for key in common_keys:
        origin = en_data[key]
        trans = zh_data[key]

        # 跳过非字符串值（例如 JSON 文本组件）
        if not isinstance(origin, str) or not isinstance(trans, str):
            skipped += 1
            continue

        entry = {
            "origin_name": origin,
            "trans_name": trans,
            "modid": modid,
            "key": key,
            "version": version,
            "curseforge": curseforge,
        }
        diff_entries.append(entry)

        existing_id = existing_map.get(key)
        if existing_id:
            to_update.append((origin, trans, existing_id))
        else:
            to_insert.append((origin, trans, modid, key, version, curseforge))

    # 批量操作
    if to_update:
        cursor.executemany(
            "UPDATE dict SET ORIGIN_NAME=?, TRANS_NAME=? WHERE ID=?", to_update
        )
    if to_insert:
        cursor.executemany(
            "INSERT INTO dict (ORIGIN_NAME, TRANS_NAME, MODID, KEY, VERSION, CURSEFORGE) VALUES (?, ?, ?, ?, ?, ?)",
            to_insert,
        )

    return len(to_insert), len(to_update), skipped, diff_entries


# --- 步骤 4: 重新生成 Release 文件 ---

def regenerate_release_files(db_path, output_dir):
    """
    从合并后的数据库重新生成 Dict.json 和 Dict-Mini.json。
    逻辑严格遵循上游 i18n-Dict-Extender 的 regenerate_release_files()。
    """
    print("\n--- 开始从数据库重新生成 Release 文件 ---")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        "SELECT ORIGIN_NAME, TRANS_NAME, MODID, KEY, VERSION, CURSEFORGE FROM dict"
    )
    all_entries = [
        {
            "origin_name": r["ORIGIN_NAME"],
            "trans_name": r["TRANS_NAME"],
            "modid": r["MODID"],
            "key": r["KEY"],
            "version": r["VERSION"],
            "curseforge": r["CURSEFORGE"],
        }
        for r in cursor.fetchall()
    ]
    conn.close()

    integral = []
    integral_mini_temp = defaultdict(list)

    print(f"处理 {len(all_entries)} 个词条中...")
    for entry in all_entries:
        if len(entry["origin_name"]) > 50 or entry["origin_name"] == "":
            continue
        integral.append(entry)
        if entry["origin_name"] != entry["trans_name"]:
            integral_mini_temp[entry["origin_name"]].append(entry["trans_name"])

    # 使用 Counter 进行高效排序（与上游一致）
    integral_mini_final = {
        origin: [item for item, _ in Counter(trans_list).most_common()]
        for origin, trans_list in integral_mini_temp.items()
    }

    # 生成 Dict.json
    json_path = output_dir / JSON_FILENAME
    text = json.dumps(integral, ensure_ascii=False, indent=4)
    if text != "[]":
        json_path.write_text(text, encoding="utf-8")
        print(f"已生成 {JSON_FILENAME}，共 {len(integral)} 个词条")
    else:
        print(f"{JSON_FILENAME} 为空，跳过生成。")

    # 生成 Dict-Mini.json
    mini_path = output_dir / MINI_JSON_FILENAME
    mini_text = json.dumps(integral_mini_final, ensure_ascii=False, separators=(",", ":"))
    if mini_text != "{}":
        mini_path.write_text(mini_text, encoding="utf-8")
        print(f"已生成 {MINI_JSON_FILENAME}，共 {len(integral_mini_final)} 个词条")
    else:
        print(f"{MINI_JSON_FILENAME} 为空，跳过生成。")


# --- 步骤 5: 生成 release body ---

def generate_release_body(summaries, diff_count):
    """生成 Release 描述的 Markdown 文本。"""
    body = [
        "## 合并词典更新",
        f"本次合并共计处理了 **{diff_count}** 个用户贡献的翻译条目。",
        "",
        "### 数据来源与变更摘要",
        "",
    ]

    if not summaries:
        body.append("本次运行未合并任何翻译数据。")
        return "\n".join(body)

    body.append("| 模组 (modid) | 版本 | 新增条目 | 更新条目 | 跳过条目 | 状态 |")
    body.append("|---|---|---:|---:|---:|:---|")

    for s in summaries:
        status = "✅ 成功" if not s.get("error") else f"❌ 失败: `{s['error']}`"
        body.append(
            f"| `{s['modid']}` | `{s['version']}` | {s['inserted']} | {s['updated']} | {s['skipped']} | {status} |"
        )

    body.append("")
    body.append("`diff.json` 文件包含了本次合并所有新增和更新的条目详情。")
    return "\n".join(body)


# --- 主函数 ---

def main():
    # 确保输出目录存在
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    db_path = OUTPUT_DIR / DB_FILENAME

    # 1. 获取上游 Release 并下载 DB
    upstream_tag, db_asset = get_upstream_release_info()
    if upstream_tag:
        print(f"上游最新 Release tag: {upstream_tag}")

    if not download_upstream_db(db_asset, db_path):
        # 无法下载，创建新 DB
        conn = sqlite3.connect(str(db_path))
        initialize_db(conn)
    else:
        conn = sqlite3.connect(str(db_path))
        initialize_db(conn)  # 确保索引存在

    cursor = conn.cursor()

    # 2. 扫描 assets 目录
    mod_entries = scan_assets()
    print(f"\n共发现 {len(mod_entries)} 个待合并的模组翻译。\n")

    if not mod_entries:
        print("没有找到任何翻译文件，退出。")
        conn.close()
        return

    # 3. 逐个合并
    summaries = []
    all_diff_entries = []

    for mod_info in mod_entries:
        label = mod_info["dir_label"]
        print(f"--- 处理: {label} (modid={mod_info['modid']}, version={mod_info['version']}) ---")

        try:
            inserted, updated, skipped, diff_entries = merge_mod_entries(cursor, mod_info)
            all_diff_entries.extend(diff_entries)
            print(f"  完成: 新增 {inserted} / 更新 {updated} / 跳过 {skipped}")
            summaries.append({
                "modid": mod_info["modid"],
                "version": mod_info["version"],
                "inserted": inserted,
                "updated": updated,
                "skipped": skipped,
                "error": None,
            })
        except Exception as e:
            print(f"  错误: {e}")
            import traceback
            traceback.print_exc()
            summaries.append({
                "modid": mod_info["modid"],
                "version": mod_info["version"],
                "inserted": 0,
                "updated": 0,
                "skipped": 0,
                "error": str(e),
            })

    conn.commit()
    conn.close()

    # 4. 重新生成 Release 文件
    regenerate_release_files(db_path, OUTPUT_DIR)

    # 5. 生成 diff.json
    diff_path = OUTPUT_DIR / DIFF_JSON_FILENAME
    print(f"\n正在生成 {DIFF_JSON_FILENAME}，包含 {len(all_diff_entries)} 个变动条目...")
    with open(diff_path, "w", encoding="utf-8") as f:
        json.dump(all_diff_entries, f, ensure_ascii=False, indent=4)
    print(f"{DIFF_JSON_FILENAME} 生成完毕。")

    # 6. 生成 release body
    release_body_path = OUTPUT_DIR / RELEASE_BODY_FILENAME
    total_diff = sum(s["inserted"] + s["updated"] for s in summaries)
    release_body_content = generate_release_body(summaries, total_diff)
    release_body_path.write_text(release_body_content, encoding="utf-8")
    print(f"{RELEASE_BODY_FILENAME} 生成完毕。")

    # 7. 保存上游 tag（供 GitHub Actions 使用）
    if upstream_tag:
        tag_file = OUTPUT_DIR / "last_upstream_tag.txt"
        tag_file.write_text(upstream_tag, encoding="utf-8")
        print(f"已保存上游 tag: {upstream_tag}")

    print(f"\n所有任务完成！输出文件位于: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
