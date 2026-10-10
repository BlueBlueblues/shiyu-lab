# 模板契约确认表（B/C 交付模板前请 A 确认）

提出人：C（临时接手模板交付）｜时间：2026-10-10
依据：总纲 §3.2 / §6 / §7 / §8、成员 B 指南 §2/§5、现有 `tests/` 里已固化的断言
状态：**待 A 确认**。A 只需在每节末尾打勾或直接改，不用重写文档。

> ⚠️ 前置：**仓库里目前没有后端代码。**
> `git clone` 下来的 `firstorder-lab` 只有 5 个文件：
> ```
> .gitignore (236B)  .env.example (239B)  requirements.txt (966B)
> app/__init__.py (0B)  app/services/__init__.py (0B)
> ```
> 没有 `app/main.py`、`app/schemas.py`、`app/db.py`、`app/routes*`、`app/services/*.py`，
> 也没有 `app/templates/`、`app/static/`、`tests/`、`docs/`、`scripts/`。
> **模板无法在看不到真实 `render_template(...)` 与 context 的情况下写**，否则页面继续 500。

---

## 1. 我要交付的文件

| 文件 | 归属 | 说明 |
| --- | --- | --- |
| `app/templates/base.html` | 模板公共布局 | 导航、`<link>` 引 `app.css`、`<script>` 引 `app.js`、内容 block |
| `app/templates/experiment_edit.html` | 草稿编辑 | 只编辑允许字段，不提供增删题/改 ID/改题型 |
| `app/templates/service.html` | 服务展示页 | 预览与公开共用，靠 `is_preview` 区分 |
| `app/templates/survey.html` | 问卷页 | 预览与公开共用，靠 `is_preview` 区分 |
| `app/static/app.css` | 样式 | 320～430px 手机宽度不横向溢出 |
| `app/static/app.js` | 脚本 | 统一 fetch 封装、提交状态、UUID 幂等、轮询 |

**本次不动**：`projects.html`、`project_new.html`、`workspace.html`、`report.html`（工作台/列表/报告页，属另一交付批次）。

---

## 2. 请 A 确认的三件事（最关键的三个问题）

### 问题 1：`render_template` 用的**真实模板名字符串**是什么？

我按总纲 §3.2 假设如下，**请确认或直接改成你代码里的真实值**：

| 路由 | 我假设的模板名 |
| --- | --- |
| `GET /experiments/{experiment_id}/edit` | `"experiment_edit.html"` |
| `GET /experiments/{experiment_id}/preview/service` | `"service.html"` |
| `GET /experiments/{experiment_id}/preview/survey` | `"survey.html"` |
| `GET /e/{public_token}` | `"service.html"` |
| `GET /e/{public_token}/survey` | `"survey.html"` |

- [ ] 确认无误
- [ ] 请改为：____________________________________

> 如果 Jinja2 环境配置的 `template_folder` 不是 `app/templates`，也请说明。

### 问题 2：公开页传的是 `public_token` 还是拼好的 `survey_url`？

问卷页提交要 POST `/api/public/e/{public_token}/responses`，所以模板必须拿到 token。请确认：

- [ ] 传 `public_token`（字符串），我自己拼 URL
- [ ] 传 `survey_url`（完整 URL），我从里面取 token
- [ ] 两个都传

另外：`experiment_edit` 页的"预览"链接需要 `experiment_id`：

- [ ] context 里有 `experiment.id`，我直接拼 `/experiments/{id}/preview/survey`
- [ ] 你直接传现成的 `preview_survey_url` / `preview_service_url`

### 问题 3：`is_preview=true` 时你期望页面怎么表现？

我按总纲 §6.1 与 B 指南 B05 假设：

- 页面顶部显示明显标识「**草稿预览，不能提交**」
- 问卷的提交按钮 **disabled**
- 服务页的「去问卷」按钮指向预览问卷地址（或 disabled）

- [ ] 确认
- [ ] 请改为：____________________________________

---

## 3. 每个模板需要的 context 字段

字段名按总纲 §7.2 / §7.3 的 JSON 结构。**请确认变量名一致**（我按 Jinja2 的 `{{ landing.title }}` 这种属性访问写）。

### 3.1 `experiment_edit.html`

```
experiment = {
  id, project_id,
  landing: {title, summary, target_users, value_proposition, features[], disclaimer},
  questions: [{id, type, title, required, options:[{id,label}]}],
  published_at
}
project = {id, name}
is_preview = false
```

- [ ] 字段名一致
- [ ] 请改为：____________________________________

### 3.2 `service.html`

```
landing = {title, summary, target_users, value_proposition, features[], disclaimer}
is_preview = bool
project = {name}            # 可选
survey_url / public_token   # 见问题 2
```

- [ ] 字段名一致
- [ ] 请改为：____________________________________

### 3.3 `survey.html`

```
questions = [{id, type, title, required, options:[{id,label}]}]
public_token                # 见问题 2
is_preview = bool
landing = {title}           # 可选，用于页头
```

- [ ] 字段名一致
- [ ] 请改为：____________________________________

---

## 4. 我**不会**做的事（避免破坏你的契约）

- 不修改任何后端 API 的路径、入参、返回结构、错误码
- 不新增/删除题目、不改变题型与 ID
- 不在前端计算统计（统计一律读你的 `statistics` 接口）
- 不把模型输出或用户反馈用 `innerHTML` 注入（一律 Jinja2 转义 / `textContent`）
- 不在 JS/HTML 里硬编码管理员密码或 API Key
- 不在 `localStorage` 里存业务数据（只存本次提交 UUID 与成功标记）

---

## 5. DOM 标识约定（`data-testid`）

沿用成员 B 指南 §5。**这些名字一旦定了，C 的浏览器测试就依赖它们，请勿改名。**

| 元素 | `data-testid` |
| --- | --- |
| 草稿保存 | `experiment-save` |
| 发布 | `experiment-publish` |
| 发布后的问卷 URL 文本 | `survey-url` |
| 问卷表单 / 同意勾选 / 提交 | `survey-form` / `survey-consent` / `survey-submit` |
| 每题容器 | `question-{question_id}` |
| 每个选项 | `option-{question_id}-{option_id}` |
| 提交成功提示 | `submission-success` |
| 回答数 | `response-count` |
| 复盘 / 报告内容 / 下载 | `review-generate` / `report-content` / `report-download` |
| 运行日志区域 | `run-log` |

**另外三个需要补齐**（B 指南没给，C 的 E2E 现在用占位符）：

| 元素 | 我打算用 | 是否同意 |
| --- | --- | --- |
| landing.title 输入框 | `input[name='title']` | [ ] 同意 [ ] 改为：______ |
| landing.summary 输入框 | `textarea[name='summary']` | [ ] 同意 [ ] 改为：______ |
| 题目标题输入框 | `input[name='question-{qid}-title']` | [ ] 同意 [ ] 改为：______ |

> 现有 `tests/e2e/test_flow.py` 第 34 行就是 `LANDING_TITLE_INPUT = "input[name='title']"`，
> 你确认后我把这条固化进契约。

---

## 6. 问卷前端的三个硬性行为（来自总纲 §7.3，请确认与你的后端一致）

1. **UUID**：每次填写开始时在浏览器生成一个 UUID，作为 `client_submission_id`。
   网络失败重发时**沿用同一个 UUID 和同一份 payload**。
   如果用户改了答案，则**生成新 UUID**（避免后端按"同 UUID 不同内容"返回 409）。
2. **提交值格式**：
   - `single` → 选项 ID 字符串，如 `"o1"`
   - `multiple` → 选项 ID 字符串数组，如 `["o1","o2"]`
   - `text` → 字符串
   - 可选题未作答 → **从 `answers` 里省略**（不发空字符串/空数组）
3. **请求体**：`{"client_submission_id": "...", "consent": true, "answers": {...}}`
   - 首次成功 **201**，幂等重放 **200** 且 `duplicate=true`
   - 未勾选同意不放行提交

- [ ] 与后端一致
- [ ] 请改为：____________________________________

---

## 7. 我的交付时间

| 前提 | 交付 |
| --- | --- |
| A push 后端代码到 GitHub | 收到后 **2～3 小时内**交付 6 个文件（4 模板 + CSS + JS） |
| 同时需要 | `.env.example` 已有；还需你确认第 2 节的三个问题 |

**在拿到代码之前我无法给准确时间** —— 模板必须对上你真实的 context 变量名，
猜错一个变量名页面就继续 500，反而浪费我们两个人的时间。

---

## 8. A 只需要做的事（三条）

1. **`git push`** —— 确认 `app/main.py`、`app/schemas.py`、`app/db.py`、`app/services/*.py`、
   `app/prompts/*.txt` 都推上去了。现在远端只有空骨架。
2. **回答第 2 节的三个问题**（模板名 / token 还是 url / is_preview 行为）。
3. **回答两个契约问题**：
   - 最终跑 **Python 3.11** 还是 **3.13**？（契约写 3.11，我本机只有 3.13）
   - `llm_client` 里调模型的**函数名**是什么？（C 的 51 个契约用例要按真实名字 mock，否则会真的调用模型产生费用）
