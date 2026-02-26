# i18n-Dict-Merged

[![Merge and Release](https://github.com/zack-zzq/i18n-Dict-Merged/actions/workflows/merge_and_release.yml/badge.svg)](https://github.com/zack-zzq/i18n-Dict-Merged/actions/workflows/merge_and_release.yml)

将用户贡献的翻译文件与上游 [i18n-Dict-Extender](https://github.com/VM-Chinese-translate-group/i18n-Dict-Extender) 词典合并，发布增强版 Minecraft 模组翻译词典。

## 🎯 项目目标

[i18n-Dict-Extender](https://github.com/VM-Chinese-translate-group/i18n-Dict-Extender) 从模组官方仓库自动抓取翻译并生成词典，但总有一些翻译无法被自动覆盖。本项目提供一个**人工补充通道**：用户可以直接向 `assets/` 目录提交翻译文件，系统会自动将这些翻译与上游词典合并，发布一个更完整的词典。

## 📁 如何贡献翻译

### 目录结构

将翻译文件放在 `assets/{游戏版本}/{模组ID}/` 目录下：

```
assets/
├── 1.21.1/
│   ├── actuallyadditions/
│   │   ├── en_us.json      # 英文原文
│   │   └── zh_cn.json      # 中文译文
│   └── additional_lights/
│       ├── en_us.json
│       └── zh_cn.json
├── 1.20.1/
│   └── some_mod/
│       ├── en_us.json
│       └── zh_cn.json
```

### 翻译文件格式

标准 Minecraft 语言文件格式（JSON key-value 对）：

```json
{
  "item.modid.example_item": "Example Item",
  "block.modid.example_block": "Example Block"
}
```

> **注意**：`en_us.json` 和 `zh_cn.json` 中的 key 必须对应。只有两个文件中都存在的 key 才会被收录到词典。

### 可选：meta.json

如需自定义模组元信息，可在模组目录下创建 `meta.json`：

```json
{
  "modid": "custom_modid",
  "curseforge": "custom-curseforge-slug",
  "version": "1.21"
}
```

不提供 `meta.json` 时，`modid` 默认使用目录名，`curseforge` 默认使用目录名（下划线替换为连字符），`version` 默认使用上级版本目录名。

## ⚙️ 自动化工作流

本项目由 GitHub Actions 驱动，有以下触发方式：

| 触发方式 | 条件 | 说明 |
|---|---|---|
| **定时任务** | 每天 UTC 12:00 | 检测上游 i18n-Dict-Extender 是否有新 Release，有则合并 |
| **Push 触发** | `assets/` 目录变更 | 用户提交新翻译后立即触发合并 |
| **手动触发** | workflow_dispatch | 可在 Actions 页面手动运行 |

### 工作流程

1. **下载上游词典**：从 i18n-Dict-Extender 最新 Release 获取 `Dict-Sqlite.db`
2. **扫描本地翻译**：遍历 `assets/` 目录下所有模组翻译
3. **合并数据**：将本地翻译条目 upsert 到上游数据库
4. **生成产物**：重新生成 `Dict.json`、`Dict-Mini.json`、`Dict-Sqlite.db`、`diff.json`
5. **发布 Release**：创建新的 GitHub Release，附带所有词典文件

## 📦 Release 文件

与上游 i18n-Dict-Extender 格式完全一致：

- **`Dict.json`** — 完整词典（JSON 数组，每个条目包含 origin_name / trans_name / modid / key / version / curseforge）
- **`Dict-Mini.json`** — 轻量词典（原文 → 译名列表，按出现频率排序）
- **`Dict-Sqlite.db`** — SQLite 数据库版词典
- **`diff.json`** — 本次合并的变动条目

## 📜 版权归属

本项目的数据基础源自 CFPA [Minecraft 模组简体中文翻译项目](https://github.com/CFPAOrg/Minecraft-Mod-Language-Package) 及其演绎项目。

词典数据遵循 [**CC BY-NC-SA 4.0**](https://creativecommons.org/licenses/by-nc-sa/4.0/) 授权。  
自动化脚本采用 [**MIT LICENSE**](https://mit-license.org/) 许可。
