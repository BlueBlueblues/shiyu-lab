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
  var ADMIN_TIMEOUT_MS = 170000;   // 工作流 80 秒，留出响应传输余量。
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

  /** 项目表单字段名 → 页面上的中文标签。
   *  错误提示直接显示 name / idea 这类内部字段名，用户看不懂；
   *  这里让提示复述用户实际看到的标签（与 project_new.html 的 <label> 保持一致）。 */
  var PROJECT_FIELD_LABELS = {
    name: "项目名称",
    idea: "想验证的想法",
    target_users: "目标用户",
    skills: "团队现有能力",
    budget_cny: "预算（元）",
    validation_goal: "验证目标",
    data_kind: "数据用途"
  };

  function fieldLabel(key) {
    return PROJECT_FIELD_LABELS[key] || key;
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
      /* 问卷按题号呈现（第 N 题），项目表单按页面标签呈现（如「项目名称」）。
         两者都不该把 name / q1 这类内部字段名直接甩给用户。 */
      var label = questions && questions.length ? questionLabel(key, questions) : fieldLabel(key);
      item.textContent = label + "：" + fields[key];
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
    /* 用户勾上同意后应立即撤掉错误高亮：错误态描述的是「现在缺什么」，
       缺的东西补上了还留着红框，会让人以为仍未满足条件。 */
    if (consent) {
      consent.addEventListener("change", function () {
        var row = consent.closest(".consent-row");
        if (!row) { return; }
        if (consent.checked) { row.classList.remove("is-error"); }
      });
    }

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
        /* 漏答必答题时会滚动到那一题；漏勾同意时同样要给出落点，
           否则用户（尤其手机上滚到页面底部点提交的人）只看到一句错误提示，
           不知道要回到哪里勾。这里把提示、可视落点与焦点一起补上。 */
        setStatus(status, "请先勾选同意参与，再提交问卷。", "error");
        if (consent) {
          var consentRow = consent.closest(".consent-row") || consent;
          consentRow.classList.add("is-error");
          if (consentRow.scrollIntoView) { consentRow.scrollIntoView({ block: "center" }); }
          try { consent.focus({ preventScroll: true }); } catch (error) { /* 老浏览器忽略 */ }
        }
        return;
      }
      if (consent) {
        var row = consent.closest(".consent-row");
        if (row) { row.classList.remove("is-error"); }
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
    make_validation_plan: "识玉 · 制定验证计划",
    create_experiment: "琢玉 · 创建实验草稿",
    analyze_experiment: "鉴玉 · 读取反馈并复盘",
    propose_iteration: "攻玉 · 制定迭代方案"
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

  /**
   * 项目列表页的「视图切换」（缺陷修复）
   * 原行为：`#my-projects` 只是锚点滚动，松手后 hero / 故事条仍在同一屏，用户会以为「怎么又回来了」。
   * 现行为：hash = #my-projects 时当屏只显示「我进行中的验证」，其余区块整体隐藏；「返回」清掉 hash。
   * 走 location.hash，所以浏览器后退/前进、刷新、深链接都能回到正确视图。
   * 只用既有 class 与 [hidden] 属性，不新增 CSS 类，不改动任何既有 data-testid。
   */
  function initProjectsView() {
    var target = byTestId("my-projects");
    if (!target) { return; }
    var hash = "#my-projects";
    var main = target.parentNode;
    if (!main || !main.children) { return; }
    var nodes = Array.prototype.slice.call(main.children);
    /* 记住各区块原本的 hidden，返回时按原样恢复，不覆盖模板里已有的 hidden */
    var originalHidden = nodes.map(function (node) { return Boolean(node.hidden); });
    var lastApplied = null;
    function apply() {
      var projectOnly = (window.location.hash || "") === hash;
      /* 只有视图真的变了才动视口。hashchange 与定时器都可能打到这里，
         没有这个判断会在用户阅读时反复把页面拉回顶部。 */
      if (lastApplied === projectOnly) { return; }
      lastApplied = projectOnly;
      nodes.forEach(function (node, index) {
        if (node === target) { return; }
        node.hidden = projectOnly ? true : originalHidden[index];
      });
      target.hidden = false;
      if (projectOnly) { window.scrollTo(0, 0); }
    }
    window.addEventListener("hashchange", apply);
    apply();
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
      var input = tool.input_summary || {};
      var label = TOOL_CN[tool.tool_name] || tool.tool_name;
      if (input.execution === "model") { label = (input.agent || "AI角色") + " · 独立模型推理"; }
      else if (input.execution === "program") { label = (input.agent || "程序") + " · 程序工具（非AI推理）"; }
      row.appendChild(el("td", null, label));
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
        " · " + ({plan: "方案生成", review: "证据复盘", iterate: "迭代提案", build: "确认后构建"}[run.action] || run.action)
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
          if (planButton) { planButton.disabled = locked || Boolean(config.planLocked); }
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
      bindCopyLink(byTestId("copy-" + kind + "-url"), byTestId(kind + "-url"),
                   byTestId("copy-" + kind + "-status"), "还没有可复制的链接，请先发布实验。");
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

  function initIterationPage() {
    localizeTimes(document);
    var status = byTestId("iteration-status");
    var generate = byTestId("iteration-generate");
    var confirm = byTestId("iteration-confirm");
    function execute(button, url, body, prompt, complete) {
      if (!window.confirm(prompt)) { return; }
      button.disabled = true;
      setStatus(status, "正在执行，请不要重复点击…", null);
      request("POST", url, body, { admin: true, timeout: ADMIN_TIMEOUT_MS })
        .then(complete)
        .catch(function (error) {
          setStatus(status, messageFor(error), "error");
          button.disabled = false;
        });
    }
    if (generate) { generate.addEventListener("click", function () {
      execute(generate, generate.dataset.iterationUrl, {}, "根据已保存的真实复盘生成下一轮待审核方案？", function (result) {
        window.location.href = "/projects/" + encodeURIComponent(generate.dataset.projectId) + "/iterations/" + encodeURIComponent(result.proposal_id);
      });
    }); }
    if (confirm) { confirm.addEventListener("click", function () {
      var approved = {confirmed:true};
      var goals = document.getElementById("approved-goals");
      var constraints = document.getElementById("approved-constraints");
      if (goals) { approved.revised_goals = goals.value.split(/\r?\n/).map(function(x){return x.trim();}).filter(Boolean); }
      if (constraints) { approved.revised_constraints = constraints.value.split(/\r?\n/).map(function(x){return x.trim();}).filter(Boolean); }
      execute(confirm, confirm.dataset.confirmUrl, approved, "确认上方所有修改和原因，并创建独立的下一轮草稿？发布仍需另行审核。", function (result) {
        window.location.href = "/experiments/" + encodeURIComponent(result.experiment_id) + "/edit";
      });
    }); }
    var reject = byTestId("iteration-reject");
    if (reject) { reject.addEventListener("click", function () {
      var reason = window.prompt("拒绝策略的原因（将保存在人工决策记录中）");
      if (!reason || !reason.trim()) { return; }
      execute(reject, reject.dataset.rejectUrl, {reason:reason.trim()}, "拒绝此策略？不会创建新实验。", function(){window.location.reload();});
    }); }
    document.querySelectorAll("[data-round-review]").forEach(function (button) {
      button.addEventListener("click", function () {
        execute(button, button.dataset.roundReview, {}, "读取本轮反馈并保存新的复盘快照？", function () { window.location.reload(); });
      });
    });
  }

  /* ------------------------------------------------------------------ *
   * 启动
   * ------------------------------------------------------------------ */

  function bindCopyLink(button, target, copyStatus, emptyMessage) {
    if (!button || !target) { return; }
    button.addEventListener("click", function () {
      var url = (target.value || target.textContent || "").trim();
      if (!url) { setStatus(copyStatus, emptyMessage, "error"); return; }
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
        } else { manualCopy(); }
      } catch (error) { manualCopy(); }
    });
  }

  function initInvitationsPage() {
    var form = byTestId("invitation-form");
    if (!form) { return; }
    var button = byTestId("invitation-create");
    var status = byTestId("invitation-status");
    var list = byTestId("invitation-list");
    var listStatus = byTestId("invitation-list-status");
    var result = byTestId("invitation-result");
    var target = byTestId("invitation-link");
    var copyStatus = byTestId("invitation-copy-status");
    var currentId = null;
    var labels = { active: "可使用", used: "已使用", expired: "已过期", revoked: "已撤销" };
    bindCopyLink(byTestId("invitation-copy"), target, copyStatus, "请先生成邀请链接。");

    function loadList() {
      setStatus(listStatus, "正在读取邀请记录…", null);
      return request("GET", "/api/admin/invitations", undefined, { admin: true })
        .then(function (rows) {
          clearNode(list);
          setStatus(listStatus, rows.length ? "" : "还没有邀请记录。", null);
          rows.forEach(function (row) {
            var box = el("div", "run", null);
            box.setAttribute("data-testid", "invitation-row-" + row.id);
            box.setAttribute("data-status", row.status);
            box.appendChild(el("p", null, labels[row.status] + " · 已使用 " + row.used_count + "/" + row.max_uses + " 次"));
            box.appendChild(el("p", "muted", "过期时间：" + beijingTime(new Date(row.expires_at * 1000).toISOString()) + "（北京时间）"));
            var revoke = el("button", "button button-ghost", "撤销邀请");
            revoke.type = "button";
            revoke.disabled = row.status !== "active";
            revoke.setAttribute("data-testid", "invitation-revoke-" + row.id);
            revoke.addEventListener("click", function () {
              if (!window.confirm("撤销后，此邀请链接将无法再注册。确定撤销？")) { return; }
              revoke.disabled = true;
              request("POST", "/api/admin/invitations/" + encodeURIComponent(row.id) + "/revoke", {}, { admin: true })
                .then(function () {
                  if (currentId === row.id) { target.value = ""; result.hidden = true; currentId = null; }
                  setStatus(status, "邀请已撤销。", "ok");
                  return loadList();
                })
                .catch(function (error) { revoke.disabled = false; setStatus(status, "撤销失败：" + messageFor(error), "error"); });
            });
            box.appendChild(revoke);
            list.appendChild(box);
          });
        })
        .catch(function (error) { setStatus(listStatus, "邀请记录读取失败：" + messageFor(error), "error"); });
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (button.disabled || !form.reportValidity()) { return; }
      button.disabled = true;
      setStatus(status, "正在生成邀请链接…", null);
      request("POST", "/api/admin/invitations", { expires_hours: Number(form.elements.expires_hours.value) }, { admin: true })
        .then(function (data) {
          currentId = data.id;
          target.value = data.link;
          byTestId("invitation-open").href = data.link;
          result.hidden = false;
          setStatus(copyStatus, "", null);
          setStatus(status, "邀请链接已生成，只能成功注册一次。请及时复制并发给指定的人。", "ok");
          return loadList();
        })
        .catch(function (error) { setStatus(status, "生成失败：" + messageFor(error), "error"); })
        .then(function () { button.disabled = false; });
    });
    byTestId("invitation-refresh").addEventListener("click", loadList);
    loadList();
  }

  function initAuthPage(root) {
    var form = root.getElementById("auth-form");
    if (!form) { return; }
    var registering = form.getAttribute("data-auth-action") === "register";
    var button = byTestId("auth-submit");
    var status = byTestId("auth-status");
    var errors = byTestId("auth-errors");
    var codeFeedback = registering ? byTestId("code-feedback") : null;
    var passwordStatus = registering ? byTestId("password-status") : null;
    var confirmStatus = registering ? byTestId("password-confirmation-status") : null;
    /* 显示/隐藏密码（register.html / login.html 各一处）。
       手机上要处理两件事：
         1. 有些浏览器在按钮被重绘时会丢掉 click，这里额外监听 pointerup/touchend 兜底；
         2. 兜底事件先执行时，随后的 click 必须被忽略，否则两次切换互相抵消、等于没反应。
       所以用「最近处理时间」去重，而不是让两条路径各切一次。 */
    Array.prototype.forEach.call(root.querySelectorAll("[data-password-toggle]"), function (toggle) {
      var field = root.getElementById(toggle.getAttribute("aria-controls"));
      if (!field) { return; }
      var lastHandled = 0;
      function handle(event) {
        var now = Date.now();
        if (now - lastHandled < 500) { return; }   /* 同一次点击的第二个事件，忽略 */
        lastHandled = now;
        if (event && event.type === "touchend" && event.cancelable) {
          event.preventDefault();                  /* 不抢输入框焦点，避免键盘弹起把按钮顶走 */
        }
        var show = field.type === "password";
        field.type = show ? "text" : "password";
        toggle.setAttribute("aria-pressed", show ? "true" : "false");
        toggle.textContent = show ? "隐藏密码" : "显示密码";
      }
      toggle.addEventListener("click", handle);
      if ("ontouchend" in document) {
        toggle.addEventListener("touchend", handle, { passive: false });
      } else {
        toggle.addEventListener("pointerup", handle);
      }
    });
    /* 密码规则：与后端一致的 12 位下限，失焦即时校验 + 浏览器原生提示 */
    var passwordField = form.elements.password;
    var confirmField = form.elements.password_confirmation;
    var PASSWORD_RULE = "密码必须是 12 位及以上（最多 128 位）。";
    function checkPasswordRule() {
      if (!registering || !passwordField) { return true; }
      var value = passwordField.value;
      if (value && value.length < 12) {
        passwordField.setCustomValidity(PASSWORD_RULE);
        setStatus(passwordStatus, PASSWORD_RULE + "请再补 " + (12 - value.length) + " 位。", "error");
        return false;
      }
      passwordField.setCustomValidity("");
      if (value) { setStatus(passwordStatus, "长度符合要求。", "ok"); }
      else { setStatus(passwordStatus, "", null); }
      return true;
    }
    function checkConfirmRule() {
      if (!registering || !confirmField) { return true; }
      var mismatch = Boolean(confirmField.value) &&
        confirmField.value !== (passwordField ? passwordField.value : "");
      if (mismatch) {
        confirmField.setCustomValidity("两次输入的密码不一致。");
        setStatus(confirmStatus, "两次输入的密码不一致，请检查后重新输入。", "error");
        return false;
      }
      confirmField.setCustomValidity("");
      setStatus(confirmStatus, confirmField.value ? "两次输入一致。" : "", confirmField.value ? "ok" : null);
      return true;
    }
    if (registering && passwordField) {
      passwordField.addEventListener("blur", checkPasswordRule);
      passwordField.addEventListener("input", function () {
        passwordField.setCustomValidity("");
        if (passwordStatus && passwordStatus.classList.contains("is-error")) { checkPasswordRule(); }
        if (confirmField && confirmField.value) { checkConfirmRule(); }
      });
    }
    if (registering && confirmField) {
      confirmField.addEventListener("blur", checkConfirmRule);
      confirmField.addEventListener("input", function () {
        confirmField.setCustomValidity("");
        if (confirmStatus && confirmStatus.classList.contains("is-error")) { checkConfirmRule(); }
      });
    }
    if (registering && /(?:^|[?&])invite=/.test(window.location.search || "")) {
      var invite = new URLSearchParams(window.location.search).get("invite");
      if (invite !== null) {
        var cleanUrl = new URL(window.location.href);
        cleanUrl.searchParams.delete("invite");
        window.history.replaceState(null, "", cleanUrl.pathname + cleanUrl.search + cleanUrl.hash);
        if (/^[A-Za-z0-9_-]{43}$/.test(invite)) {
          form.elements.invitation.value = invite;
          setStatus(status, "已识别邀请链接，请填写邮箱并获取验证码。", null);
        } else {
          setStatus(status, "邀请链接不完整，请联系管理员获取新的链接。", "error");
        }
      }
    }
    function authMessage(error) {
      var invalid = (error && error.fields) || {};
      if (registering && invalid.email) { return "邮箱格式错误，请填写可接收验证码的邮箱。"; }
      if (registering && invalid.password) { return "密码须为 12–128 位。"; }
      if (registering && invalid.invitation) { return "邀请码为必填项，请输入管理员提供的完整邀请码。"; }
      if (registering && invalid.verification_code) { return "请输入收到的 6 位邮箱验证码。"; }
      return (error && error.message) || messageFor(error);
    }
    var sendCode = registering ? byTestId("auth-send-code") : null;
    var countdownTimer = null;
    if (sendCode) {
      sendCode.addEventListener("click", function () {
        if (sendCode.disabled) { return; }   // 发送中/倒计时期间不重复发请求
        var fields = form.elements;
        if (!fields.email.reportValidity() || !fields.invitation.reportValidity()) { return; }
        sendCode.disabled = true;
        sendCode.textContent = "正在发送…";
        setStatus(codeFeedback, "正在发送验证码…", null);
        setStatus(status, "正在发送验证码…", null);
        request("POST", "/api/auth/register/code", {
          email: fields.email.value.trim(), invitation: fields.invitation.value.trim()
        }, { admin: true }).then(function (data) {
          var total = Number(data && data.cooldown_seconds) > 0 ? Number(data.cooldown_seconds) : 60;
          setStatus(status, "验证码发送请求已受理，请检查邮箱及垃圾邮件文件夹。", "ok");
          if (countdownTimer) { window.clearTimeout(countdownTimer); countdownTimer = null; }
          var until = Date.now() + total * 1000;
          sendCode.textContent = "重新发送验证码";
          function countdown() {
            var seconds = Math.max(0, Math.ceil((until - Date.now()) / 1000));
            if (seconds > 0) {
              sendCode.textContent = seconds + " 秒后可重新发送";
              setStatus(codeFeedback,
                "验证码已发送，请查收邮箱（也看一眼垃圾邮件文件夹）；" + seconds + " 秒后可重新发送。", "ok");
              sendCode.disabled = true;
              countdownTimer = window.setTimeout(countdown, 1000);
            } else {
              countdownTimer = null;
              sendCode.textContent = "重新发送验证码";
              sendCode.disabled = false;
              setStatus(codeFeedback, "现在可以重新发送验证码。", null);
            }
          }
          countdown();
        }).catch(function (error) {
          showFieldErrors(errors, error);
          var message = error && (error.code === "TIMEOUT" || error.code === "NETWORK_ERROR")
            ? "发送结果未确认，请稍后再试；原验证码如仍有效可继续使用。" : authMessage(error);
          setStatus(codeFeedback, message, "error");
          setStatus(status, message, "error");
          sendCode.textContent = "重新发送验证码";
          sendCode.disabled = false;   // 失败立即恢复可点
        });
      });
    }
    if (!registering && /(?:^|\?)registered=1(?:&|$)/.test(window.location.search || "")) {
      var autoLoginFailed = /(?:^|[?&])autologin=failed(?:&|$)/.test(window.location.search || "");
      setStatus(status, autoLoginFailed
        ? "注册成功，账号已创建；自动登录没成功，请用刚才的用户名和密码登录。"
        : "注册成功，请登录。", "ok");
    }
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (button.disabled) { return; }
      var fields = form.elements;
      if (registering && !checkPasswordRule()) { fields.password.focus(); return; }
      if (registering && !checkConfirmRule()) { fields.password_confirmation.focus(); return; }
      if (registering && fields.password.value !== fields.password_confirmation.value) {
        setStatus(passwordStatus, "两次输入的密码不一致，请检查后重新输入。", "error");
        setStatus(status, "两次输入的密码不一致。", "error");
        return;
      }
      var payload = { username: fields.username.value.trim(), password: fields.password.value };
      if (registering) {
        payload.email = fields.email.value.trim();
        payload.invitation = fields.invitation.value.trim();
        payload.verification_code = fields.verification_code.value.trim();
      }
      /* 注册成功后用同一份凭据自动登录，因此先把凭据留存，reset 后再放入登录表单 */
      var credentials = { username: payload.username, password: payload.password };
      button.disabled = true;
      setStatus(status, registering ? "正在创建账号…" : "正在登录…", null);
      showFieldErrors(errors, { fields: {} });
      request("POST", "/api/auth/" + (registering ? "register" : "login"), payload, { admin: true })
        .then(function () {
          if (!registering) { window.location.href = "/projects"; return; }
          fields.invitation.value = "";
          fields.verification_code.value = "";
          form.reset();
          setStatus(status, "注册成功，正在自动登录…", "ok");
          /* 注册已成功：即使自动登录失败也只提示「请登录」，绝不让用户以为注册失败 */
          return request("POST", "/api/auth/login", credentials, { admin: true })
            .catch(function (loginError) {
              /* 打标记区分「注册失败」与「注册成功但自动登录失败」，后者不能走失败分支清空表单 */
              if (loginError && typeof loginError === "object") { loginError.__autoLogin = true; }
              throw loginError;
            });
        })
        .then(function () {
          if (registering) { window.location.href = "/projects"; }
        })
        .catch(function (error) {
          if (registering && error && error.__autoLogin) {
            /* 注册已成功、只是自动登录没成功：不能走下面的失败分支，否则会把刚填好的账号密码清掉 */
            button.disabled = false;
            window.location.href = "/login?registered=1&autologin=failed";
            return;
          }
          fields.password.value = "";
          if (registering) { fields.password_confirmation.value = ""; }
          if (registering) { setStatus(passwordStatus, "", null); setStatus(confirmStatus, "", null); }
          showFieldErrors(errors, error);
          setStatus(status, authMessage(error), "error");
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

  function initAgentDashboard() {
    var panel = byTestId("agent-dashboard");
    if (!panel) { return; }
    var pid = panel.dataset.projectId;
    var states = {awaiting_build:"推理已完成，草稿尚未构建",awaiting_publish:"草稿已生成，等待发布",pending:"待开始",running:"正在执行",succeeded:"已完成",failed:"失败，可重试",awaiting_confirmation:"等待人工确认",rejected:"用户已拒绝",inherited:"沿用前轮假设，未重新调用"};
    var busy = false;
    function refresh() {
      if (busy || document.hidden) { return; }
      busy = true;
      request("GET", "/api/projects/"+encodeURIComponent(pid)+"/agent-tasks", undefined, {admin:true})
        .then(function(data){
          var navStates = {};
          data.cards.forEach(function(card){
            var state=panel.querySelector('[data-agent-state="'+card.role+'"]');
            if(state){
              /* 记下服务端的原始状态键，供四阶段导航同步（不从中文反推） */
              state.setAttribute("data-agent-state-value", card.state);
              state.textContent=states[card.state] || card.state;
            }
            navStates[card.role]=card.state;
          });
          /* 导航与角色卡同一口径：优先用服务端给的阶段状态，没有才用角色原始状态 */
          if (window.__SY_SYNC__) { window.__SY_SYNC__(data.stage_states || navStates); }
        })
        .catch(function(){}).then(function(){busy=false;});
    }
    window.setInterval(refresh, 3000);
    panel.querySelectorAll("[data-agent-run]").forEach(function(button){
      button.addEventListener("click",function(){
        if(button.disabled){return;}
        button.disabled=true;
        setStatus(byTestId("agent-action-status"),"任务已提交，请等待实际角色状态更新…",null);
        request("POST","/api/projects/"+encodeURIComponent(pid)+"/agents/"+button.dataset.agentRun+"/run",{},{admin:true})
          .then(function(){window.location.reload();})
          .catch(function(error){setStatus(byTestId("agent-action-status"),messageFor(error),"error");button.disabled=false;refresh();});
      });
    });
  }

  function initRecruitmentKit() {
    document.querySelectorAll(".recruitment-kit").forEach(function (kit) {
      var community = kit.querySelector("[data-kit-community]");
      if (!community) { return; }
      var duration = kit.querySelector("[data-kit-duration]");
      var status = kit.querySelector("[data-kit-status]");
      var link = kit.querySelector('[data-testid="kit-public-link"]');
      function refresh() {
        var minutes = Number(duration.value);
        var valid = duration.value !== "" && Number.isInteger(minutes) && minutes >= 1 && minutes <= 120;
        var timeText = valid ? "预计" + minutes + "分钟（作者试填估计）" : "时长待试填确认";
        ["community", "private", "outline"].forEach(function (name) {
          var original = kit.querySelector('[data-kit-template="' + name + '"]');
          var target = kit.querySelector("[data-kit-" + name + "]");
          if (original && target) {
            target.value = original.value.split("{耗时说明}").join(timeText)
              .split("{实验链接}").join(link ? link.textContent : "{实验链接}");
          }
        });
      }
      duration.addEventListener("input", refresh);
      refresh();
      kit.querySelectorAll("[data-kit-copy]").forEach(function (button) {
        button.addEventListener("click", function () {
          var target = kit.querySelector("[data-kit-" + button.dataset.kitCopy + "]");
          refresh();
          function fallback() {
            target.focus(); target.select();
            try {
              if (!document.execCommand("copy")) { throw new Error("copy"); }
              setStatus(status, "已复制。请核对内容后手动发送；本网站不会替你招募。", "ok");
            } catch (error) { setStatus(status, "自动复制不可用，已选中文本，请长按或使用系统复制。", "error"); }
          }
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(target.value).then(function () {
              setStatus(status, "已复制。请核对内容后手动发送；本网站不会替你招募。", "ok");
            }).catch(fallback);
          } else { fallback(); }
        });
      });
    });
  }

  function boot() {
    var body = document.body;
    initAgentDashboard();
    initRecruitmentKit();
    localizeTimes(document);
    if (body.classList.contains("page-auth")) { initAuthPage(document); }
    if (body.classList.contains("page-admin") || body.classList.contains("page-landing")
        || body.classList.contains("page-profile")) { initLogout(); }
    if (body.classList.contains("page-admin")) { initEditPage(document); }
    if (body.classList.contains("page-survey")) { initSurveyPage(document); }
    // B 追加：项目管理三页
    if (body.classList.contains("page-projects")) { initProjectsPage(document); }
    if (body.classList.contains("page-projects")) { initProjectsView(); }
    if (body.classList.contains("page-project-new")) { initProjectNewPage(document); }
    if (body.classList.contains("page-workspace")) { initWorkspacePage(document); }
    if (body.classList.contains("page-rounds") || body.classList.contains("page-iteration")) { initIterationPage(); }
    if (body.classList.contains("page-invitations")) { initInvitationsPage(); }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  /* 仅本机自测用：页面里存在 window.__FO_SMOKE__ 时才暴露内部函数，生产页面不设置该变量。 */
  if (window.__FO_SMOKE__) {
    window.__FO_SMOKE_IMPL__ = {
      auth: initAuthPage,
      projectsView: initProjectsView,
      projects: initProjectsPage,
      setStatus: setStatus,
      request: request
    };
  }
}());
