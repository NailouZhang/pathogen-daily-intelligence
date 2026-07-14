# Streamlit Community Cloud 私有仓库部署

## 一、创建应用

在 Streamlit Community Cloud 中选择：

```text
Repository: NailouZhang/pathogen-daily-intelligence
Branch: main
Main file path: app.py
Python: 3.12
```

如果仓库列表中看不到私有仓库，需要在 Streamlit 的 GitHub connection 设置中重新授权该仓库。

## 二、为什么还需要 GITHUB_DATA_TOKEN

Streamlit 获得的私有仓库 Deploy Key 用于拉取部署代码。网页运行后需要读取另一个分支 `intelligence-data` 的内容和状态，因此本工程显式使用 GitHub Contents API。

将一个只读 fine-grained token 放入 Streamlit Secrets：

```toml
PDI_GITHUB_REPO = "NailouZhang/pathogen-daily-intelligence"
PDI_DATA_BRANCH = "intelligence-data"
GITHUB_DATA_TOKEN = "github_pat_xxx"
```

推荐权限：

- Repository access：Only select repositories → `pathogen-daily-intelligence`；
- Contents：Read-only；
- Actions：Read-only（可选）。

## 三、数据读取状态

首页会明确显示：

- GitHub 生产数据；
- Streamlit 本地缓存；
- 包内 Demo；
- 未读取到数据。

生产上线验收时，必须显示“GitHub 生产数据”。

## 四、缓存

成功读取 GitHub 后，文件会短期写入：

```text
runtime/dashboard_cache/
```

此目录是 Streamlit 容器的临时缓存，不提交 Git，也不替代 `intelligence-data`。容器重启后缓存可能消失。

首页“刷新生产数据”会清除内存和本地缓存，然后重新读取 GitHub。

## 五、静态日报

左侧页面“静态日报与下载”会读取：

```text
intelligence-data/site/index.html
```

可直接预览，并下载 HTML 或 DailyIssue JSON。该功能取代 GitHub Pages 的最终网页查看需求。

## 六、常见错误

### 显示 Demo

检查：

- `PDI_GITHUB_REPO` 是否拼写正确；
- `PDI_DATA_BRANCH` 是否为 `intelligence-data`；
- token 是否属于当前账号；
- token 是否授权当前仓库；
- Contents 权限是否为 Read；
- 数据分支中是否存在 `data/latest.json`。

### 能读取日报但不能显示 Workflow 状态

这通常表示 token 只有 Contents: Read，没有 Actions: Read。日报展示不受影响。

### 显示本地缓存

表示以前成功读取过生产数据，但当前 GitHub 请求失败。应查看侧栏错误信息并检查网络、token 有效期或 GitHub API 状态。
