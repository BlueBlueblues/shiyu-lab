# 管理页面接线与 A06 交接

日期：2026-10-11；分支 feat/a-backend；开始 HEAD 3f79df93c93cf854b0768a53382c824ff9197af0，初始工作区干净，未发现适用 AGENTS.md。

## 第一批：管理页面 context 契约

三个页面沿用管理员 HTTP Basic，复用现有仓库查询、派生状态、受控分享 URL 和 Jinja 自动转义。

| 路由 | 模板 | 业务 context |
| --- | --- | --- |
| GET / | projects.html | projects，与 GET /api/projects 的 data 相同 |
| GET /projects/new | project_new.html | 空对象；创建操作调用现有 POST /api/projects |
| GET /projects/{project_id} | workspace.html | 与 GET /api/projects/{project_id} 的 data 完全一致 |

所有模板另有 Jinja 提供的 request。project_new 路由注册在动态项目路径之前；首页替换原临时响应，没有重复注册。

新建 test 项目 context 示例（UUID 和时间为占位说明）：

```json
{
  "project": {
    "id": "项目UUID", "name": "测试项目", "idea": "低成本创业服务验证",
    "target_users": "测试对象", "skills": "Python", "budget_cny": 100,
    "validation_goal": "验证需求", "data_kind": "test",
    "created_at": "UTC ISO 8601", "status": "draft"
  },
  "plan": null,
  "experiment": null,
  "latest_report_meta": null,
  "running_run_id": null
}
```

首页 projects 为摘要数组，每项固定包含 id、name、status、data_kind、created_at。详情 project 不含 plan_json，plan 单独提供；已有实验、报告和运行信息时沿用现有详情 API 对象。

不存在的项目返回 HTTP 404 / NOT_FOUND，未认证返回 401 / AUTH_REQUIRED。

**路由已接线、页面待联调。** 当前仓库仍未交付 projects.html、project_new.html、workspace.html；缺模板返回既有 HTTP 500 / INTERNAL_ERROR 与明确集成未完成说明。管理页面测试只用临时模板确认 context 和路由行为，不代表正式页面验收通过。未修改模板或静态资源。

第一批验证：

```powershell
conda run -n firstorder-lab python -m pytest tests/test_management_pages.py tests/test_a01.py tests/test_a05.py -q
git diff --check
```

结果：93 passed，1 warning，10.53 秒；diff 检查通过。A01 首页测试同步改为临时模板，以继续验证管理员认证，不再依赖已替换的占位文字。

## 第二批：A06 接口

### GET /api/experiments/{experiment_id}/responses

管理员权限；按整数回答 ID 升序返回所有已保存回答，不分页、不截断。只提供 id、answers、created_at，不暴露 client_submission_id、token 或内部字段。草稿存在但尚无回答时返回空数组；实验不存在返回 404。

```json
{"data":[{"id":1,"answers":{"q1":"o1","q2":"o1","q3":"第一份反馈","q4":["o1","o2"]},"created_at":"UTC ISO 8601"}]}
```

### GET /api/experiments/{experiment_id}/statistics

管理员权限；统计已发布实验，草稿返回 400 / INVALID_STATE，实验不存在返回 404。data 完整字段为 experiment_id、response_count、cutoff_response_id、questions；复用已有 Statistics Schema，不改变字段契约。

测试样本共 4 份回答，其中 q4 有 3 份有效作答。下面仅摘录完整结果中的 q4：

```json
{
  "id":"q4", "type":"multiple", "answered_count":3,
  "options":[
    {"id":"o1","label":"手机使用方便","count":2,"percentage":66.67},
    {"id":"o2","label":"信息导出","count":3,"percentage":100.0},
    {"id":"o3","label":"操作简单","count":0,"percentage":0.0},
    {"id":"o4","label":"暂时无需求","count":0,"percentage":0.0}
  ]
}
```

- 每题有效回答独立计数；多选分母为作答份数，百分比之和可以超过 100%。
- 所有题、所有选项均输出，保持原顺序和标签；零分母 percentage 为 null。
- 数值四舍五入至 2 位小数，JSON 数值不保证保留尾随零；B 按两位小数展示。
- 简答返回 response_id/text，空白文本不计入有效作答。
- response_count 是该实验记录数；cutoff_response_id 是这份读取快照的最大 ID，零回答为 0，不将最大 ID 当数量。
- 同一 SQLite 读取事务取得题目与回答；测试在两次读取之间通过另一连接插入回答，确认新回答不会混入已开始的快照。
- 统计是纯 Python 计算，不调用模型；不保存新报告或运行日志。Agent 运行中仍可读取这些接口。

第二批验证：

```powershell
conda run -n firstorder-lab python -m pytest tests/test_a06.py -q
conda run -n firstorder-lab python -m pytest -q
conda run -n firstorder-lab python docs/verify_templates.py
git diff --check
```

实际结果：A06 13 passed（2.00 秒）；全量 301 passed（18.42 秒）；现有模板验证 34/34 通过；diff 检查通过。pytest 保留现有 1 条 Starlette/httpx 弃用警告，未升级依赖。

测试只使用临时 SQLite、明确 test 项目和测试答案，没有触碰真实反馈库、读取或输出真实密钥，也没有新增真实模型请求。未提交、推送或合并。

待 B/C：交付三个管理模板后核对上述 context、页面空状态、运行显示及真实 API 交互；接入原始反馈和统计展示，并补浏览器/手机验证。本轮未开发 A07。
