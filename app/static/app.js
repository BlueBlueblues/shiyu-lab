/* FirstOrder Lab 前端脚本
 *
 * 职责（成员 B 指南 §4 B01/B04/B05）：
 *   1. 统一 JSON 请求封装：读取 {data} 或 {error{code,message,fields}}
 *   2. 管理写请求自动带同源凭据与 X-Requested-With: FirstOrderLab
 *   3. 草稿编辑页：收集完整 {landing, questions} 并 PATCH，只允许文本变化
 *   4. 问卷页：三类题收集、必答校验、UUID 幂等提交、成功/失败反馈、草稿预览禁用
 *
 * 安全：不使用 innerHTML 拼接外部文本；全部用 textContent。
 */
(function () {
  "use strict";

  var SITE_HEADER = "FirstOrderLab";
  var ADMIN_TIMEOUT_MS = 90000;   // 工作流 80 秒，留出响应传输余量。
  var PUBLIC_TIMEOUT_MS = 30000;  // 公开提交预算

  /* ------------------------------------------------------------------ *
   * 工具
   * ------------------------------------------------------------------ */

  function uuid() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    // 兜底：非安全上下文（如 http://192.168.x.x）下 randomUUID 不可用
    var bytes = new Uint8Array(16);
    (window.crypto || window.msCrypto).getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    var hex = [];
    for (var i = 0; i < 16; i += 1) {
      hex.push((bytes[i] + 0x100).toString(16).slice(1));
    }
    return (
      hex.slice(0, 4).join("") + "-" + hex.slice(4, 6).join("") + "-" +
      hex.slice(6, 8).join("") + "-" + hex.slice(8, 10).join("") + "-" +
      hex.slice(10, 16).join("")
    );
  }

  function setStatus(element, text, kind) {
    if (!element) { return; }
    element.textContent = text || "";
    element.classList.remove("is-error", "is-ok");
    if (kind === "error") { element.classList.add("is-error"); }
    if (kind === "ok") { element.classList.add("is-ok"); }
  }

  /**
   * B 追加（缺陷修复）：后端 422 的 error.fields 键名是题号（如 q1）或 answers.q1。
   * 直接显示 q1 对受访者不可读，这里换成人能读的「第 N 题」；
   * questions 省略时退回原键名，管理端调用行为不变。
   */
  function questionLabel(key, questions) {
    var id = String(key).replace(/^answers\./, "");
    if (questions && questions.length) {
      for (var i = 0; i < questions.length; i += 1) {
        if (questions[i].id === id) { return "第 " + (i + 1) + " 题"; }
      }
    }
    return key;
  }

  function firstErrorQuestion(error, questions) {
    var fields = (error && error.fields) || {};
    var keys = Object.keys(fields);
    for (var i = 0; i < keys.length; i += 1) {
      var id = String(keys[i]).replace(/^answers\./, "");
      for (var j = 0; questions && j < questions.length; j += 1) {
        if (questions[j].id === id) { return questions[j].id; }
      }
    }
    return null;
  }

  function showFieldErrors(container, error, questions) {
    if (!container) { return; }
    while (container.firstChild) { container.removeChild(container.firstChild); }
    var fields = (error && error.fields) || {};
    var keys = Object.keys(fields);
    if (!keys.length) {
      container.hidden = true;
      return;
    }
    var title = document.createElement("p");
    title.textContent = "请修正以下内容：";
    container.appendChild(title);
    var list = document.createElement("ul");
    keys.forEach(function (key) {
      var item = document.createElement("li");
      item.textContent = questionLabel(key, questions) + "：" + fields[key];
      list.appendChild(item);
    });
    container.appendChild(list);
    container.hidden = false;
  }

  function withTimeout(ms) {
    var controller = new AbortController();
    var timer = window.setTimeout(function () { controller.abort(); }, ms);
    return { signal: controller.signal, done: function () { window.clearTimeout(timer); } };
  }

  /**
   * 统一请求。成功返回 data；失败抛出 {code, message, fields, status}。
   * 管理写入自动附加同源检查所需的请求头。
   */
  function request(method, url, payload, options) {
    var opts = options || {};
    var limits = withTimeout(opts.timeout || (opts.admin ? ADMIN_TIMEOUT_MS : PUBLIC_TIMEOUT_MS));
    var headers = { Accept: "application/json" };
    var init = {
      method: method,
      credentials: opts.admin ? "same-origin" : "omit",
      headers: headers,
      signal: limits.signal
    };
    if (payload !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(payload);
    }
    if (opts.admin) {
      headers["X-Requested-With"] = SITE_HEADER;
    }
    return fetch(url, init).then(function (response) {
      return response.text().then(function (text) {
        limits.done();
        var body = null;
        if (text) {
          try { body = JSON.parse(text); } catch (error) { body = null; }
        }
        if (body && body.error) {
          throw {
            status: response.status,
            code: body.error.code || "INTERNAL_ERROR",
            message: body.error.message || "请求未成功。",
            fields: body.error.fields || {},
            run_id: body.error.run_id
          };
        }
        if (!response.ok) {
          throw {
            status: response.status,
            code: "HTTP_" + response.status,
            message: "请求未成功（HTTP " + response.status + "）。",
            fields: {}
          };
        }
        return body && Object.prototype.hasOwnProperty.call(body, "data") ? body.data : body;
      });
    }).catch(function (error) {
      limits.done();
      if (error && error.code) { throw error; }
      if (error && error.name === "AbortError") {
        throw { status: 0, code: "TIMEOUT", message: "请求超时，请稍后手动重试。", fields: {} };
      }
      throw { status: 0, code: "NETWORK_ERROR", message: "网络请求失败，请检查网络后重试。", fields: {} };
    });
  }

  function messageFor(error) {
    var map = {
      AUTH_REQUIRED: "登录状态已失效，请重新登录。",
      FORBIDDEN: "该操作必须从受控的管理页面发起。",
      NOT_FOUND: "资源不存在或已被删除。",
      RUN_IN_PROGRESS: "该项目已有任务执行中，请稍后再试。",
      EXPERIMENT_LOCKED: "实验已发布，内容已锁定。若要改题请创建下一轮项目。",
      IDEMPOTENCY_CONFLICT: "本次提交编号已用于不同内容，请刷新页面后重新填写。",
      VALIDATION_ERROR: "填写内容不符合要求，请检查后重试。",
      LLM_UPSTREAM_ERROR: "模型服务暂时不可用，请稍后手动重试。",
      LLM_TIMEOUT: "模型响应超时，请手动重试。",
      LLM_OUTPUT_INVALID: "模型输出不符合要求，本次未生成结果，请手动重试。",
      INTERNAL_ERROR: "服务暂时无法完成请求，请稍后重试。",
      TIMEOUT: "请求超时，请稍后手动重试。",
      NETWORK_ERROR: "网络请求失败，请检查网络后重试。"
    };
    return map[error && error.code] || (error && error.message) || "请求未成功。";
  }

  /* ------------------------------------------------------------------ *
   * 草稿编辑页（experiment_edit.html）
   * ------------------------------------------------------------------ */

  function valueOf(form, name) {
    var field = form.elements[name];
    if (!field) { return ""; }
    if (typeof field.value === "string") { return field.value; }
    return field[0] ? field[0].value : "";
  }

  function collectLanding(form) {
    var features = valueOf(form, "features")
      .split("\n")
      .map(function (line) { return line.trim(); })
      .filter(function (line) { return line.length > 0; });

    return {
      title: valueOf(form, "title").trim(),
      summary: valueOf(form, "summary").trim(),
      target_users: valueOf(form, "target_users").trim(),
      value_proposition: valueOf(form, "value_proposition").trim(),
      features: features,
      disclaimer: valueOf(form, "disclaimer").trim()
    };
  }

  function collectQuestions(form, existing) {
    // 结构（id/type/options 的编号与顺序）严格沿用服务端返回，只替换可编辑文本
    return existing.map(function (question) {
      var titleField = form.elements["question-" + question.id + "-title"];
      var requiredField = form.elements["question-" + question.id + "-required"];
      return {
        id: question.id,
        type: question.type,
        title: titleField ? titleField.value.trim() : question.title,
        required: requiredField ? !!requiredField.checked : question.required,
        options: (question.options || []).map(function (option) {
          var labelField = form.elements["option-" + question.id + "-" + option.id + "-label"];
          return {
            id: option.id,
            label: labelField ? labelField.value.trim() : option.label
          };
        })
      };
    });
  }

  function initEditPage(root) {
    var form = document.getElementById("experiment-form");
    if (!form) { return; }
    if (form.dataset.locked === "true") { return; }   // 已发布：只读，无保存

    var saveButton = root.querySelector('[data-testid="experiment-save"]');
    var status = root.querySelector('[data-testid="experiment-status"]');
    var errorBox = document.querySelector('[data-testid="field-errors"]');
    var experimentId = form.dataset.experimentId;
    var existing = window.__FO_EXPERIMENT__ || null;

    if (!experimentId || !existing || !saveButton) { return; }

    saveButton.addEventListener("click", function () {
      var landing = collectLanding(form);
      var questions = collectQuestions(form, existing.questions || []);

      if (!landing.title || !landing.summary || !landing.target_users ||
          !landing.value_proposition || !landing.disclaimer) {
        setStatus(status, "保存失败：服务文案的必填项不能为空。", "error");
        return;
      }
      if (landing.features.length > 6) {
        setStatus(status, "保存失败：能力说明最多 6 项。", "error");
        return;
      }

      saveButton.disabled = true;
      setStatus(status, "正在保存…", null);
      showFieldErrors(errorBox, null);

      request("PATCH", "/api/experiments/" + encodeURIComponent(experimentId),
              { landing: landing, questions: questions },
              { admin: true, timeout: ADMIN_TIMEOUT_MS })
        .then(function (data) {
          window.__FO_EXPERIMENT__ = data;   // 后续保存沿用服务端返回的结构
          setStatus(status, "已保存。", "ok");
        })
        .catch(function (error) {
          setStatus(status, "保存失败：" + messageFor(error), "error");
          showFieldErrors(errorBox, error);
        })
        .then(function () {
          saveButton.disabled = false;      // 无论成败都恢复可用（E2E 依赖此行为）
        });
    });
  }

  /* ------------------------------------------------------------------ *
   * 问卷页（survey.html）
   * ------------------------------------------------------------------ */

  function storageKey(token) { return "fo-lab-submission:" + token; }

  function readStored(key) {
    try { return window.localStorage.getItem(key); } catch (error) { return null; }
  }

  function writeStored(key, value) {
    try { window.localStorage.setItem(key, value); } catch (error) { /* 隐私模式下忽略 */ }
  }

  function tokenFromSubmitUrl(url) {
    var match = /\/api\/public\/e\/([^/]+)\/responses/.exec(url || "");
    return match ? decodeURIComponent(match[1]) : "";
  }

  function questionIdFromName(name) {
    return name.indexOf("answer-") === 0 ? name.slice("answer-".length) : name;
  }

  function collectAnswers(form) {
    var questions = (form.dataset.questions ? JSON.parse(form.dataset.questions) : []) || [];
    var answers = {};
    var missing = [];

    questions.forEach(function (question) {
      var name = "answer-" + question.id;
      var fields = form.querySelectorAll('[name="' + name + '"]');
      var value = null;

      if (question.type === "single") {
        for (var i = 0; i < fields.length; i += 1) {
          if (fields[i].checked) { value = fields[i].value; break; }
        }
      } else if (question.type === "multiple") {
        var picked = [];
        for (var j = 0; j < fields.length; j += 1) {
          if (fields[j].checked) { picked.push(fields[j].value); }
        }
        value = picked.length ? picked.sort() : null;
      } else {
        var text = fields.length ? fields[0].value.trim() : "";
        value = text.length ? text : null;
      }

      if (value === null) {
        if (question.required) { missing.push(question); }
        return;                      // 可选题未作答：从 answers 中省略
      }
      answers[question.id] = value;
    });

    return { answers: answers, missing: missing };
  }

  function showSubmissionSuccess(form, submittedAt) {
    var successBox = document.querySelector('[data-testid="submission-success"]');
    var timeSlot = document.querySelector('[data-testid="submission-time"]');
    if (timeSlot) { timeSlot.textContent = submittedAt || new Date().toLocaleString(); }
    if (successBox) { successBox.hidden = false; }
    if (form) {
      Array.prototype.forEach.call(form.elements, function (element) {
        if (element.type !== "hidden") { element.disabled = true; }
      });
    }
  }

  function initSurveyPage(root) {
    var form = document.getElementById("survey-form");
    if (!form) { return; }

    var status = root.querySelector('[data-testid="survey-status"]');
    var errorBox = document.querySelector('[data-testid="field-errors"]');
    var submitButton = root.querySelector('[data-testid="survey-submit"]');
    var consent = root.querySelector('[data-testid="survey-consent"]');
    var submitUrl = form.dataset.submitUrl || "";
    var isPreview = form.dataset.isPreview === "true";

    if (isPreview || !submitUrl) {
      // 草稿预览：后端已把控件 disabled，这里再兜一层，并给出可读说明
      if (submitButton) { submitButton.disabled = true; }
      setStatus(status, "这是草稿预览，不能提交。", null);
      return;
    }

    var token = tokenFromSubmitUrl(submitUrl);
    var key = storageKey(token);
    var sigKey = key + ":sig";     // B 追加：上一次提交的内容指纹
    var doneKey = key + ":done";   // B 追加：成功标记（只用于避免重复操作）
    var questions = (form.dataset.questions ? JSON.parse(form.dataset.questions) : []) || [];

    // B 追加（缺陷修复，B 指南 §B05 第 8 条）：刷新后若本浏览器已成功提交过，
    // 直接显示收条并禁用表单，避免同一个人反复提交。该标记不是独立用户证明。
    var doneAt = readStored(doneKey);
    if (doneAt) {
      showSubmissionSuccess(form, doneAt);
      setStatus(status, "本浏览器此前已成功提交过这份问卷，无需重复填写。", "ok");
      return;
    }

    var submissionId = readStored(key) || uuid();
    var previousSignature = readStored(sigKey);
    writeStored(key, submissionId);

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (submitButton && submitButton.disabled) { return; }

      var collected = collectAnswers(form);
      if (collected.missing.length) {
        setStatus(status, "还有必答题未作答，请先完成后再提交。", "error");
        var first = document.querySelector(
          '[data-testid="question-' + collected.missing[0].id + '"]'
        );
        if (first && first.scrollIntoView) { first.scrollIntoView({ block: "center" }); }
        return;
      }
      if (!consent || !consent.checked) {
        setStatus(status, "请先勾选同意参与，再提交问卷。", "error");
        return;
      }

      // B 追加（B 指南 §B05 第 5 条）：提交编号只对同一份内容有效。
      // 用户改了答案再提交时若仍用旧编号，服务端会判 IDEMPOTENCY_CONFLICT，
      // 用户得点两次才能成功；这里在本地就换新编号，409 只留作兜底。
      var signature = JSON.stringify(collected.answers);
      if (previousSignature && previousSignature !== signature) {
        submissionId = uuid();
        writeStored(key, submissionId);
        setStatus(status, "本次填写内容已变化，已按一次新的提交处理。", null);
      }
      writeStored(sigKey, signature);
      previousSignature = signature;

      var payload = {
        client_submission_id: submissionId,
        consent: true,
        answers: collected.answers
      };

      if (submitButton) { submitButton.disabled = true; }
      setStatus(status, "正在提交…", null);
      showFieldErrors(errorBox, null);

      request("POST", submitUrl, payload, { timeout: PUBLIC_TIMEOUT_MS })
        .then(function (data) {
          writeStored(key, submissionId);       // 固定本次提交编号，重发沿用同一编号
          writeStored(doneKey, (data && data.submitted_at) || new Date().toISOString());
          setStatus(status, data && data.duplicate
            ? "这份回答此前已提交过，未重复记录。"
            : "提交成功，感谢参与。", "ok");
          showSubmissionSuccess(form, data && data.submitted_at);
        })
        .catch(function (error) {
          if (error && error.code === "IDEMPOTENCY_CONFLICT") {
            // 同一编号已用于不同内容：换新编号，用户重新提交即可
            submissionId = uuid();
            writeStored(key, submissionId);
            writeStored(sigKey, "");
            previousSignature = null;
            setStatus(status, "本次填写内容已变更，已准备重新提交，请再点一次提交。", "error");
          } else {
            // B 追加（B 指南 §B05 第 7 条）：字段级错误必须落到题目上，不能只说"填写内容有误"
            showFieldErrors(errorBox, error, questions);
            var fields = (error && error.fields) || {};
            var count = Object.keys(fields).length;
            setStatus(status, count
              ? "提交失败：有 " + count + " 处需要修正，请按下方提示修改后重试。"
              : "提交失败：" + messageFor(error), "error");
            var firstId = firstErrorQuestion(error, questions);
            if (firstId) {
              var node = document.querySelector('[data-testid="question-' + firstId + '"]');
              if (node && node.scrollIntoView) { node.scrollIntoView({ block: "center" }); }
            }
          }
          if (submitButton) { submitButton.disabled = false; }   // 保留已填答案，允许重试
        });
    });
  }

  /* ------------------------------------------------------------------ *
   * 项目管理三页（B 追加）
   *   projects.html      GET /
   *   project_new.html   GET /projects/new
   *   workspace.html     GET /projects/{project_id}
   *
   * A 侧只需要三个路由渲染模板；三页的数据全部走已冻结的 JSON API，
   * 因此不需要任何额外 context，也不需要 bjtime / status_cn 等 Jinja 过滤器。
   * 状态与时间都在前端按同一口径换算（库里存 UTC，页面显示北京时间）。
   * ------------------------------------------------------------------ */

  var STATUS_CN = { draft: "草稿", planned: "已出方案", collecting: "收反馈中", reviewed: "已复盘" };
  var KIND_CN = { real: "真实实验", test: "测试样例" };
  var TOOL_CN = {
    make_validation_plan: "制定验证计划",
    create_experiment: "创建实验草稿",
    analyze_experiment: "读取反馈并复盘"
  };
  var RUN_CN = { running: "进行中", succeeded: "成功", failed: "失败" };

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }

  function byTestId(id) { return document.querySelector('[data-testid="' + id + '"]'); }

  /** UTC ISO8601 → 北京时间字符串（总纲 §3.1：库里存 UTC，页面显示北京时间） */
  function beijingTime(value) {
    if (!value) { return "—"; }
    var date = new Date(value);
    if (isNaN(date.getTime())) { return String(value); }
    try {
      return new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", hour12: false
      }).format(date);
    } catch (error) {
      return String(value);
    }
  }

  /** 把页面里所有 [data-utc] 转成北京时间（服务端渲染的时间占位也走这里） */
  function localizeTimes(root) {
    Array.prototype.forEach.call((root || document).querySelectorAll("[data-utc]"), function (node) {
      var iso = node.getAttribute("data-utc");
      if (iso) { node.textContent = beijingTime(iso); }
    });
  }

  function statusTag(status) {
    return el("span", "tag tag-" + status, STATUS_CN[status] || status);
  }

  function kindTag(kind) {
    return el("span", "tag tag-" + kind, KIND_CN[kind] || kind);
  }

  function definitionRow(list, term, value) {
    list.appendChild(el("dt", null, term));
    list.appendChild(el("dd", null, value === "" || value === null || value === undefined ? "—" : value));
  }

  function clearNode(node) {
    while (node && node.firstChild) { node.removeChild(node.firstChild); }
  }

  /* ---------------- 项目列表 ---------------- */

  function renderProjectRows(tbody, projects) {
    clearNode(tbody);
    projects.forEach(function (project) {
      var row = document.createElement("tr");
      row.setAttribute("data-testid", "project-row-" + project.id);
      row.appendChild(el("td", null, project.name));

      var statusCell = document.createElement("td");
      var tag = statusTag(project.status);
      tag.setAttribute("data-testid", "status-" + project.id);
      statusCell.appendChild(tag);
      row.appendChild(statusCell);

      var kindCell = document.createElement("td");
      var kind = kindTag(project.data_kind);
      kind.setAttribute("data-testid", "data-kind-" + project.id);
      kindCell.appendChild(kind);
      row.appendChild(kindCell);

      var timeCell = document.createElement("td");
      var time = el("time", "muted", beijingTime(project.created_at));
      time.setAttribute("datetime", project.created_at || "");
      timeCell.appendChild(time);
      row.appendChild(timeCell);

      var openCell = document.createElement("td");
      var link = el("a", null, "打开工作台");
      link.setAttribute("href", "/projects/" + encodeURIComponent(project.id));
      link.setAttribute("data-testid", "project-open-" + project.id);
      openCell.appendChild(link);
      row.appendChild(openCell);

      tbody.appendChild(row);
    });
  }

  function initProjectsPage(root) {
    var tbody = byTestId("project-list-body");
    if (!tbody) { return; }
    var empty = byTestId("project-empty");
    var status = byTestId("projects-status");

    function show(projects) {
      var list = projects || [];
      renderProjectRows(tbody, list);
      if (empty) { empty.hidden = list.length > 0; }
      if (status && list.length) { status.textContent = "共 " + list.length + " 个项目。"; }
      localizeTimes(root);
    }

    // 服务端已经渲染过行就不再重复取数；否则由前端补齐（A 不传 context 也能用）
    if (tbody.children && tbody.children.length) { localizeTimes(root); return; }
    request("GET", "/api/projects", undefined, { admin: true })
      .then(show)
      .catch(function (error) {
        setStatus(status, "读取项目列表失败：" + messageFor(error), "error");
        if (empty) { empty.hidden = false; }
      });
  }

  /* ---------------- 新建项目 ---------------- */

  /** 本地先挡明显错误（不替代服务端校验，只省一次往返；范围与 app/schemas.py 一致） */
  function validateProject(payload) {
    var fields = {};
    var limits = { name: 80, idea: 2000, target_users: 500, skills: 500, validation_goal: 500 };
    Object.keys(limits).forEach(function (key) {
      if (!payload[key]) { fields[key] = "不能为空"; }
      else if (payload[key].length > limits[key]) { fields[key] = "最多 " + limits[key] + " 字"; }
    });
    if (!(payload.budget_cny >= 0) || payload.budget_cny > 100000) {
      fields.budget_cny = "预算必须是 0～100000 之间的数字";
    }
    if (payload.data_kind !== "real" && payload.data_kind !== "test") {
      fields.data_kind = "必须是 real 或 test";
    }
    return fields;
  }

  function initProjectNewPage(root) {
    var form = document.getElementById("project-form");
    if (!form) { return; }
    var status = byTestId("project-status");
    var errorBox = byTestId("field-errors");
    var submitButton = byTestId("project-create");

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (submitButton && submitButton.disabled) { return; }

      var payload = {
        name: valueOf(form, "name").trim(),
        idea: valueOf(form, "idea").trim(),
        target_users: valueOf(form, "target_users").trim(),
        skills: valueOf(form, "skills").trim(),
        budget_cny: valueOf(form, "budget_cny").trim() === "" ? NaN : Number(valueOf(form, "budget_cny")),
        validation_goal: valueOf(form, "validation_goal").trim(),
        data_kind: valueOf(form, "data_kind")
      };

      var local = validateProject(payload);
      if (Object.keys(local).length) {
        showFieldErrors(errorBox, { fields: local });
        setStatus(status, "创建失败：有 " + Object.keys(local).length + " 处需要修正。", "error");
        return;
      }

      if (submitButton) { submitButton.disabled = true; }
      setStatus(status, "正在创建…", null);
      showFieldErrors(errorBox, null);

      request("POST", "/api/projects", payload, { admin: true, timeout: ADMIN_TIMEOUT_MS })
        .then(function (data) {
          setStatus(status, "创建成功，正在进入工作台…", "ok");
          window.location.href = "/projects/" + encodeURIComponent(data.project_id);
        })
        .catch(function (error) {
          showFieldErrors(errorBox, error);
          setStatus(status, "创建失败：" + messageFor(error), "error");
          if (submitButton) { submitButton.disabled = false; }   // 保留已填内容，允许改后重试
        });
    });
    localizeTimes(root);
  }

  /* ---------------- 工作台 ---------------- */

  function projectIdFromLocation() {
    var match = /^\/projects\/([^/?#]+)/.exec(window.location.pathname || "");
    return match ? decodeURIComponent(match[1]) : "";
  }

  /* ---------------- 工作台（服务端渲染 + 三块实时区域） ----------------
   * 首屏由 workspace.html 用 A 传来的 context 渲染；这里只负责必须实时取数的三块：
   *   工具运行日志（/api/projects/{id}/runs + /api/runs/{id}）
   *   反馈统计（/api/experiments/{id}/statistics，A06）
   *   原始回答（/api/experiments/{id}/responses，A06）
   * 生成方案与发布成功后整页重载，避免"服务端渲染 + 前端渲染"两套结果不一致。
   */

  function renderToolTable(tools) {
    var table = document.createElement("table");
    table.className = "run-tools";
    var head = document.createElement("tr");
    ["工具", "状态", "开始", "结束", "结果 / 错误"].forEach(function (label) {
      head.appendChild(el("th", null, label));
    });
    table.appendChild(head);
    tools.forEach(function (tool) {
      var row = document.createElement("tr");
      row.setAttribute("data-testid", "tool-" + tool.id);
      row.appendChild(el("td", null, TOOL_CN[tool.tool_name] || tool.tool_name));
      row.appendChild(el("td", "tool-status-" + tool.status, RUN_CN[tool.status] || tool.status));
      row.appendChild(el("td", "mono", beijingTime(tool.started_at)));
      row.appendChild(el("td", "mono", tool.finished_at ? beijingTime(tool.finished_at) : "—"));
      var result = tool.error_code;
      if (!result && tool.output_ref) {
        if (tool.output_ref.experiment_id) {
          result = "草稿 " + String(tool.output_ref.experiment_id).slice(0, 8) + " · "
            + (tool.output_ref.question_count || "?") + " 题";
        } else {
          var parts = [];
          if (tool.output_ref.model_call && tool.output_ref.model_call.model) {
            parts.push(String(tool.output_ref.model_call.model));
          }
          if (tool.output_ref.hypothesis_count) { parts.push(tool.output_ref.hypothesis_count + " 条假设"); }
          if (tool.output_ref.question_count) { parts.push(tool.output_ref.question_count + " 题"); }
          result = parts.length ? parts.join(" · ") : "已记录";
        }
      }
      row.appendChild(el("td", tool.error_code ? "tool-status-failed" : "mono", result || "—"));
      table.appendChild(row);
    });
    return table;
  }

  function renderRuns(logs, runs) {
    clearNode(logs);
    logs.setAttribute("data-rendered-by", "js");
    if (!runs || !runs.length) {
      var empty = el("p", "muted", "还没有运行记录。");
      empty.setAttribute("data-testid", "runs-empty");
      logs.appendChild(empty);
      return;
    }
    runs.forEach(function (run) {
      var box = el("div", "run", null);
      box.setAttribute("data-testid", "run-" + run.id);
      var head = el("p", "run-head", null);
      head.appendChild(el("span", "tag tag-" + run.status, RUN_CN[run.status] || run.status));
      head.appendChild(document.createTextNode(
        " · " + (run.action === "plan" ? "方案生成" : "反馈复盘")
        + " · " + beijingTime(run.started_at)
        + (run.finished_at ? " → " + beijingTime(run.finished_at) : "（进行中）")
        + " · " + run.id));
      box.appendChild(head);
      if (run.error_code) {
        box.appendChild(el("p", "notice notice-error",
          run.error_code + (run.error_message ? "：" + run.error_message : "")));
      }
      var holder = el("p", "muted", "工具调用加载中…");
      box.appendChild(holder);
      logs.appendChild(box);

      // 工具明细只存在于 GET /api/runs/{run_id}
      request("GET", "/api/runs/" + encodeURIComponent(run.id), undefined, { admin: true })
        .then(function (detail) {
          var tools = (detail && detail.tool_calls) || [];
          if (holder.parentNode === box) { box.removeChild(holder); }
          if (!tools.length) {
            box.appendChild(el("p", "muted", "本次运行没有记录工具调用。"));
            return;
          }
          box.appendChild(renderToolTable(tools));
        })
        .catch(function () {
          holder.textContent = "工具明细读取失败。";
        });
    });
  }

  /** 反馈统计（A06）。分母一律用"该题有效作答份数"，零分母显示暂无数据而不是 0%。 */
  function renderStatistics(config) {
    var body = byTestId("statistics-body");
    var countNode = byTestId("response-count");
    var cutoffNode = byTestId("cutoff-response-id");
    var statusNode = byTestId("statistics-status");
    if (!body) { return; }

    if (!config.experimentId) {
      setStatus(statusNode, "还没有实验，无法统计。", null);
      return;
    }
    if (!config.published) {
      setStatus(statusNode, "实验尚未发布，请先检查草稿并发布，再查看反馈统计。", null);
      return;
    }

    var titles = {};
    (config.questions || []).forEach(function (question) { titles[question.id] = question.title; });

    request("GET", "/api/experiments/" + encodeURIComponent(config.experimentId) + "/statistics",
            undefined, { admin: true })
      .then(function (stats) {
        clearNode(body);
        var total = stats.response_count || 0;
        if (countNode) { countNode.textContent = "有效回答份数：" + total + " 份"; }
        if (cutoffNode) {
          cutoffNode.textContent = "统计快照截止回答编号：" + (stats.cutoff_response_id || 0);
        }
        setStatus(statusNode, total
          ? "统计完成，共 " + total + " 份回答。"
          : "目前还没有回答，所有题都显示「暂无数据」。", total ? "ok" : null);

        (stats.questions || []).forEach(function (question) {
          var box = el("div", "stat-question", null);
          box.setAttribute("data-testid", "stat-" + question.id);
          box.appendChild(el("h3", null,
            (titles[question.id] || question.id) + "（有效作答 " + question.answered_count + " 份）"));

          if (question.type === "text") {
            if (!(question.texts || []).length) {
              var none = el("p", "muted", "暂无数据（还没有人填写这道题）。");
              none.setAttribute("data-testid", "stat-empty-" + question.id);
              box.appendChild(none);
            } else {
              var list = el("ul", "feature-list", null);
              question.texts.forEach(function (item) {
                var li = el("li", null, item.text);
                li.setAttribute("data-testid", "stat-text-" + question.id + "-" + item.response_id);
                list.appendChild(li);
              });
              box.appendChild(list);
            }
          } else {
            var table = document.createElement("table");
            table.className = "data-table";
            var head = document.createElement("tr");
            ["选项", "计数", "百分比"].forEach(function (label) {
              head.appendChild(el("th", null, label));
            });
            table.appendChild(head);
            (question.options || []).forEach(function (option) {
              var row = document.createElement("tr");
              row.setAttribute("data-testid", "stat-option-" + question.id + "-" + option.id);
              row.appendChild(el("td", null, option.label));
              row.appendChild(el("td", null, option.count));
              row.appendChild(el("td", null,
                (option.percentage === null || option.percentage === undefined)
                  ? "暂无数据"
                  : Number(option.percentage).toFixed(2) + "%"));
              table.appendChild(row);
            });
            box.appendChild(table);
            box.appendChild(el("p", "muted",
              "百分比分母为该题有效作答份数 " + question.answered_count + " 份。"));
          }
          body.appendChild(box);
        });
      })
      .catch(function (error) {
        setStatus(statusNode, "统计读取失败：" + messageFor(error), "error");
      });
  }

  /** 原始回答（A06）：按需读取，只显示回答编号、答案与时间 */
  function renderResponses(config) {
    var body = byTestId("responses-body");
    var button = byTestId("responses-load");
    var status = byTestId("responses-status");
    if (!body || !button || !config.experimentId) { return; }

    button.addEventListener("click", function () {
      button.disabled = true;
      setStatus(status, "正在读取…", null);
      request("GET", "/api/experiments/" + encodeURIComponent(config.experimentId) + "/responses",
              undefined, { admin: true })
        .then(function (rows) {
          clearNode(body);
          var list = rows || [];
          setStatus(status, list.length ? "共 " + list.length + " 份回答。" : "还没有回答。",
                    list.length ? "ok" : null);
          list.forEach(function (row) {
            var box = el("div", "run", null);
            box.setAttribute("data-testid", "response-" + row.id);
            box.appendChild(el("p", "run-head",
              "回答 #" + row.id + " · " + beijingTime(row.created_at) + "（北京时间）"));
            var dl = el("dl", "service-meta", null);
            Object.keys(row.answers || {}).forEach(function (key) {
              dl.appendChild(el("dt", null, key));
              var value = row.answers[key];
              dl.appendChild(el("dd", null, Array.isArray(value) ? value.join("、") : value));
            });
            box.appendChild(dl);
            body.appendChild(box);
          });
        })
        .catch(function (error) { setStatus(status, "读取失败：" + messageFor(error), "error"); })
        .then(function () { button.disabled = false; });
    });
  }

  function initWorkspacePage(root) {
    var config = window.__FO_WORKSPACE__ || {};
    var projectId = config.projectId || projectIdFromLocation();
    if (!projectId) { return; }

    var statusBox = byTestId("ws-status");
    var errorBox = byTestId("field-errors");
    var planButton = byTestId("plan-generate");
    var publishButton = byTestId("experiment-publish");
    var reviewButton = byTestId("review-generate");
    var reviewStatus = byTestId("review-status");
    var stageStatus = byTestId("ws-stage-status");
    var logs = byTestId("run-log");

    function say(text, kind) {
      setStatus(statusBox, text, kind);
      setStatus(stageStatus, text, kind);
    }

    function refreshActions() {
      return request("GET", "/api/projects/" + encodeURIComponent(projectId), undefined, { admin: true })
        .then(function (detail) {
          if (config.projectStatus && detail.project && detail.project.status !== config.projectStatus) {
            window.location.reload();
          }
          if (detail.running_run_id) {
            setStatus(stageStatus, "AI 任务仍在执行，请等待完成后再编辑、生成或发布。", null);
          }
          var locked = Boolean(detail.running_run_id || (detail.experiment && detail.experiment.published_at));
          if (planButton) { planButton.disabled = locked; }
          if (publishButton) {
            publishButton.disabled = locked;
            publishButton.hidden = Boolean(detail.experiment && detail.experiment.published_at);
          }
          if (reviewButton) {
            reviewButton.disabled = Boolean(detail.running_run_id || !detail.experiment || !detail.experiment.published_at);
          }
          return request("GET", "/api/projects/" + encodeURIComponent(projectId) + "/runs", undefined, { admin: true });
        })
        .then(function (runs) { if (logs) { renderRuns(logs, runs); } })
        .catch(function () {
          setStatus(stageStatus, "暂时无法确认任务是否结束，请刷新页面查看最新状态。", "error");
          if (planButton) { planButton.disabled = true; }
          if (publishButton) { publishButton.disabled = true; }
          if (reviewButton) { reviewButton.disabled = true; }
        });
    }

    if (planButton) {
      planButton.addEventListener("click", function () {
        if (config.experimentId &&
            !window.confirm("重新生成会整体替换当前未发布的草稿（服务页文案与问卷），确定继续？")) {
          return;
        }
        planButton.disabled = true;
        if (publishButton) { publishButton.disabled = true; }
        if (reviewButton) { reviewButton.disabled = true; }
        say("正在调用模型生成验证方案，通常需要几十秒，请不要重复点击…", null);
        showFieldErrors(errorBox, null);
        request("POST", "/api/projects/" + encodeURIComponent(projectId) + "/plan", {},
                { admin: true, timeout: ADMIN_TIMEOUT_MS })
          .then(function () { window.location.reload(); })
          .catch(function (error) {
            showFieldErrors(errorBox, error);
            say("生成失败：" + messageFor(error), "error");
            return refreshActions();
          });
      });
    }

    if (publishButton) {
      publishButton.addEventListener("click", function () {
        if (!config.experimentId) { return; }
        if (!window.confirm("发布后服务页文案与问卷题目立即锁定，公开链接对访客可用。"
          + "发布不会被生成计划自动触发，确定现在发布？")) { return; }
        publishButton.disabled = true;
        if (planButton) { planButton.disabled = true; }
        if (reviewButton) { reviewButton.disabled = true; }
        say("正在发布…", null);
        request("POST", "/api/experiments/" + encodeURIComponent(config.experimentId) + "/publish", {},
                { admin: true, timeout: ADMIN_TIMEOUT_MS })
          .then(function () { window.location.reload(); })
          .catch(function (error) {
            say("发布失败：" + messageFor(error), "error");
            return refreshActions();
          });
      });
    }

    // A07：生成复盘。报告保存工作流读取反馈时的统计快照，之后新增回答不会改动它。
    if (reviewButton) {
      reviewButton.addEventListener("click", function () {
        if (!config.experimentId) { return; }
        if (!config.published) {
          setStatus(reviewStatus, "实验尚未发布，请先检查草稿并发布，再生成复盘。", "error");
          return;
        }
        if (!window.confirm("复盘会读取当前全部回答并调用模型生成报告。"
          + "报告保存读取反馈时的统计快照，之后新增回答不会改变它的数字，只会提示「已有新反馈」。"
          + "确定继续？")) { return; }
        reviewButton.disabled = true;
        if (planButton) { planButton.disabled = true; }
        if (publishButton) { publishButton.disabled = true; }
        setStatus(reviewStatus, "正在读取反馈并复盘，通常需要几十秒，请不要重复点击…", null);
        setStatus(stageStatus, "AI 正在复盘，请等待完成后再操作。", null);
        request("POST", "/api/projects/" + encodeURIComponent(projectId) + "/review", {},
                { admin: true, timeout: ADMIN_TIMEOUT_MS })
          .then(function (data) {
            setStatus(reviewStatus, "复盘完成（" + ((data && data.response_count) || 0)
              + " 份回答），正在刷新报告…", "ok");
            window.location.reload();
          })
          .catch(function (error) {
            // 请求失败不代表运行锁已释放（如 409 或网络中断），重新读取后端状态。
            setStatus(stageStatus, "复盘失败：" + messageFor(error), "error");
            if (error && error.run_id) {
              setStatus(reviewStatus, "复盘失败：" + messageFor(error)
                + "（运行编号 " + error.run_id + "，可在上方运行日志查看）", "error");
            } else {
              setStatus(reviewStatus, "复盘失败：" + messageFor(error), "error");
            }
            return refreshActions();
          });
      });
    }

    ["service", "survey"].forEach(function (kind) {
      var button = byTestId("copy-" + kind + "-url");
      var target = byTestId(kind + "-url");
      var copyStatus = byTestId("copy-" + kind + "-status");
      if (!button || !target) { return; }
      button.addEventListener("click", function () {
        var url = (target.value || target.textContent || "").trim();
        if (!url) { setStatus(copyStatus, "还没有可复制的链接，请先发布实验。", "error"); return; }
        function manualCopy() {
          try {
            target.focus();
            target.select();
            target.setSelectionRange(0, url.length);
            if (document.activeElement !== target || target.selectionStart !== 0 || target.selectionEnd !== url.length) {
              throw new Error("Link selection failed");
            }
            setStatus(copyStatus, "自动复制不可用，已选中链接。请按 Ctrl+C（Mac 按 ⌘C）或在手机上长按复制。", null);
          } catch (error) {
            setStatus(copyStatus, "无法自动选中链接。请点按链接框手动选择并复制，或打开页面后复制地址栏中的链接。", "error");
          }
        }
        setStatus(copyStatus, "正在复制…", null);
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            Promise.resolve(navigator.clipboard.writeText(url)).then(function () {
              setStatus(copyStatus, "链接已复制。", "ok");
            }, manualCopy);
          } else {
            manualCopy();
          }
        } catch (error) {
          manualCopy();
        }
      });
    });

    localizeTimes(root);

    if (logs) {
      request("GET", "/api/projects/" + encodeURIComponent(projectId) + "/runs",
              undefined, { admin: true })
        .then(function (runs) { renderRuns(logs, runs); })
        .catch(function (error) {
          clearNode(logs);
          logs.appendChild(el("p", "muted", "运行日志读取失败：" + messageFor(error)));
        });
    }

    renderStatistics(config);
    renderResponses(config);
  }

  /* ------------------------------------------------------------------ *
   * 启动
   * ------------------------------------------------------------------ */

  function initAuthPage(root) {
    var form = root.getElementById("auth-form");
    if (!form) { return; }
    var registering = form.getAttribute("data-auth-action") === "register";
    var button = byTestId("auth-submit");
    var status = byTestId("auth-status");
    var errors = byTestId("auth-errors");
    if (!registering && /(?:^|\?)registered=1(?:&|$)/.test(window.location.search || "")) {
      setStatus(status, "注册成功，请登录。", "ok");
    }
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (button.disabled) { return; }
      var fields = form.elements;
      if (registering && fields.password.value !== fields.password_confirmation.value) {
        setStatus(status, "两次输入的密码不一致。", "error");
        return;
      }
      var payload = { username: fields.username.value.trim(), password: fields.password.value };
      if (registering) { payload.email = fields.email.value.trim() || null; }
      button.disabled = true;
      setStatus(status, registering ? "正在创建账号…" : "正在登录…", null);
      showFieldErrors(errors, { fields: {} });
      request("POST", "/api/auth/" + (registering ? "register" : "login"), payload, { admin: true })
        .then(function () {
          form.reset();
          window.location.href = registering ? "/login?registered=1" : "/projects";
        })
        .catch(function (error) {
          fields.password.value = "";
          if (registering) { fields.password_confirmation.value = ""; }
          showFieldErrors(errors, error);
          setStatus(status, (error && error.message) || messageFor(error), "error");
          button.disabled = false;
        });
    });
  }

  function initLogout() {
    var button = byTestId("auth-logout");
    if (!button) { return; }
    button.addEventListener("click", function () {
      if (button.disabled) { return; }
      button.disabled = true;
      request("POST", "/api/auth/logout", {}, { admin: true })
        .then(function () { window.location.href = "/"; })
        .catch(function (error) {
          setStatus(byTestId("logout-status"), messageFor(error), "error");
          button.disabled = false;
        });
    });
  }

  function boot() {
    var body = document.body;
    if (body.classList.contains("page-auth")) { initAuthPage(document); }
    if (body.classList.contains("page-admin") || body.classList.contains("page-landing")
        || body.classList.contains("page-profile")) { initLogout(); }
    if (body.classList.contains("page-admin")) { initEditPage(document); }
    if (body.classList.contains("page-survey")) { initSurveyPage(document); }
    // B 追加：项目管理三页
    if (body.classList.contains("page-projects")) { initProjectsPage(document); }
    if (body.classList.contains("page-project-new")) { initProjectNewPage(document); }
    if (body.classList.contains("page-workspace")) { initWorkspacePage(document); }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
}());
